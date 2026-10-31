"""Solid-color textures with a large text label tiled across them, for in-game identification.

render_mask(w, h, label) -> bytearray w*h (1 = text pixel). The label (3x5 font from bmppreview) is drawn once per
cell of a 3x3 grid, scaled so it fills about 80% of a cell's width. Mips with a side < 32 get no text.
labelled_pixels(info, rgb, label) -> BC1 / BC3 / RGBA8 bytes for all mips: background = rgb, text = black or white
(whichever contrasts more). Each 4x4 block holds at most these two colors, so BC1/BC3 store them exactly
(c0 = background, c1 = text, indices 0/1; BC3 alpha fixed at 255).
expected_rgba(w, h, rgb, label) -> what a decoder must return for that mip (colors after RGB565 rounding).
"""
import struct

from bmppreview import FONT, c565


def rgb565(r, g, b):
    return (r >> 3) << 11 | (g >> 2) << 5 | b >> 3


def text_color(rgb):
    r, g, b = rgb
    return (0, 0, 0) if 0.299 * r + 0.587 * g + 0.114 * b > 110 else (255, 255, 255)


def render_mask(w, h, label, grid=3):
    m = bytearray(w * h)
    if min(w, h) < 32:
        return m
    cols = 4 * len(label) - 1           # glyph 3 wide + 1 gap
    cw, ch = w // grid, h // grid
    s = max(1, min(int(cw * 0.8) // cols, int(ch * 0.8) // 5))
    tw, th = cols * s, 5 * s
    for gy in range(grid):
        for gx in range(grid):
            ox, oy = gx * cw + (cw - tw) // 2, gy * ch + (ch - th) // 2
            for k, chr_ in enumerate(label.upper()):
                g = FONT.get(chr_, FONT["?"])
                for i, bit in enumerate(g):
                    if bit != "1":
                        continue
                    x0, y0 = ox + (4 * k + i % 3) * s, oy + (i // 3) * s
                    for y in range(y0, y0 + s):
                        m[y * w + x0:y * w + x0 + s] = b"\1" * s
    return m


def labelled_pixels(info, rgb, label):
    fmt, w, h = info["fmt_label"], info["w"], info["h"]
    fg = text_color(rgb)
    cb, cf = rgb565(*rgb), rgb565(*fg)
    alpha = bytes([255, 255, 0, 0, 0, 0, 0, 0])
    out = bytearray()
    for _ in range(info["mips"]):
        m = render_mask(w, h, label)
        if fmt == "RGBA8?":
            for p in m:
                out += bytes((fg if p else rgb) + (255,))
        elif fmt in ("BC1", "BC3"):
            pre = alpha if fmt == "BC3" else b""
            bw, bh = max(1, (w + 3) // 4), max(1, (h + 3) // 4)
            solid = pre + struct.pack("<HHI", cb, cb, 0)
            for by in range(bh):
                rows = [m[(4 * by + r) * w:(4 * by + r + 1) * w] for r in range(4) if 4 * by + r < h]
                if not any(any(r) for r in rows):
                    out += solid * bw
                    continue
                for bx in range(bw):
                    idx = 0
                    for r, row in enumerate(rows):
                        for c in range(4):
                            x = 4 * bx + c
                            if x < w and row[x]:
                                idx |= 1 << (2 * (4 * r + c))
                    out += pre + (struct.pack("<HHI", cb, cf, idx) if idx else struct.pack("<HHI", cb, cb, 0))
        else:
            raise ValueError(f"unsupported format {fmt}")
        w, h = max(1, w // 2), max(1, h // 2)
    return bytes(out)


def expected_rgba(w, h, rgb, label):
    bg, fg = c565(rgb565(*rgb)), c565(rgb565(*text_color(rgb)))
    return [(fg if p else bg) for p in render_mask(w, h, label)]
