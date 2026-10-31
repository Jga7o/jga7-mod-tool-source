"""Make a test copy of a DPC with chosen Bitmap_Z resources replaced by a solid color.

The replacement keeps the format, dimensions and mip count (all mips solid). Raw bitmaps are
overwritten byte for byte (same size, no field changes). LZ4 bitmaps are recompressed with
lz4hc.py (M17 session 2) and must fit their slot; the size fields are updated. Offsets never change.
Supported formats: BC1, BC3, RGBA8 (4 bytes/pixel). Cubemaps / unknown formats are refused.

Usage: python -I patch_bitmaps.py <in.DPC> <out.DPC> <workdir> --ids id1,id2,... [--color RRGGBB]
       python -I patch_bitmaps.py <in.DPC> <out.DPC> <workdir> --map colors.txt   (lines: "<id> RRGGBB")
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dpc  # noqa: E402
from bitmaps import decode  # noqa: E402
from bmpdump import bitmaps  # noqa: E402

S = dpc.SECTOR


def rgb565(r, g, b):
    return (r >> 3) << 11 | (g >> 2) << 5 | b >> 3


def solid_pixels(info, rgb):
    """Pixel data (all mips) of one solid color in the bitmap's own format."""
    r, g, b = rgb
    c = rgb565(r, g, b)
    bc1 = struct.pack("<HHI", c, c, 0)  # c0 == c1, all indices 0 -> color c0
    blocks = {"BC1": bc1, "BC3": bytes([255, 255, 0, 0, 0, 0, 0, 0]) + bc1}
    fmt, w, h = info["fmt_label"], info["w"], info["h"]
    out = bytearray()
    for _ in range(info["mips"]):
        if fmt in blocks:
            out += blocks[fmt] * (max(1, (w + 3) // 4) * max(1, (h + 3) // 4))
        elif fmt == "RGBA8?":
            out += bytes([r, g, b, 255]) * (w * h)
        else:
            raise ValueError(f"unsupported format {fmt}")
        w, h = max(1, w // 2), max(1, h // 2)
    return bytes(out)


def color_map(a):
    """{id: (r, g, b)} from --map file or --ids/--color."""
    if a.map:
        m = {}
        for line in open(a.map):
            if line.split() and not line.startswith("#"):
                i, c = line.split()[:2]
                m[int(i)] = tuple(bytes.fromhex(c))
        return m
    if not a.ids:
        sys.exit("need --ids or --map")
    return {int(x): tuple(bytes.fromhex(a.color)) for x in a.ids.split(",")}


def label_map(path):
    """{id: label} from the third column of a --map file."""
    return {int(line.split()[0]): line.split()[2] for line in open(path)
            if len(line.split()) > 2 and not line.startswith("#")}


def pixels_for(bi, rgb, label):
    """Solid color, or solid color with the label tiled across it (labeltex)."""
    if label:
        from labeltex import labelled_pixels
        return labelled_pixels(bi, rgb, label)
    return solid_pixels(bi, rgb)


def lz4_body(raw, work=None, tag=None):
    """The DPC LZ4 body of raw (M17 session 2: lz4hc.py, checked with lz4dec.py; bff-cli is no longer used)."""
    import lz4hc
    body = lz4hc.dpc_body(raw, 64)
    if not lz4hc.verify(raw, body):
        raise ValueError("lz4hc output does not decompress to the body")
    return body


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src"); ap.add_argument("dst"); ap.add_argument("work")
    ap.add_argument("--ids")
    ap.add_argument("--color", default="FF00FF")
    ap.add_argument("--map")
    ap.add_argument("--labels", action="store_true", help="draw the map's 3rd column as text on each texture")
    a = ap.parse_args()
    os.makedirs(a.work, exist_ok=True)
    colors = color_map(a)
    ids = set(colors)
    labels = label_map(a.map) if a.labels and a.map else {}
    buf = bytearray(open(a.src, "rb").read())
    info = dpc.parse(bytes(buf))
    h = info["header"]
    ents = [e for bd in info["blocks"] for e in bd["resources"]["resources"]]
    limit = {e["data"]["pos"]: n * S for e, n in
             zip(ents, [x["offset"] for x in ents[1:]] + [h["block_description_offset"]])}
    entry_of = {e["data"]["pos"]: e for e in ents}
    done = set()
    for d in bitmaps(info):
        name = dpc.s64(d["name"])
        if name not in ids:
            continue
        bi = decode(bytes(buf), d)
        if bi["kind"] != 0 or bi["faces"] != 1:
            sys.exit(f"{name}: cubemap, refused")
        pix = pixels_for(bi, colors[name], labels.get(name))
        assert len(pix) == bi["pixel_bytes"], (len(pix), bi["pixel_bytes"])
        raw = pix + b"\0\0\0\0"  # original bodies end with 4 zero bytes
        assert len(raw) == d["decompressed_body_size"]
        if d["compression_type"] == 0:
            buf[d["body_pos"]:d["end"]] = raw
            how = "raw overwrite"
        else:
            body = lz4_body(raw, a.work, str(name))
            end, lim = d["body_pos"] + len(body), limit[d["pos"]]
            if end > lim:
                sys.exit(f"{name}: recompressed body does not fit ({end} > {lim})")
            buf[d["body_pos"]:lim] = body + b"\xff" * (lim - end)
            new_size = d["link_header_size"] + len(body)
            struct.pack_into("<I", buf, d["pos"] + 24, new_size)                      # DataResource.size
            struct.pack_into("<I", buf, d["pos"] + 40, len(body))                     # compressed_body_size
            struct.pack_into("<Q", buf, entry_of[d["pos"]]["entry_pos"] + 20, 48 + new_size)  # entry.size
            how = f"lz4 {d['body_len']} -> {len(body)} bytes"
        done.add(name)
        print(f"{name}: {bi['w']}x{bi['h']} {bi['fmt_label']} mips={bi['mips']} -> solid #{bytes(colors[name]).hex().upper()}{' + label ' + labels[name] if name in labels else ''} ({how})")
    missing = ids - done
    if missing:
        sys.exit(f"not standalone Bitmap_Z ids: {sorted(missing)}")
    open(a.dst, "wb").write(buf)
    print(f"wrote {a.dst}")


if __name__ == "__main__":
    main()
