"""Write a rebuilt Mesh_Z (bff extract format, e.g. from meshio.py import) back into a copy of a DPC.

The link header must be identical (with --allow-bounds: may differ only in its bounding sphere/box floats, link
+16..+96) and the decompressed body the same length (same counts); offsets never change.
- Body identical to the original: nothing is written (the DPC stays byte-identical).
- Raw resource: the body is overwritten in place.
- LZ4 resource: the body is recompressed with lz4hc.py (M17 session 2: the only compressor; about 5-7 % smaller than
  the game's own; checked by decompressing with lz4dec.py) and must fit its slot; slot padding 0xFF and the three size
  fields are updated, as in patch_hide.py / patch_bitmaps.py. lz4hc bodies passed in game (M10, M16).

Usage: python -I put_mesh.py <in.DPC> <out.DPC> <workdir> <mesh_id>=<file.Mesh_Z> [...] [--allow-bounds]
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dpc  # noqa: E402
from bmpdump import body  # noqa: E402
from patch_bitmaps import lz4_body  # noqa: E402
from patch_hide import find, slot_limits  # noqa: E402


BOUNDS = (16, 96)  # link-header bytes of the bounding sphere + box (20 floats, see meshio.write_bounds)


def put(buf, info, n, new_file, work, allow_bounds=False):
    d, e = find(info, {n})[n]
    lh = struct.unpack_from("<I", new_file, 0x2C)[0]
    link, new = new_file[64:64 + lh], new_file[64 + lh:]
    old_link = bytes(buf[d["link_header_pos"]:d["body_pos"]])
    if len(link) != len(old_link):
        raise ValueError(f"{n}: link header size differs")
    diff = [i for i in range(len(link)) if link[i] != old_link[i]]
    if diff and not (allow_bounds and all(BOUNDS[0] <= i < BOUNDS[1] for i in diff)):
        raise ValueError(f"{n}: link header differs at {diff[:8]}")
    if diff:
        buf[d["link_header_pos"]:d["body_pos"]] = link   # same size: only the bounds floats change
    old = body(buf, d, work)
    if len(new) != len(old):
        raise ValueError(f"{n}: body length {len(new)} != {len(old)}")
    if new == old:
        return "body unchanged" + (" (bounds written)" if diff else " (nothing written)")
    if d["compression_type"] == 0:
        buf[d["body_pos"]:d["end"]] = new
        return "raw overwrite"
    comp = lz4_body(new)   # M17 session 2: lz4hc (checked with lz4dec); bff-cli is no longer used
    lim = slot_limits(info)[d["pos"]]
    end = d["body_pos"] + len(comp)
    how = "lz4hc"
    if end > lim:
        raise ValueError(f"{n}: compressed body does not fit even with lz4hc ({end} > {lim}, "
                         f"{end - lim} bytes over): use fewer vertices/triangles")
    buf[d["body_pos"]:lim] = comp + b"\xff" * (lim - end)
    new_size = d["link_header_size"] + len(comp)
    struct.pack_into("<I", buf, d["pos"] + 24, new_size)               # DataResource.size
    struct.pack_into("<I", buf, d["pos"] + 40, len(comp))              # compressed_body_size
    struct.pack_into("<Q", buf, e["entry_pos"] + 20, 48 + new_size)    # entry.size
    return f"lz4 {d['body_len']} -> {len(comp)} bytes with {how} (slot ends at {lim}, body ends at {end})"


def main():
    src, dst, work = sys.argv[1:4]
    os.makedirs(work, exist_ok=True)
    buf = bytearray(open(src, "rb").read())
    info = dpc.parse(bytes(buf))
    allow = "--allow-bounds" in sys.argv
    for arg in sys.argv[4:]:
        if arg.startswith("--"):
            continue
        n, path = arg.split("=", 1)
        print(f"{n}: {put(buf, info, int(n), open(path, 'rb').read(), work, allow)}")
    open(dst, "wb").write(buf)
    print(f"wrote {dst}")


if __name__ == "__main__":
    main()
