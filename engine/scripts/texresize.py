"""Write a texture of a NEW size into a DPC copy (session 56: the 4096 route). texpatch.py keeps the size; this tool
changes it.

Bitmap_Z link header (55 bytes; output\\analysis\\fullrun3\\tex4k_survey.txt, every size and format in the game files):
  +10 u32 width, +14 u32 height, +26 u32 data size (= body length - 4), +42 u8 mip count - 1, +49 format (0x1b BC1,
  0x1d BC3). The other bytes do not depend on the size (they differ between textures of the same size, or are flags).
Body: every mip from the largest down to 1 x 1 (BC1 8 / BC3 16 bytes per 4 x 4 block, at least one block), then 4 zero
bytes (the game's layout).
The PNG (any power-of-two size, RGBA) is encoded in the texture's own format, all mips (2 x 2 box, texcodec); the new
resource is written by growdpc.py (slot enlarged; the size ceiling LARGEST_SEEN is lifted only with --over-largest).
--raw stores the body uncompressed even if the slot was LZ4 (a 4096 texture should not need the archive's working buffer:
guess). Checks after writing: none here; run layoutcheck / verify_face (--bitmap-resized) on the result.
Usage: python -I texresize.py <in.DPC> <out.DPC> --res <unpacked resources> --id <bitmap id> --png <image.png>
       --work <dir> [--over-largest] [--raw]
"""
import argparse
import os
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import growdpc  # noqa: E402
import texcodec as tc  # noqa: E402
import texlib  # noqa: E402


def encode_chain(img, w, h, fmt):
    """All mips of img (RGBA) in fmt, largest first, + 4 zero bytes; returns (body, mip count)."""
    out = bytearray()
    n = 0
    while True:
        for by in range(0, max(1, (h + 3) // 4)):
            for bx in range(0, max(1, (w + 3) // 4)):
                out += tc.encode_block(tc.block_pixels(img, w, h, bx, by), fmt)
        n += 1
        if w == 1 and h == 1:
            break
        img, w, h = tc.downsample(img, w, h)
    return bytes(out) + b"\0" * 4, n


def link_for(link, w, h, mips, body_len):
    l = bytearray(link)
    struct.pack_into("<I", l, 10, w)
    struct.pack_into("<I", l, 14, h)
    struct.pack_into("<I", l, 26, body_len - 4)
    l[42] = mips - 1
    return bytes(l)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src"); ap.add_argument("dst")
    ap.add_argument("--res", required=True); ap.add_argument("--id", type=int, required=True)
    ap.add_argument("--png", required=True); ap.add_argument("--work", required=True)
    ap.add_argument("--over-largest", action="store_true")
    ap.add_argument("--raw", action="store_true")
    a = ap.parse_args()
    t0 = time.time()
    i = texlib.info(os.path.join(a.res, f"{a.id}.Bitmap_Z"))
    if i["fmt"] not in ("BC1", "BC3"):
        sys.exit(f"ERROR: format {i['fmt']} not supported")
    w, h, img, _ = tc.png_read(a.png)
    if w & (w - 1) or h & (h - 1):
        sys.exit("ERROR: the PNG must have power-of-two sides")
    body, mips = encode_chain(img, w, h, i["fmt"])
    link = link_for(i["link"], w, h, mips, len(body))
    f = bytearray(64) + link + body
    struct.pack_into("<I", f, 0x2C, len(link))
    os.makedirs(a.work, exist_ok=True)
    if a.over_largest:
        growdpc.LARGEST_SEEN = 1 << 31
    res = raw_resource(a.src, a.id, bytes(f)) if a.raw else growdpc.make_resource(a.src, a.id, bytes(f), a.work)
    g = growdpc.write(a.src, a.dst, {a.id: dict(resource=res)})
    print(f"bitmap {a.id}: {i['w']}x{i['h']} {i['fmt']} {i['mips']} mips -> {w}x{h} {mips} mips; body {len(body)} bytes, "
          f"resource {len(res)} bytes stored ({'raw' if a.raw else 'as the slot'}); slot +{g['sectors_inserted']} sectors; "
          f"{time.time() - t0:.0f} s")


def raw_resource(src, n, new_file):
    """The DataResource stored uncompressed (compression type 0, as the game's raw slots): the slot's own 24-byte head
    (class, name, link name), then size = link + body, link size, body size, type 0, 0, 0, link header, body. The table
    entry has no compression flag (dpc.py); growdpc.write updates its sizes."""
    import mmap
    import dpc
    with open(src, "rb") as fh, mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as b:
        d = next(e["data"] for e in dpc.parse(b)["blocks"][0]["resources"]["resources"] if dpc.s64(e["name"]) == n)
        head = bytes(b[d["pos"]:d["pos"] + 24])
    lh = struct.unpack_from("<I", new_file, 0x2C)[0]
    link, body = new_file[64:64 + lh], new_file[64 + lh:]
    return head + struct.pack("<IIIIII", lh + len(body), lh, len(body), 0, 0, 0) + link + body


if __name__ == "__main__":
    main()
