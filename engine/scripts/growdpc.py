"""Enlarge standalone resource slots of a DPC (M13): insert whole sectors after a resource and move everything behind it.

Layout (dpc.py): header (4096 bytes) | standalone resources, each in its own sector-aligned slot (fill 0xFF) |
block descriptions (block_description_offset) | resource map (resources_map_offset; table of standalone entries) |
data groups (data_offset; fill 0x00) | EOF. pool_offset is 0 in every Amicia archive (refused otherwise).

Fields that change when k sectors are inserted after slot i (notes\\dpc_grow.md has the evidence):
  table entry offset (u32, entry +16)        + sectors inserted before that resource (every later resource)
  header.block_description_offset (u32, 264) + k_total
  block.resources_map_offset (u32)           + k_total
  map.data_offset (u32, map +4)              + k_total
  header.file_size (u64, 304)                + k_total * 2048
  the resized resource itself: DataResource.size/compressed_body_size (+24/+40), entry size (+20) and
  decompressed_size (+28); header.total_decompressed_size (u64, 312) + the change of (link + decompressed body).
Unchanged: block.data_resources_map_offset (relative: data_offset - map), header.padding_size and total_resources,
the map's working_buffer_offset / x1 / padded_size / padding_size and every data description (they depend on the data
groups only; identical groups have identical values at different file offsets in P_AMICIA and END).
A slot never shrinks: if the new resource fits the old slot, nothing moves (same as the in-place writers).
Low memory: the source is memory-mapped and the output written in order, in 1 MB pieces.

Usage: python -I growdpc.py <in.DPC> <out.DPC> --pad <id>=<sectors> [...]     (content unchanged, slot enlarged)
       python -I growdpc.py <in.DPC> <out.DPC> --put <id>=<resource.bin> [...] (a whole DataResource: 48-byte header +
                                                                              link header + body)
       python -I growdpc.py <in.DPC> <out.DPC> --mesh <id>=<file.Mesh_Z> [...] --work <dir>   (bff extract form; LZ4
                                                                              with lz4hc.py if the slot is LZ4)
"""
import argparse
import mmap
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fsx  # noqa: E402  (renames with a retry: Windows may hold a new file)
import dpc  # noqa: E402

S = dpc.SECTOR
FILL = b"\xff"          # slot padding in the originals (raw and LZ4 slots alike)
# Size limit for one standalone resource as stored. The largest in the five Amicia archives is 5,592,539 bytes (a raw
# texture, in all five). The map's x1 (10,551,296, the data-group working buffer) is not known to bound standalone
# resources, but nothing bigger than the largest original has been seen; a bigger one is refused.
LARGEST_SEEN = 5592539
CH = 1 << 20


def pad(n):
    return (n + S - 1) // S * S


def slots(info):
    """[(entry, data resource, slot start, slot end)] in file order (one block)."""
    h = info["header"]
    if len(info["blocks"]) != 1:
        raise ValueError("only single-block archives are supported")
    if h["pool_offset"]:
        raise ValueError("archive has a pool (pool_offset != 0): not supported")
    ents = info["blocks"][0]["resources"]["resources"]
    offs = [e["offset"] * S for e in ents]
    if offs != sorted(offs):
        raise ValueError("standalone resources are not in file order")
    ends = offs[1:] + [h["block_description_offset"] * S]
    return [(e, e["data"], s, t) for e, s, t in zip(ents, offs, ends)]


def plan(info, changes):
    """changes: {id: dict(resource=<bytes or None>, extra=<sectors>)} -> per-slot new sizes and the shift table."""
    out = []
    by_id = {dpc.s64(e["name"]): i for i, (e, *_) in enumerate(slots(info))}
    missing = set(changes) - set(by_id)
    if missing:
        raise ValueError(f"not standalone resources: {sorted(missing)}")
    shift = 0
    for i, (e, d, s, t) in enumerate(slots(info)):
        n = dpc.s64(e["name"])
        old_len = t - s
        if n in changes:
            c = changes[n]
            rb = c.get("resource")
            size = len(rb) if rb is not None else d["end"] - d["pos"]
            if size > LARGEST_SEEN:
                raise ValueError(f"{n}: too big for one resource: {size} bytes as stored (the limit is {LARGEST_SEEN}, the "
                                 f"largest standalone resource in the game's archives). Reduce the mesh")
            new_len = max(old_len, pad(size)) + c.get("extra", 0) * S
        else:
            new_len = old_len
        out.append(dict(i=i, id=n, entry=e, data=d, old=(s, t), new_start=s + shift, new_len=new_len))
        shift += new_len - old_len
    return out, shift // S


def header_fields(rb):
    """(size, link, dec, ctype, comp) of a whole DataResource."""
    _, _, _, size, link, dec, ctype, comp, zero = struct.unpack_from("<QQQIIIIII", rb, 0)
    return size, link, dec, ctype, comp


