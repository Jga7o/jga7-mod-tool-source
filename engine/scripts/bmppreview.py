"""Preview Bitmap_Z textures: decode one small mip to RGBA, write a labelled contact sheet and per-bitmap stats.

Pure Python (no PIL/numpy): BC1/BC3/RGBA8 decoder and a zlib PNG writer. For each bitmap the first mip whose
largest side is <= --max (default 128) is decoded; cubemaps show face 0 assuming face-major storage (unchecked).
Each tile is the colour (RGB) next to the alpha channel (grey). BC6H/BC7 tiles stay black.
Stats per bitmap: mean R, G, B, A; normal_score = 1 - mean distance of (R, G, B) from a unit normal, for
spotting tangent-space normal maps (bluish-purple: high B, R and G around 128).
Usage: python -I bmppreview.py <file.DPC> <out.png> <out.tsv> <workdir> [--labels labels.tsv] [--max 128] [--cols 4]
       python -I bmppreview.py <resources_dir> ... --unpacked   (bff-cli extract output: *.Bitmap_Z files, sorted by name)
Extracted file layout (observed): 64-byte BFF0 header (u32 link header size at 0x2C, u32 body size at 0x30),
link header, body (already decompressed).
       ... --unpacked --xple <archive.DPC>   for version_xple 1 archives (CHARACTERS_DATAS, CUSTOMIZATION): bff extracts
Bitmap_Z as stubs (link header, body size 0). The bodies sit elsewhere in the archive, each at a 2048-byte boundary:
u64 class id, u64 resource id, u64 unknown, u64 stored size, u32 decompressed size, u32 compression (0 raw, 2 LZ4),
u64 compressed size (0 if raw), then the body. LZ4 bodies use the same 8-byte prefix as in normal DPCs (observed).
labels.tsv: "<id>\t<label>" lines; default label = index in file order (B00, B01, ...).
"""
import argparse
import math
import os
import struct
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dpc  # noqa: E402
from bitmaps import FORMATS, decode  # noqa: E402
from bmpdump import bitmaps, body  # noqa: E402


def c565(c):
    r, g, b = c >> 11 & 31, c >> 5 & 63, c & 31
    return (r << 3 | r >> 2, g << 2 | g >> 4, b << 3 | b >> 2)


