"""Pure-Python texture codec for the face kit (no numpy/PIL): PNG read/write, BC1/BC3 decode/encode, mip downsampling.

Images are flat bytearrays of RGBA (4 bytes per pixel, row major).
- png_read: 8- or 16-bit, color types 0 (gray), 2 (RGB), 3 (palette), 4 (gray+alpha), 6 (RGBA); not interlaced.
- png_write / png_bytes: RGB or RGBA, 8-bit.
- decode_bc / encode_bc: BC1 (8 bytes per 4x4 block; 4-color mode, or 3-color + transparent when a pixel's alpha < 128)
  and BC3 (8-byte alpha block, 8-value mode, + BC1 color block always in 4-color mode).
  Encoder: principal axis of the block's colors -> endpoints -> 2 rounds of least-squares endpoint refinement; keeps the
  best result by squared error.
- downsample: 2x2 box filter on the stored (sRGB) values. On B05 it matches the game's mip 1 as well as a linear-light
  filter (RMSE 2.30 vs 2.29 of 255), so the simpler one is used.
"""
import struct
import zlib

# ---------------------------------------------------------------- PNG


def png_read(path):
    """Returns (w, h, rgba bytearray, has_alpha)."""
    data = open(path, "rb").read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path}: not a PNG file")
    p, idat, plte, trns = 8, [], None, None
    while p < len(data):
        n = struct.unpack_from(">I", data, p)[0]
        t = data[p + 4:p + 8]
        c = data[p + 8:p + 8 + n]
        if t == b"IHDR":
            w, h, depth, ctype, _, _, inter = struct.unpack(">IIBBBBB", c)
        elif t == b"PLTE":
            plte = c
        elif t == b"tRNS":
            trns = c
        elif t == b"IDAT":
            idat.append(c)
        elif t == b"IEND":
            break
        p += 12 + n
    if inter:
        raise ValueError(f"{path}: interlaced PNG; save without interlacing")
    if depth not in (8, 16) and not (ctype == 3 and depth == 8):
        raise ValueError(f"{path}: bit depth {depth} not supported; save as 8-bit")
    chans = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]
    bpp = chans * depth // 8
    stride = w * bpp
    raw = zlib.decompress(b"".join(idat))
    img = bytearray(stride * h)
    prev = bytearray(stride)
    q = 0
    for y in range(h):
        f = raw[q]
        line = bytearray(raw[q + 1:q + 1 + stride])
        q += 1 + stride
        if f == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 255
        elif f == 2:
            line = bytearray((a + b) & 255 for a, b in zip(line, prev))
        elif f == 3:
            for i in range(stride):
                line[i] = (line[i] + ((line[i - bpp] if i >= bpp else 0) + prev[i]) // 2) & 255
        elif f == 4:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        elif f != 0:
            raise ValueError(f"{path}: bad PNG filter {f}")
        img[y * stride:(y + 1) * stride] = line
        prev = line
    if depth == 16:
        img = img[0::2]
    out = bytearray(w * h * 4)
    if ctype == 6:
        out[:] = img
    elif ctype == 2:
        out[0::4], out[1::4], out[2::4] = img[0::3], img[1::3], img[2::3]
        out[3::4] = b"\xff" * (w * h)
    elif ctype == 0:
        out[0::4] = out[1::4] = out[2::4] = img
        out[3::4] = b"\xff" * (w * h)
    elif ctype == 4:
        out[0::4] = out[1::4] = out[2::4] = img[0::2]
        out[3::4] = img[1::2]
    else:
        alpha = bytes(trns or b"") + b"\xff" * 256
        for i, k in enumerate(img):
            out[4 * i:4 * i + 4] = plte[3 * k:3 * k + 3] + alpha[k:k + 1]
    return w, h, out, any(a != 255 for a in out[3::4])


def png_bytes(w, h, rgba, alpha=False):
    ch = 4 if alpha else 3
    if alpha:
        px = bytes(rgba)
    else:
        px = bytearray(w * h * 3)
        px[0::3], px[1::3], px[2::3] = rgba[0::4], rgba[1::4], rgba[2::4]
    st = w * ch
    raw = b"".join(b"\0" + px[y * st:(y + 1) * st] for y in range(h))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6 if alpha else 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def png_write(path, w, h, rgba, alpha=False):
    open(path, "wb").write(png_bytes(w, h, rgba, alpha))


# ---------------------------------------------------------------- BC decode

def c565(c):
    r, g, b = c >> 11 & 31, c >> 5 & 63, c & 31
    return (r << 3 | r >> 2, g << 2 | g >> 4, b << 3 | b >> 2)


def bc1_palette(c0, c1, four):
    a, b = c565(c0), c565(c1)
    if c0 > c1 or four:
        return [a + (255,), b + (255,), tuple((2 * p + q) // 3 for p, q in zip(a, b)) + (255,),
                tuple((p + 2 * q) // 3 for p, q in zip(a, b)) + (255,)]
    return [a + (255,), b + (255,), tuple((p + q) // 2 for p, q in zip(a, b)) + (255,), (0, 0, 0, 0)]


def alpha_palette(a0, a1):
    if a0 > a1:
        return [a0, a1] + [((7 - i) * a0 + i * a1) // 7 for i in range(1, 7)]
    return [a0, a1] + [((5 - i) * a0 + i * a1) // 5 for i in range(1, 5)] + [0, 255]


def block_size(fmt):
    return 8 if fmt == "BC1" else 16


def decode_block(blk, fmt):
    """16 RGBA tuples of one block."""
    if fmt == "BC1":
        c0, c1, idx = struct.unpack_from("<HHI", blk)
        pal = bc1_palette(c0, c1, False)
        return [pal[idx >> (2 * i) & 3] for i in range(16)]
    c0, c1, idx = struct.unpack_from("<HHI", blk, 8)
    pal = bc1_palette(c0, c1, True)
    apal = alpha_palette(blk[0], blk[1])
    bits = int.from_bytes(blk[2:8], "little")
    return [pal[idx >> (2 * i) & 3][:3] + (apal[bits >> (3 * i) & 7],) for i in range(16)]


def decode_bc(data, fmt, w, h):
    """Flat RGBA bytearray of one mip (w, h = real size; blocks cover the padded size, cropped here)."""
    bs = block_size(fmt)
    bw, bh = max(1, (w + 3) // 4), max(1, (h + 3) // 4)
    out = bytearray(w * h * 4)
    k = 0
    for by in range(bh):
        for bx in range(bw):
            px = decode_block(data[k:k + bs], fmt)
            k += bs
            for i in range(16):
                x, y = bx * 4 + i % 4, by * 4 + i // 4
                if x < w and y < h:
                    o = 4 * (y * w + x)
                    out[o:o + 4] = bytes(px[i])
    return out


def block_pixels(img, w, h, bx, by):
    """16 RGBA tuples of block (bx, by); pixels outside the image repeat the edge."""
    out = []
    for i in range(16):
        x, y = min(w - 1, bx * 4 + i % 4), min(h - 1, by * 4 + i // 4)
        o = 4 * (y * w + x)
        out.append(tuple(img[o:o + 4]))
    return out


# ---------------------------------------------------------------- BC encode

def _q565(c):
    r, g, b = (max(0, min(255, int(round(x)))) for x in c)
    return (r * 31 + 127) // 255 << 11 | (g * 63 + 127) // 255 << 5 | (b * 31 + 127) // 255


def _axis_endpoints(cols):
    n = len(cols)
    m = [sum(c[k] for c in cols) / n for k in range(3)]
    cov = [[sum((c[i] - m[i]) * (c[j] - m[j]) for c in cols) for j in range(3)] for i in range(3)]
    v = [1.0, 1.0, 1.0]
    for _ in range(8):
        v = [sum(cov[i][j] * v[j] for j in range(3)) for i in range(3)]
        s = max(abs(x) for x in v) or 1.0
        v = [x / s for x in v]
    if max(abs(x) for x in v) < 1e-9:
        return m, m
    t = [sum((c[k] - m[k]) * v[k] for k in range(3)) for c in cols]
    vv = sum(x * x for x in v)
    lo, hi = min(t) / vv, max(t) / vv
    return [m[k] + v[k] * hi for k in range(3)], [m[k] + v[k] * lo for k in range(3)]


def _lsq(px, idx, weights):
    """Least-squares endpoints for fixed indices; weights[k] = (wa, wb) with color = wa*A + wb*B."""
    aa = ab = bb = 0.0
    ax = [0.0] * 3
    bx = [0.0] * 3
    for i, p in enumerate(px):
        wa, wb = weights[idx >> (2 * i) & 3]
        aa += wa * wa
        ab += wa * wb
        bb += wb * wb
        for k in range(3):
            ax[k] += wa * p[k]
            bx[k] += wb * p[k]
    det = aa * bb - ab * ab
    if abs(det) < 1e-9:
        return None
    A = [(bb * ax[k] - ab * bx[k]) / det for k in range(3)]
    B = [(aa * bx[k] - ab * ax[k]) / det for k in range(3)]
    return A, B


W4 = {0: (1, 0), 1: (0, 1), 2: (2 / 3, 1 / 3), 3: (1 / 3, 2 / 3)}
W3 = {0: (1, 0), 1: (0, 1), 2: (0.5, 0.5), 3: (0, 0)}


def encode_bc1_color(px, force_four=False):
    """8-byte BC1 block. px: 16 RGBA tuples. Transparent mode if any alpha < 128 (unless force_four, used by BC3)."""
    transparent = (not force_four) and any(p[3] < 128 for p in px)
    cols = [p[:3] for p in px if force_four or p[3] >= 128]
    if not cols:
        return struct.pack("<HHI", 0, 0xFFFF, 0xFFFFFFFF)  # c0 <= c1, all index 3 = transparent
    first = cols[0]
    if all(c == first for c in cols) and not transparent:
        c = _q565(first)
        return struct.pack("<HHI", c, c, 0)  # c0 = c1: every index 0 = that color (both modes)
    A, B = _axis_endpoints(cols)
    best = None
    for _ in range(3):
        a, b = _q565(A), _q565(B)
        if transparent:
            c0, c1 = min(a, b), max(a, b)
            pal = bc1_palette(c0, c1, False)
            weights = W3
        else:
            if a == b:
                c0, c1 = a, b
            else:
                c0, c1 = max(a, b), min(a, b)
            pal = bc1_palette(c0, c1, True if force_four else c0 > c1)
            weights = W4 if (force_four or c0 > c1) else W3
        err, idx = 0, 0
        for i, p in enumerate(px):
            if transparent and p[3] < 128:
                idx |= 3 << (2 * i)
                continue
            usable = [0, 1, 2] if (transparent or not (force_four or c0 > c1)) else [0, 1, 2, 3]
            bd, bk = None, 0
            for k in usable:
                q = pal[k]
                d = (p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 + (p[2] - q[2]) ** 2
                if bd is None or d < bd:
                    bd, bk = d, k
            err += bd
            idx |= bk << (2 * i)
        if best is None or err < best[0]:
            best = (err, struct.pack("<HHI", c0, c1, idx))
        if err == 0:
            break
        sub = [(p, i) for i, p in enumerate(px) if not (transparent and p[3] < 128)]
        r = _lsq([p for p, _ in sub], sum(((idx >> (2 * i) & 3) << (2 * j)) for j, (_, i) in enumerate(sub)), weights)
        if r is None:
            break
        A, B = r
    return best[1]


def encode_alpha(px):
    al = [p[3] for p in px]
    a0, a1 = max(al), min(al)
    if a0 == a1:
        return bytes([a0, a1]) + bytes(6)
    pal = alpha_palette(a0, a1)
    bits = 0
    for i, a in enumerate(al):
        k = min(range(8), key=lambda j: abs(pal[j] - a))
        bits |= k << (3 * i)
    return bytes([a0, a1]) + bits.to_bytes(6, "little")


def encode_block(px, fmt):
    if fmt == "BC1":
        return encode_bc1_color(px)
    return encode_alpha(px) + encode_bc1_color(px, force_four=True)


# ---------------------------------------------------------------- mips

def downsample(img, w, h):
    """2x2 box filter (sRGB values, rounded). Returns (img, w2, h2)."""
    w2, h2 = max(1, w // 2), max(1, h // 2)
    out = bytearray(w2 * h2 * 4)
    for y in range(h2):
        y0, y1 = min(h - 1, 2 * y), min(h - 1, 2 * y + 1)
        r0, r1 = 4 * y0 * w, 4 * y1 * w
        for x in range(w2):
            x0, x1 = min(w - 1, 2 * x), min(w - 1, 2 * x + 1)
            o = 4 * (y * w2 + x)
            for k in range(4):
                out[o + k] = (img[r0 + 4 * x0 + k] + img[r0 + 4 * x1 + k] + img[r1 + 4 * x0 + k] + img[r1 + 4 * x1 + k] + 2) >> 2
    return out, w2, h2
