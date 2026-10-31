"""Make a test copy of a DPC with chosen Mesh_Z resources hidden: every index of their index buffers set to 0.

All triangles become degenerate (0,0,0), so nothing is drawn. Counts, submesh entries, vertices and the decompressed
size stay the same; only the index data changes. Offsets never change.
- Raw Mesh_Z: the index bytes are overwritten in place (nothing else changes).
- LZ4 Mesh_Z: the body is decompressed, the indices zeroed, the body recompressed with lz4hc.py (M17 session 2; same convention as
  patch_bitmaps.py). It must fit its slot; the rest of the slot is padded with 0xFF and the three size fields are
  updated (DataResource.size, compressed_body_size, table entry size), as in the LZ4 bitmap tests.
Layout of the body: see meshfmt.py and notes\\mesh_format.md.

Usage: python -I patch_hide.py <in.DPC> <out.DPC> <workdir> --ids id1,id2,...
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dpc  # noqa: E402
from bmpdump import body  # noqa: E402
from meshfmt import parse, check  # noqa: E402
from patch_bitmaps import lz4_body  # noqa: E402

S = dpc.SECTOR


def find(info, ids):
    """{id: (data resource, table entry)} for standalone resources."""
    out = {}
    for bd in info["blocks"]:
        for e in bd["resources"]["resources"]:
            n = dpc.s64(e["data"]["name"])
            if n in ids:
                out[n] = (e["data"], e)
    return out


def slot_limits(info):
    """{resource pos: slot end} = next resource start (or the block description)."""
    h = info["header"]
    ents = [e for bd in info["blocks"] for e in bd["resources"]["resources"]]
    return {e["data"]["pos"]: n * S for e, n in
            zip(ents, [x["offset"] for x in ents[1:]] + [h["block_description_offset"]])}


def extract_view(link, dec_body):
    """The resource as bff-cli extracts it: fake 64-byte header (link size at 0x2C) + link header + body."""
    head = bytearray(64)
    struct.pack_into("<I", head, 0x2C, len(link))
    return bytes(head) + link + dec_body


def ib_ranges(view):
    """Index data ranges [(start, end)] in extract-view offsets; raises if the layout check fails."""
    m = parse(view)
    msgs = check(view, m)
    if msgs:
        raise ValueError("; ".join(msgs))
    return [(ib["pos"], ib["pos"] + ib["count"] * ib["size"]) for ib in m["ibs"]], m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src"); ap.add_argument("dst"); ap.add_argument("work")
    ap.add_argument("--ids", required=True)
    a = ap.parse_args()
    os.makedirs(a.work, exist_ok=True)
    ids = {int(x) for x in a.ids.split(",")}
    buf = bytearray(open(a.src, "rb").read())
    info = dpc.parse(bytes(buf))
    found = find(info, ids)
    if set(found) != ids:
        sys.exit(f"not standalone resources: {sorted(ids - set(found))}")
    limit = slot_limits(info)
    for n, (d, e) in sorted(found.items()):
        link = bytes(buf[d["link_header_pos"]:d["body_pos"]])
        dec = body(buf, d, a.work)
        view = bytearray(extract_view(link, dec))
        ranges, m = ib_ranges(bytes(view))
        for s, t in ranges:
            view[s:t] = bytes(t - s)
        new = bytes(view[64 + len(link):])
        assert len(new) == len(dec) == d["decompressed_body_size"]
        nidx = sum(i["count"] for i in m["ibs"])
        if d["compression_type"] == 0:
            shift = d["body_pos"] - (64 + len(link))
            for s, t in ranges:
                buf[s + shift:t + shift] = bytes(t - s)
            how = f"raw, file ranges {[(hex(s + shift), hex(t + shift)) for s, t in ranges]}"
        else:
            comp = lz4_body(new, a.work, str(n))
            end, lim = d["body_pos"] + len(comp), limit[d["pos"]]
            if end > lim:
                sys.exit(f"{n}: recompressed body does not fit ({end} > {lim})")
            buf[d["body_pos"]:lim] = comp + b"\xff" * (lim - end)
            new_size = d["link_header_size"] + len(comp)
            struct.pack_into("<I", buf, d["pos"] + 24, new_size)               # DataResource.size
            struct.pack_into("<I", buf, d["pos"] + 40, len(comp))              # compressed_body_size
            struct.pack_into("<Q", buf, e["entry_pos"] + 20, 48 + new_size)    # entry.size
            how = f"lz4 {d['body_len']} -> {len(comp)} bytes"
        print(f"{n}: {m['vbs'][0]['count']} vertices, {nidx} indices zeroed ({how})")
    open(a.dst, "wb").write(buf)
    print(f"wrote {a.dst}")


if __name__ == "__main__":
    main()