def bc1_block(blk, out, x0, y0, w, alpha_from_bc1=True):
    c0, c1, idx = struct.unpack_from("<HHI", blk)
    a, b = c565(c0), c565(c1)
    if c0 > c1 or not alpha_from_bc1:
        pal = [a + (255,), b + (255,), tuple((2 * p + q) // 3 for p, q in zip(a, b)) + (255,),
               tuple((p + 2 * q) // 3 for p, q in zip(a, b)) + (255,)]
    else:
        pal = [a + (255,), b + (255,), tuple((p + q) // 2 for p, q in zip(a, b)) + (255,), (0, 0, 0, 0)]
    for i in range(16):
        out[(y0 + i // 4) * w + x0 + i % 4] = pal[idx >> (2 * i) & 3]


def bc3_alpha(blk):
    a0, a1 = blk[0], blk[1]
    bits = int.from_bytes(blk[2:8], "little")
    if a0 > a1:
        pal = [a0, a1] + [((7 - i) * a0 + i * a1) // 7 for i in range(1, 7)]
    else:
        pal = [a0, a1] + [((5 - i) * a0 + i * a1) // 5 for i in range(1, 5)] + [0, 255]
    return [pal[bits >> (3 * i) & 7] for i in range(16)]


def decode_mip(data, fmt, w, h):
    """RGBA tuples, row major. w and h are padded up to multiples of 4 for block formats."""
    label = FORMATS.get(fmt, ("?",))[0]
    if label == "RGBA8?":
        return [tuple(data[i:i + 4]) for i in range(0, w * h * 4, 4)], w, h
    pw, ph = max(4, (w + 3) // 4 * 4), max(4, (h + 3) // 4 * 4)
    out = [(0, 0, 0, 255)] * (pw * ph)
    if label not in ("BC1", "BC3"):
        return out, pw, ph
    bs = 8 if label == "BC1" else 16
    k = 0
    for by in range(0, ph, 4):
        for bx in range(0, pw, 4):
            blk = data[k:k + bs]
            k += bs
            if label == "BC1":
                bc1_block(blk, out, bx, by, pw)
            else:
                bc1_block(blk[8:], out, bx, by, pw, alpha_from_bc1=False)
                al = bc3_alpha(blk)
                for i in range(16):
                    p = (by + i // 4) * pw + bx + i % 4
                    out[p] = out[p][:3] + (al[i],)
    return out, pw, ph


def mip_size(w, h, fmt):
    label, blk, bpp = FORMATS.get(fmt, ("?", None, None))
    return max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * blk if blk else w * h * bpp


def pick_mip(bi, limit):
    """Offset, width, height of the first mip with max side <= limit (face 0)."""
    w, h, off = bi["w"], bi["h"], 0
    for _ in range(bi["mips"]):
        if max(w, h) <= limit:
            return off, w, h
        off += mip_size(w, h, bi["fmt"])
        w, h = max(1, w // 2), max(1, h // 2)
    return off - mip_size(w * 2, h * 2, bi["fmt"]), w * 2, h * 2


# 3x5 font for labels
FONT = {"0": "111101101101111", "1": "010110010010111", "2": "111001111100111", "3": "111001111001111",
        "4": "101101111001001", "5": "111100111001111", "6": "111100111101111", "7": "111001001001001",
        "8": "111101111101111", "9": "111101111001111", "B": "110101110101110", "C": "111100100100111",
        "U": "101101101101111", "-": "000000111000000", " ": "000000000000000", "M": "101111111101101",
        "A": "010101111101101", "X": "101101010101101", "?": "111001011000010",
        "E": "111100111100111", "N": "110101101101101", "D": "110101101101110", "I": "111010010010111",
        "H": "101101111101101", "P": "111101111100100"}


def draw_text(img, W, x, y, text, scale=2):
    for ch in text.upper():
        g = FONT.get(ch, FONT["?"])
        for i, bit in enumerate(g):
            if bit == "1":
                for dy in range(scale):
                    for dx in range(scale):
                        px, py = x + (i % 3) * scale + dx, y + (i // 3) * scale + dy
                        img[py * W + px] = (255, 255, 0)
        x += 4 * scale


def write_png(path, W, H, rows):
    raw = b"".join(b"\0" + bytes(c for p in rows[y * W:(y + 1) * W] for c in p) for y in range(H))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def from_dpc(path):
    buf = open(path, "rb").read()
    return [(buf, d) for d in bitmaps(dpc.parse(buf))]


def from_unpacked(folder):
    """(buf, d) pairs shaped like dpc.parse output, so decode() and body() work unchanged."""
    out = []
    for f in sorted(x for x in os.listdir(folder) if x.endswith(".Bitmap_Z")):
        buf = open(os.path.join(folder, f), "rb").read()
        assert buf[:4] == b"BFF0", f
        lh, bsz = struct.unpack_from("<II", buf, 0x2C)
        assert 64 + lh + bsz == len(buf), (f, lh, bsz, len(buf))
        name = struct.unpack_from("<Q", buf, 64)[0]
        assert dpc.s64(name) == int(f.split(".")[0]), f
        out.append((buf, dict(name=name, link_header_pos=64, body_pos=64 + lh, end=len(buf), compression_type=0,
                              decompressed_body_size=bsz, body_len=bsz, pos=0)))
    return out


def xple_index(path, class_id):
    """{resource id: (archive bytes, body_pos, stored, decompressed, compression)} for an xple archive's body records."""
    buf = open(path, "rb").read()
    key, idx, odd = struct.pack("<Q", class_id), {}, []
    pos = 0
    while (pos := buf.find(key, pos)) >= 0:
        if pos % 2048 == 0:
            name, _, stored, dec, ctype, comp = struct.unpack_from("<qQQIIQ", buf, pos + 8)
            if (ctype == 0 and comp == 0 and dec == stored) or (ctype == 2 and comp == stored):
                idx[name] = (buf, pos + 48, stored, dec, ctype)
            else:
                odd.append((name, pos, stored, dec, ctype, comp))
        pos += 1
    return idx, odd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dpc"); ap.add_argument("png"); ap.add_argument("tsv"); ap.add_argument("work")
    ap.add_argument("--unpacked", action="store_true"); ap.add_argument("--xple"); ap.add_argument("--labels"); ap.add_argument("--max", type=int, default=128); ap.add_argument("--cols", type=int, default=4)
    a = ap.parse_args()
    os.makedirs(a.work, exist_ok=True)
    labels = {}
    if a.labels:
        for line in open(a.labels):
            if line.strip():
                i, lab = line.rstrip("\n").split("\t")[:2]
                labels[int(i)] = lab
    T = a.max
    tiles, stats = [], []
    items = from_unpacked(a.dpc) if a.unpacked else from_dpc(a.dpc)
    if a.xple:
        cls = struct.unpack_from("<Q", items[0][0], 0x10)[0]
        idx, odd = xple_index(a.xple, cls)
        print(f"xple: {len(idx)} body records, {len(odd)} odd: {odd[:5]}")
        for k, (buf, d) in enumerate(items):
            r = idx.get(dpc.s64(d["name"]))
            if d["body_len"] == 0 and r:
                big, pos, stored, dec, ctype = r
                buf = buf[:d["body_pos"]] + big[pos:pos + stored]
                d = dict(d, end=len(buf), decompressed_body_size=dec, body_len=stored, compression_type=ctype)
                items[k] = (buf, d)
    for n, (buf, d) in enumerate(items):
        bi = decode(buf, d)
        lab = labels.get(bi["name"], f"B{n:02d}")
        off, w, h = pick_mip(bi, T)
        data = body(buf, d, a.work)[off:]
        if len(data) < mip_size(w, h, bi["fmt"]):  # stub: link header only, no pixel data in this archive
            stats.append((lab + " (no pixels)", bi["name"], f"{bi['w']}x{bi['h']}", bi["fmt_label"], bi["faces"],
                          "-", "", "", "", "", ""))
            tiles.append((lab + "-X", [(40, 40, 40, 40)] * 16, 4, 4))
            continue
        px, pw, ph = decode_mip(data, bi["fmt"], w, h)
        px = [px[y * pw + x] for y in range(h) for x in range(w)]
        m = [sum(p[c] for p in px) / len(px) for c in range(4)]
        dist = 0.0
        for p in px:
            v = [(p[c] / 255) * 2 - 1 for c in range(3)]
            dist += abs(math.sqrt(sum(x * x for x in v)) - 1) + (0 if v[2] > 0 else 1)
        stats.append((lab, bi["name"], f"{bi['w']}x{bi['h']}", bi["fmt_label"], bi["faces"], f"{w}x{h}",
                      *[round(x) for x in m], round(1 - dist / len(px), 3)))
        tiles.append((lab, px, w, h))
    cols = a.cols
    TW, TH = 2 * T + 8, T + 16
    W, H = cols * TW, ((len(tiles) + cols - 1) // cols) * TH
    img = [(40, 40, 40)] * (W * H)
    for k, (lab, px, w, h) in enumerate(tiles):
        ox, oy = (k % cols) * TW, (k // cols) * TH
        draw_text(img, W, ox + 2, oy + 2, lab)
        sx, sy = w / T if w > T else 1, h / T if h > T else 1
        for y in range(min(h, T)):
            for x in range(min(w, T)):
                p = px[int(y * sy) * w + int(x * sx)]
                img[(oy + 14 + y) * W + ox + x] = p[:3]
                img[(oy + 14 + y) * W + ox + T + 4 + x] = (p[3],) * 3
    write_png(a.png, W, H, img)
    with open(a.tsv, "w") as f:
        f.write("label\tid\tsize\tformat\tfaces\tpreview\tmeanR\tmeanG\tmeanB\tmeanA\tnormal_score\n")
        for s in stats:
            f.write("\t".join(map(str, s)) + "\n")
    print(f"{len(tiles)} bitmaps -> {a.png}, {a.tsv}")


if __name__ == "__main__":
    main()
