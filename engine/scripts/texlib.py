"""Texture helpers for M14: read a Bitmap_Z (bff extract form) into RGBA, cached; sampling; image statistics.

Bitmap_Z link header (bitmaps.py): width/height at +10/+14, mip count - 1 at +42, format byte at +49 (0x1b BC1, 0x1d BC3).
The decoded mip 0 is cached as <cache>/<path hash>_<id>.rgba (raw RGBA8, w*h*4 bytes) so the slow pure-Python decode runs
once per file (M16: ids repeat across archives with other pixels, so the key includes the path).
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import texcodec as tc  # noqa: E402

import paths  # noqa: E402
import fsx  # noqa: E402  (renames with a retry: Windows may hold a new file)
OUT = paths.OUT
CACHE = os.path.join(OUT, "import_work", "m14", "cache")
FMT = {0x1b: "BC1", 0x1d: "BC3"}


def info(path):
    b = open(path, "rb").read()
    lh = struct.unpack_from("<I", b, 0x2C)[0]
    link = b[64:64 + lh]
    w, h = struct.unpack_from("<II", link, 10)
    return dict(w=w, h=h, mips=link[42] + 1, fmt=FMT.get(link[49], hex(link[49])), link=link, body=b[64 + lh:], lh=lh)


def load(res, bid, cache=CACHE):
    """(w, h, fmt, rgba bytearray of mip 0)."""
    p = os.path.join(res, f"{bid}.Bitmap_Z")
    i = info(p)
    os.makedirs(cache, exist_ok=True)
    import hashlib   # M16 fix: the key includes the file's folder (texture ids repeat across archives with other pixels)
    c = os.path.join(cache, f"{hashlib.sha1(os.path.abspath(p).lower().encode()).hexdigest()[:12]}_{bid}.rgba")
    if os.path.isfile(c) and os.path.getsize(c) == i["w"] * i["h"] * 4 and os.path.getmtime(c) >= os.path.getmtime(p):
        return i["w"], i["h"], i["fmt"], bytearray(open(c, "rb").read())
    n = max(1, (i["w"] + 3) // 4) * max(1, (i["h"] + 3) // 4) * tc.block_size(i["fmt"])
    img = tc.decode_bc(i["body"][:n], i["fmt"], i["w"], i["h"])
    open(c + ".tmp", "wb").write(img)
    fsx.replace(c + ".tmp", c)
    return i["w"], i["h"], i["fmt"], img


def texel(img, w, h, u, v, wrap=True):
    """Nearest texel at UV (u right, v down, as the game's UVs index the image rows top to bottom)."""
    if wrap:
        u, v = u % 1.0, v % 1.0
    x = min(w - 1, max(0, int(u * w)))
    y = min(h - 1, max(0, int(v * h)))
    o = 4 * (y * w + x)
    return tuple(img[o:o + 4])


def bilinear(img, w, h, u, v, wrap=True):
    """Bilinear RGBA (floats) at UV; texel centres at (i + 0.5) / w."""
    x, y = u * w - 0.5, v * h - 0.5
    x0, y0 = int(x // 1), int(y // 1)
    fx, fy = x - x0, y - y0
    out = [0.0, 0.0, 0.0, 0.0]
    for dy, wy in ((0, 1 - fy), (1, fy)):
        for dx, wx in ((0, 1 - fx), (1, fx)):
            xx, yy = x0 + dx, y0 + dy
            if wrap:
                xx, yy = xx % w, yy % h
            else:
                xx, yy = min(w - 1, max(0, xx)), min(h - 1, max(0, yy))
            o = 4 * (yy * w + xx)
            k = wx * wy
            for c in range(4):
                out[c] += k * img[o + c]
    return out


def channel_stats(img, mask=None):
    """Per channel: mean, min, max, and a 5-bin histogram; mask: optional bytearray (1 = use the texel)."""
    n = len(img) // 4
    out = []
    for c in range(4):
        vals = [img[4 * i + c] for i in range(n) if mask is None or mask[i]]
        if not vals:
            out.append(None)
            continue
        hist = [0] * 5
        for x in vals:
            hist[min(4, x * 5 // 256)] += 1
        out.append(dict(mean=sum(vals) / len(vals), min=min(vals), max=max(vals), hist=[round(100 * k / len(vals)) for k in hist]))
    return out