def write(src, dst, changes):
    """Write dst = src with the changed / enlarged slots. Returns a report dict."""
    with open(src, "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as b:
        info = dpc.parse(b)
        h = info["header"]
        bd = info["blocks"][0]
        res = bd["resources"]
        sl, k = plan(info, changes)
        first = sl[0]["old"][0]
        # header
        head = bytearray(b[:first])
        dec_delta = 0
        for p in sl:
            rb = changes.get(p["id"], {}).get("resource")
            if rb is not None:
                d = p["data"]
                size, link, dec, ctype, comp = header_fields(rb)
                if size != link + (comp or dec) or len(rb) != 48 + size or ctype not in (0, 2) or (ctype == 0) != (comp == 0):
                    raise ValueError(f"{p['id']}: inconsistent DataResource header")
                if rb[:24] != bytes(b[d["pos"]:d["pos"] + 24]):
                    raise ValueError(f"{p['id']}: class/name/link name differ from the slot's resource")
                dec_delta += (link + dec) - (d["link_header_size"] + d["decompressed_body_size"])
        struct.pack_into("<I", head, 264, h["block_description_offset"] + k)
        struct.pack_into("<Q", head, 304, h["file_size"] + k * S)
        struct.pack_into("<Q", head, 312, h["total_decompressed_size"] + dec_delta)
        tmp = dst + ".part"
        with open(tmp, "wb") as o:
            o.write(head)
            for p in sl:
                s, t = p["old"]
                rb = changes.get(p["id"], {}).get("resource")
                if rb is None:
                    for q in range(s, t, CH):
                        o.write(b[q:min(t, q + CH)])
                    o.write(FILL * (p["new_len"] - (t - s)))
                else:
                    o.write(rb)
                    o.write(FILL * (p["new_len"] - len(rb)))
            # block descriptions + resource map (+ its fill) up to data_offset, with the moved offsets
            ms, me = h["block_description_offset"] * S, res["data_offset"] * S
            mid = bytearray(b[ms:me])
            bpos = 4 + 24  # block 0: resources_map_offset after a, b, c
            struct.pack_into("<I", mid, bpos, bd["resources_map_offset"] + k)
            mpos = bd["resources_map_offset"] * S - ms
            struct.pack_into("<I", mid, mpos + 4, res["data_offset"] + k)
            for p in sl:
                e = p["entry"]
                ep = e["entry_pos"] - ms
                struct.pack_into("<I", mid, ep + 16, p["new_start"] // S)
                rb = changes.get(p["id"], {}).get("resource")
                if rb is not None:
                    size, link, dec, ctype, comp = header_fields(rb)
                    struct.pack_into("<QQ", mid, ep + 20, 48 + size, dec if ctype else 0)
            o.write(mid)
            for q in range(me, len(b), CH):
                o.write(b[q:min(len(b), q + CH)])
        grown = [dict(id=p["id"], old_sectors=(p["old"][1] - p["old"][0]) // S, new_sectors=p["new_len"] // S)
                 for p in sl if p["new_len"] != p["old"][1] - p["old"][0]]
    fsx.replace(tmp, dst)
    return dict(sectors_inserted=k, grown=grown, total_decompressed_delta=dec_delta)


def make_resource(dpc_path, n, new_file, work):
    """A whole DataResource for resource n from a file in bff extract form (64-byte header + link header + body): the
    slot's own class/name/link name, LZ4 (lz4hc.py, M17 session 2) if the slot's resource is LZ4, raw otherwise."""
    from patch_bitmaps import lz4_body
    with open(dpc_path, "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as b:
        info = dpc.parse(b)
        d = next(e["data"] for e in info["blocks"][0]["resources"]["resources"] if dpc.s64(e["name"]) == n)
        head = bytes(b[d["pos"]:d["pos"] + 24])
    lh = struct.unpack_from("<I", new_file, 0x2C)[0]
    link, body = new_file[64:64 + lh], new_file[64 + lh:]
    if d["compression_type"]:
        os.makedirs(work, exist_ok=True)
        comp = lz4_body(body, work, f"grow{n}")
        return head + struct.pack("<IIIIII", lh + len(comp), lh, len(body), 2, len(comp), 0) + link + comp
    return head + struct.pack("<IIIIII", lh + len(body), lh, len(body), 0, 0, 0) + link + body


def offset_map(orig_info, new_info):
    """Region pairs [(kind, id, orig start, orig end, new start, new end)] of two archives with the same resources
    in the same order (one may have enlarged slots). kind: header | slot | map | data."""
    a, b = slots(orig_info), slots(new_info)
    if [dpc.s64(x[0]["name"]) for x in a] != [dpc.s64(x[0]["name"]) for x in b]:
        raise ValueError("different resource lists")
    out = [("header", None, 0, a[0][2], 0, b[0][2])]
    out += [("slot", dpc.s64(x[0]["name"]), x[2], x[3], y[2], y[3]) for x, y in zip(a, b)]
    ra, rb = orig_info["blocks"][0]["resources"], new_info["blocks"][0]["resources"]
    ha, hb = orig_info["header"], new_info["header"]
    out.append(("map", None, ha["block_description_offset"] * S, ra["data_offset"] * S,
                hb["block_description_offset"] * S, rb["data_offset"] * S))
    out.append(("data", None, ra["data_offset"] * S, orig_info["file_len"], rb["data_offset"] * S, new_info["file_len"]))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src"); ap.add_argument("dst")
    ap.add_argument("--pad", action="append", default=[], help="<id>=<extra sectors> (content unchanged)")
    ap.add_argument("--put", action="append", default=[], help="<id>=<DataResource file>")
    ap.add_argument("--mesh", action="append", default=[], help="<id>=<file in bff extract form>")
    ap.add_argument("--work", default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "import_work", "grow"))
    a = ap.parse_args()
    changes = {}
    for x in a.mesh:
        n, p = x.split("=", 1)
        changes.setdefault(int(n), {})["resource"] = make_resource(a.src, int(n), open(p, "rb").read(), a.work)
    for x in a.pad:
        n, k = x.split("=")
        changes.setdefault(int(n), {})["extra"] = int(k)
    for x in a.put:
        n, p = x.split("=", 1)
        changes.setdefault(int(n), {})["resource"] = open(p, "rb").read()
    try:
        rep = write(a.src, a.dst, changes)
    except ValueError as e:
        sys.exit(f"ERROR: {e}")
    print(f"sectors inserted: {rep['sectors_inserted']}; grown slots: {rep['grown']}; "
          f"total_decompressed_size change: {rep['total_decompressed_delta']}")
    print("wrote", a.dst)


if __name__ == "__main__":
    main()
