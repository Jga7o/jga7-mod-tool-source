"""Hex-dump link header and decompressed body start of Bitmap_Z resources (for format study).

Usage: python -I bmpdump.py <file.DPC> <class_dir> [n]
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dpc  # noqa: E402

BITMAP = -1628723265474973075


def body(buf, d, work):
    """Return the decompressed body of a DataResource."""
    raw = bytes(buf[d["body_pos"]:d["end"]])
    if d["compression_type"] == 0:
        return raw
    import lz4dec   # M17 session 2: our decoder instead of bff-cli unlz
    return lz4dec.dpc_body(raw)


def bitmaps(info):
    for bd in info["blocks"]:
        for e in bd["resources"]["resources"]:
            if dpc.s64(e["class_name"]) == BITMAP:
                yield e["data"]


def main():
    buf = open(sys.argv[1], "rb").read()
    work = os.environ.get("BMP_WORK", ".")
    n = int(sys.argv[3]) if len(sys.argv) > 3 else 6
    for d in list(bitmaps(dpc.parse(buf)))[:n]:
        lh = buf[d["link_header_pos"]:d["body_pos"]]
        b = body(buf, d, work)
        print(f"name={dpc.s64(d['name'])} ctype={d['compression_type']} dec={d['decompressed_body_size']}")
        print(f"  link({len(lh)}): {lh.hex(' ')}")
        print(f"  body[:48]: {b[:48].hex(' ')}")
        print(f"  body[-16:]: {b[-16:].hex(' ')}")


if __name__ == "__main__":
    main()
