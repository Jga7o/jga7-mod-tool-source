"""Decode Bitmap_Z link headers and validate the pixel-data size against a mip-chain formula.

Bitmap_Z link header (55 bytes), as observed:
  0  u64 own name        10 u32 width        14 u32 height       18 u32 (1)   22 u32 (1)
  26 u32 pixel bytes     30 u8  flags_a      32 u8  flags_b      42 u8 mip_count - 1
  44 u8  flags_c         48 u8  flags_d      49 u8  format       50 u8 flags_e
Body = pixel bytes (mips largest first) + 4 zero bytes.
Usage: python -I bitmaps.py <file.DPC> [--tsv out.tsv]
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dpc  # noqa: E402
from bmpdump import bitmaps  # noqa: E402

# format byte -> (label, bytes per 4x4 block or None, bytes per pixel or None)
FORMATS = {0x1b: ("BC1", 8, None), 0x1d: ("BC3", 16, None), 0x06: ("RGBA8?", None, 4),
           0x21: ("BC6H/BC7?", 16, None), 0x20: ("16B-block?", 16, None),
           0x23: ("16B-block?23", 16, None)}  # 0x20: P_AMICIA_UNIVERSITY; 0x23: CHAPTERxx loading screens


def chain_size(w, h, mips, fmt, faces=1):
    label, blk, bpp = FORMATS.get(fmt, ("?", None, None))
    if not blk and not bpp:
        return None
    total = 0
    for _ in range(mips):
        if blk:
            total += max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * blk
        else:
            total += w * h * bpp
        w, h = max(1, w // 2), max(1, h // 2)
    return total * faces


def decode(buf, d):
    lh = buf[d["link_header_pos"]:d["body_pos"]]
    w, h = struct.unpack_from("<II", lh, 10)
    pix = struct.unpack_from("<I", lh, 26)[0]
    b = dict(name=dpc.s64(d["name"]), self_name=dpc.s64(struct.unpack_from("<Q", lh, 0)[0]),
             w=w, h=h, f18=struct.unpack_from("<I", lh, 18)[0], f22=struct.unpack_from("<I", lh, 22)[0],
             pixel_bytes=pix, mips=lh[42] + 1, fmt=lh[49], fa=lh[30], fb=lh[32], fc=lh[44], fd=lh[48],
             fe=lh[50], ctype=d["compression_type"], dec=d["decompressed_body_size"],
             stored=d["body_len"], pos=d["pos"], link=lh)
    b["fmt_label"] = FORMATS.get(b["fmt"], ("?",))[0]
    b["kind"] = struct.unpack_from("<H", lh, 8)[0]  # 0 = 2D, 2 = cubemap
    b["faces"] = b["f22"]
    b["expected"] = chain_size(w, h, b["mips"], b["fmt"], b["faces"])
    b["size_ok"] = b["expected"] == pix and d["decompressed_body_size"] == pix + 4
    return b


def main():
    buf = open(sys.argv[1], "rb").read()
    rows = [decode(buf, d) for d in bitmaps(dpc.parse(buf))]
    hdr = "name\tw\th\tmips\tfaces\tformat\tfmt_byte\tpixel_bytes\tstored\tlz4\tfa\tfb\tfc\tfd\tfe\tsize_ok"
    lines = [hdr] + [
        f"{r['name']}\t{r['w']}\t{r['h']}\t{r['mips']}\t{r['faces']}\t{r['fmt_label']}\t{r['fmt']:#04x}\t{r['pixel_bytes']}\t"
        f"{r['stored']}\t{'yes' if r['ctype'] else 'no'}\t{r['fa']:#x}\t{r['fb']:#x}\t{r['fc']:#x}\t{r['fd']:#x}\t"
        f"{r['fe']:#x}\t{r['size_ok']}" for r in rows]
    print("\n".join(lines))
    bad = [r for r in rows if not r["size_ok"] or r["self_name"] != r["name"] or r["f18"] != 1
           or (r["kind"], r["faces"]) not in ((0, 1), (2, 6))]
    print(f"# bitmaps={len(rows)} size formula OK={sum(r['size_ok'] for r in rows)} anomalies={len(bad)}")
    for r in bad:
        print(f"# ANOMALY {r['name']}: {r['link'].hex(' ')} expected={r['expected']} dec={r['dec']}")
    if "--tsv" in sys.argv:
        open(sys.argv[sys.argv.index("--tsv") + 1], "w").write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
