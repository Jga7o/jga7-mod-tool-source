"""M15b route B: the user's own hair texture (colour PNG + grey alpha PNG, baked for the hair glb's own UVs) as BC3
bodies for the hair's colour textures, and a flat normal for its normal textures.

prepare: colour RGB + alpha mask grey (R channel) -> one straight-alpha RGBA image (8 bit, never premultiplied).
  - Edge bleed: the colour of every pixel with alpha < BLEED_T is replaced by its known neighbours' mean, growing BLEED_PX
    pixels out from the drawn strands (8-neighbourhood, one ring per pass); what is still unknown after that gets the
    alpha-weighted mean colour of the strands. So BC3 blocks and mips never mix in black (or white) under transparent areas.
  - Options, off by default: --alpha-linearize (undo an sRGB curve on the mask: a = 255 * lin(a / 255)) and
    --alpha-sharpen (contrast x2 around 128: a = clamp(128 + 2 (a - 128)); the soft edge ramp keeps half its width).
  - Mips (12 for 2048²): 2x2 boxes, alpha = mean, colour = alpha-weighted mean (premultiplied-safe: transparent texels
    do not darken or brighten the strands' colour; an all-transparent box takes the plain mean).
  - Every block of every mip encoded BC3 (texcodec); body = mips + 4 zero bytes (the game's layout).
  - Flat normal: (R 128, G 128, B 247, A 128), flat in both readings of B02 (host layout: X = A, Y = G, B 247 = the game
    hair's own dominant B value; XYZ in RGB: x = y = 0, z high).
  Outputs in --out: hair_rgba.png (mip 0 as written), hair_colour.bc3, normal_flat.png, normal_flat.bc3, prepare.json.
put: write a prepared body into a bitmap of a normal (version 1) DPC: same size, format, mips required; raw slots in
  place, LZ4 slots recompressed (slot enlarged when needed, growdpc.py).
xple: write prepared bodies into raw bitmap slots of an xple archive (CUSTOMIZATION) in place; nothing else changes.
verify-xple: independent check of an xple result against the original (bytes outside the written bodies, records, the
  bodies against the prepared files, mip 0 decoded against hair_rgba.png).
  M17 (D3): --normal <png> (a standard OpenGL normal map, any size) is written instead of the flat normal, in the same
  layout as hosttex.py's body normals (A = X, G = Y down; R 128, B 247), mips by plain 2x2 boxes: normal_own.png / .bc3.
Usage: python -I hairtex.py prepare --colour <png> --alpha <png> --out <dir> [--alpha-linearize] [--alpha-sharpen] [--glb <hair.glb>]
       [--normal <png>]
       python -I hairtex.py put <in.DPC> <out.DPC> --res <resources> --id <bitmap> --body <file.bc3> --work <dir>
       python -I hairtex.py xple <in.DPC> <out.DPC> --stubs <resources> --write <id>=<file.bc3> ...
       python -I hairtex.py verify-xple <orig.DPC> <new.DPC> --stubs <resources> --write <id>=<file.bc3> ... --png <hair_rgba.png>
"""
import argparse
import hashlib
import json
import math
import os
import random
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fsx  # noqa: E402  (renames with a retry: Windows may hold a new file)
import texcodec as tc  # noqa: E402

BLEED_T = 8
BLEED_PX = 16
FLAT = (128, 128, 247, 128)
SIZE = 2048


def lin(v):
    v /= 255
    return 255 * (v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4)


def combine(cpath, apath, linearize, sharpen):
    wc, hc, col, _ = tc.png_read(cpath)
    wa, ha, alp, _ = tc.png_read(apath)
    if (wc, hc) != (wa, ha):
        raise ValueError(f"sizes differ: colour {wc}x{hc}, alpha {wa}x{ha}")
    if (wc, hc) != (SIZE, SIZE):
        raise ValueError(f"the hair textures are {SIZE}x{SIZE}; the PNGs are {wc}x{hc}")
    grey = alp[0::4]
    if any(grey[i] != alp[4 * i + 1] or grey[i] != alp[4 * i + 2] for i in range(0, len(grey), 61)):
        raise ValueError("the alpha PNG is not grey (R = G = B)")
    lut = list(range(256))
    if linearize:
        lut = [round(lin(v)) for v in lut]
    if sharpen:
        lut = [max(0, min(255, 128 + 2 * (v - 128))) for v in lut]
    img = bytearray(col)
    img[3::4] = bytes(lut[v] for v in grey)
    return wc, hc, img, dict(colour_alpha_all_255=all(v == 255 for v in col[3::4]))


def bleed(img, w, h):
    """In place: colour of alpha < BLEED_T texels from the drawn texels outward, BLEED_PX rings; then the strands' mean."""
    A = img[3::4]
    known = bytearray(1 if a >= BLEED_T else 0 for a in A)
    sw = [0.0, 0.0, 0.0, 0.0]
    for i in range(0, w * h, 3):
        a = A[i]
        if a >= BLEED_T:
            for c in range(3):
                sw[c] += img[4 * i + c] * a
            sw[3] += a
    mean = tuple(round(sw[c] / sw[3]) for c in range(3)) if sw[3] else (0, 0, 0)
    nb = [(-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1)]
    front = set()
    for i in range(w * h):
        if not known[i]:
            x, y = i % w, i // w
            for dx, dy in nb:
                xx, yy = x + dx, y + dy
                if 0 <= xx < w and 0 <= yy < h and known[yy * w + xx]:
                    front.add(i)
                    break
    filled = 0
    for _ in range(BLEED_PX):
        if not front:
            break
        new = {}
        for i in front:
            x, y = i % w, i // w
            s = [0, 0, 0]
            n = 0
            for dx, dy in nb:
                xx, yy = x + dx, y + dy
                if 0 <= xx < w and 0 <= yy < h:
                    j = yy * w + xx
                    if known[j]:
                        for c in range(3):
                            s[c] += img[4 * j + c]
                        n += 1
            new[i] = tuple((s[c] + n // 2) // n for c in range(3))
        nxt = set()
        for i, rgb in new.items():
            img[4 * i:4 * i + 3] = bytes(rgb)
            known[i] = 1
        filled += len(new)
        for i in new:
            x, y = i % w, i // w
            for dx, dy in nb:
                xx, yy = x + dx, y + dy
                if 0 <= xx < w and 0 <= yy < h and not known[yy * w + xx]:
                    nxt.add(yy * w + xx)
        front = nxt
    rest = 0
    for i in range(w * h):
        if not known[i]:
            img[4 * i:4 * i + 3] = bytes(mean)
            rest += 1
    return dict(bled_texels=filled, mean_filled_texels=rest, strand_mean_rgb=mean)


def mip_down(img, w, h, plain=False):
    nw, nh = max(1, w // 2), max(1, h // 2)
    out = bytearray(nw * nh * 4)
    for y in range(nh):
        for x in range(nw):
            idx = [4 * (yy * w + xx) for yy in (min(h - 1, 2 * y), min(h - 1, 2 * y + 1))
                   for xx in (min(w - 1, 2 * x), min(w - 1, 2 * x + 1))]
            al = [255] * 4 if plain else [img[i + 3] for i in idx]   # plain: a normal map (its A is X, not coverage)
            sa = sum(al)
            o = 4 * (y * nw + x)
            for c in range(3):
                out[o + c] = (sum(img[i + c] * a for i, a in zip(idx, al)) + sa // 2) // sa if sa else \
                    (sum(img[i + c] for i in idx) + 2) // 4
            out[o + 3] = (sum(img[i + 3] for i in idx) + 2) // 4 if plain else (sa + 2) // 4
    return out, nw, nh


def encode(img, w, h, constant=None, plain=False):
    """BC3 body of all mips (w = h = 2048: 12) + 4 zero bytes; [(w, h, rmse rgb, rmse a, coverage >= 128)] per mip."""
    out, rep = bytearray(), []
    cache = {}
    while True:
        bw, bh = max(1, (w + 3) // 4), max(1, (h + 3) // 4)
        sq = [0, 0]
        for by in range(bh):
            for bx in range(bw):
                px = tc.block_pixels(img, w, h, bx, by)
                key = tuple(px) if constant else None
                blk = cache.get(key) if constant else None
                if blk is None:
                    blk = tc.encode_block(px, "BC3")
                    if constant:
                        cache[key] = blk
                out += blk
                dec = tc.decode_block(blk, "BC3")
                for p, q in zip(px, dec):
                    sq[0] += sum((p[c] - q[c]) ** 2 for c in range(3))
                    sq[1] += (p[3] - q[3]) ** 2
        n = w * h
        rep.append(dict(size=f"{w}x{h}", rmse_rgb=round(math.sqrt(sq[0] / (3 * n)), 3), rmse_alpha=round(math.sqrt(sq[1] / n), 3),
                        drawn_128=round(sum(1 for a in img[3::4] if a >= 128) / n, 4)))
        if w == 1 and h == 1:
            break
        img, w, h = mip_down(img, w, h, plain)
    return bytes(out) + b"\0\0\0\0", rep


def own_normal(path, w, h):
    """M17 (D3): a standard tangent-space normal map (OpenGL, as Blender bakes it) in the hair host's B02 layout, as
    hosttex.py writes a body host's normal: A = X (+u), G = Y (+v down: DirectX), R 128 and B 247 as in FLAT."""
    import hosttex
    nw, nh, nm, _ = tc.png_read(path)
    nm = hosttex.resample(nm, nw, nh, w, h)
    out = bytearray(bytes(FLAT) * (w * h))
    for i in range(w * h):
        x = nm[4 * i] / 255 * 2 - 1
        y = -(nm[4 * i + 1] / 255 * 2 - 1)        # OpenGL +Y up -> game +Y down
        ln = math.hypot(x, y)
        if ln > 1:
            x, y = x / ln, y / ln
        out[4 * i + 3] = max(0, min(255, round(128 + 127 * x)))
        out[4 * i + 1] = max(0, min(255, round(128 + 127 * y)))
    return out


def alpha_stats(A):
    n = len(A)
    return dict(near_0=round(sum(1 for a in A if a <= 8) / n, 4), near_255=round(sum(1 for a in A if a >= 247) / n, 4),
                partial=round(sum(1 for a in A if 8 < a < 247) / n, 4), drawn_128=round(sum(1 for a in A if a >= 128) / n, 4))


def surface_alpha(glb, A, w, h):
    """Alpha at 20,000 random points on the hair's triangles (its own UVs)."""
    import gltfread
    g = gltfread.load(glb)
    ni = g.find_node("hair")
    p = g.doc["meshes"][g.doc["nodes"][ni]["mesh"]]["primitives"][0]
    U = g.accessor(p["attributes"]["TEXCOORD_0"])
    idx = [x[0] for x in g.accessor(p["indices"])]
    tris = [idx[i:i + 3] for i in range(0, len(idx), 3)]
    area = [abs((U[b][0] - U[a][0]) * (U[c][1] - U[a][1]) - (U[c][0] - U[a][0]) * (U[b][1] - U[a][1])) for a, b, c in tris]
    rnd = random.Random(41)
    pick = rnd.choices(range(len(tris)), weights=area, k=20000)
    out = []
    for t in pick:
        a, b, c = tris[t]
        r1, r2 = rnd.random(), rnd.random()
        if r1 + r2 > 1:
            r1, r2 = 1 - r1, 1 - r2
        u = U[a][0] + r1 * (U[b][0] - U[a][0]) + r2 * (U[c][0] - U[a][0])
        v = U[a][1] + r1 * (U[b][1] - U[a][1]) + r2 * (U[c][1] - U[a][1])
        out.append(A[min(h - 1, int(v % 1 * h)) * w + min(w - 1, int(u % 1 * w))])
    return alpha_stats(out)


def file_sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def prepare(a):
    os.makedirs(a.out, exist_ok=True)
    key = dict(colour=file_sha(a.colour), alpha=file_sha(a.alpha), linearize=a.alpha_linearize, sharpen=a.alpha_sharpen,
               bleed=[BLEED_T, BLEED_PX], flat=FLAT, version=1)
    if a.normal:   # M17 (D3): the user's own normal map instead of the flat one
        key["normal"] = file_sha(a.normal)
    rp = os.path.join(a.out, "prepare.json")
    nf = "normal_own" if a.normal else "normal_flat"
    files = [os.path.join(a.out, f) for f in ("hair_rgba.png", "hair_colour.bc3", f"{nf}.png", f"{nf}.bc3")]
    if os.path.isfile(rp) and json.load(open(rp)).get("key") == key and all(os.path.isfile(f) for f in files):
        print("hairtex prepare: up to date", json.dumps(json.load(open(rp))["summary"]))
        return
    w, h, img, info = combine(a.colour, a.alpha, a.alpha_linearize, a.alpha_sharpen)
    _, _, raw_a, _ = tc.png_read(a.alpha)
    rep = dict(key=key, inputs=dict(colour=os.path.abspath(a.colour), alpha=os.path.abspath(a.alpha), size=f"{w}x{h}", **info))
    rep["alpha_mask_as_given"] = alpha_stats(raw_a[0::4])
    rep["alpha_written"] = alpha_stats(img[3::4])
    if a.glb:
        rep["surface_as_given"] = surface_alpha(a.glb, raw_a[0::4], w, h)
        rep["surface_written"] = surface_alpha(a.glb, img[3::4], w, h)
        for nm, lut in (("surface_if_linearized", [round(lin(v)) for v in range(256)]),
                        ("surface_if_sharpened", [max(0, min(255, 128 + 2 * (v - 128))) for v in range(256)])):
            rep[nm] = surface_alpha(a.glb, bytes(lut[v] for v in raw_a[0::4]), w, h)
    rep["bleed"] = bleed(img, w, h)
    tc.png_write(files[0] + ".tmp.png", w, h, img, alpha=True)
    body, lv = encode(bytearray(img), w, h)
    rep["colour_mips"] = lv
    open(files[1] + ".tmp", "wb").write(body)
    if a.normal:
        flat = own_normal(a.normal, w, h)
        tc.png_write(files[2] + ".tmp.png", w, h, flat, alpha=True)
        nbody, nlv = encode(flat, w, h, plain=True)
    else:
        flat = bytearray(bytes(FLAT) * (w * h))
        tc.png_write(files[2] + ".tmp.png", w, h, flat, alpha=True)
        nbody, nlv = encode(flat, w, h, constant=True)
    rep["normal_mips"] = nlv[:1]
    open(files[3] + ".tmp", "wb").write(nbody)
    for f in files:
        fsx.replace(f + (".tmp.png" if f.endswith(".png") else ".tmp"), f)
    rep["summary"] = dict(size=f"{w}x{h}", body_bytes=len(body), alpha_written=rep["alpha_written"],
                          colour_rmse_mip0=lv[0]["rmse_rgb"], alpha_rmse_mip0=lv[0]["rmse_alpha"],
                          drawn_128_by_mip=[m["drawn_128"] for m in lv[:8]])
    json.dump(rep, open(rp, "w"), indent=1)
    print("hairtex prepare:", json.dumps(rep["summary"]))


def put(a):
    import growdpc
    b = open(os.path.join(a.res, f"{a.id}.Bitmap_Z"), "rb").read()
    lb = struct.unpack_from("<I", b, 0x2C)[0]
    body = open(a.body, "rb").read()
    if len(body) != len(b) - 64 - lb:
        raise SystemExit(f"bitmap {a.id}: body {len(body)} bytes, the texture's is {len(b) - 64 - lb}")
    import texlib
    i = texlib.info(os.path.join(a.res, f"{a.id}.Bitmap_Z"))
    if (i["w"], i["h"], i["fmt"], i["mips"]) != (SIZE, SIZE, "BC3", 12):
        raise SystemExit(f"bitmap {a.id}: {i['w']}x{i['h']} {i['fmt']} mips {i['mips']}: not a 2048² BC3 12-mip texture")
    os.makedirs(a.work, exist_ok=True)
    g = growdpc.write(a.src, a.dst, {a.id: dict(resource=growdpc.make_resource(a.src, a.id, b[:64 + lb] + body, a.work))})
    print(f"bitmap {a.id} <- {os.path.basename(a.body)}: sectors inserted {g['sectors_inserted']}"
          + (" (slot enlarged)" if g["sectors_inserted"] else ""))


def xple_load(path, stubs):
    from bitmaps import decode
    from bmppreview import from_unpacked, xple_index
    st = {}
    for b, d in from_unpacked(stubs):
        bi = decode(b, d)
        st[bi["name"]] = bi
    cls = struct.unpack_from("<Q", from_unpacked(stubs)[0][0], 0x10)[0]
    idx, odd = xple_index(path, cls)
    if odd:
        raise SystemExit(f"unexpected records: {odd[:3]}")
    return st, idx


def writes(a):
    return {int(x.split("=")[0]): x.split("=", 1)[1] for x in a.write}


def xple(a):
    st, idx = xple_load(a.src, a.stubs)
    buf = bytearray(open(a.src, "rb").read())
    for bid, f in writes(a).items():
        body = open(f, "rb").read()
        if bid not in idx or bid not in st:
            raise SystemExit(f"{bid}: no body record / stub in {a.src}")
        bi = st[bid]
        _, pos, stored, dec, ctype = idx[bid]
        if ctype != 0:
            raise SystemExit(f"{bid}: LZ4 body; only raw slots are written")
        if (bi["w"], bi["h"], bi["fmt_label"], bi["mips"]) != (SIZE, SIZE, "BC3", 12) or len(body) != stored or dec != stored:
            raise SystemExit(f"{bid}: {bi['w']}x{bi['h']} {bi['fmt_label']} mips {bi['mips']} stored {stored}: does not match the body ({len(body)})")
        buf[pos:pos + stored] = body
        print(f"{bid}: {bi['w']}x{bi['h']} BC3 12 mips <- {os.path.basename(f)} (raw overwrite, {stored} bytes)")
    open(a.dst + ".tmp", "wb").write(buf)
    fsx.replace(a.dst + ".tmp", a.dst)


def verify_xple(a):
    so, io = xple_load(a.src, a.stubs)
    _, inn = xple_load(a.dst, a.stubs)
    A, B = open(a.src, "rb").read(), open(a.dst, "rb").read()
    ok = True
    same_records = {k: v[1:] for k, v in io.items()} == {k: v[1:] for k, v in inn.items()}
    print(f"  records (id, body position, sizes, compression) identical: {same_records}; size {len(A)} -> {len(B)}")
    ok &= same_records and len(A) == len(B)
    wr = writes(a)
    spans = sorted((io[k][1], io[k][1] + io[k][2]) for k in wr)
    outside, p = 0, 0
    for s, e in spans + [(len(A), len(A))]:
        if A[p:s] != B[p:s]:
            outside += sum(1 for x, y in zip(A[p:s], B[p:s]) if x != y)
        p = e
    print(f"  bytes differing outside the {len(wr)} written bodies: {outside}")
    ok &= outside == 0
    w, h, ref, _ = tc.png_read(a.png)
    for bid, f in wr.items():
        _, pos, stored, _, _ = inn[bid]
        got = B[pos:pos + stored]
        eq = got == open(f, "rb").read()
        n = (SIZE // 4) ** 2 * 16
        dec = tc.decode_bc(got[:n], "BC3", SIZE, SIZE)
        rnd = random.Random(bid & 0xffff)
        sq, mx = [0, 0], [0, 0]
        for _ in range(20000):
            i = 4 * rnd.randrange(SIZE * SIZE)
            for c in range(4):
                d = abs(dec[i + c] - ref[i + c])
                sq[c == 3] += d * d
                mx[c == 3] = max(mx[c == 3], d)
        rr, ra = math.sqrt(sq[0] / 60000), math.sqrt(sq[1] / 20000)
        good = eq and rr < 4 and ra < 4
        print(f"  {bid}: body equals the prepared file: {eq}; mip 0 vs hair_rgba.png at 20,000 points: RGB RMSE {rr:.2f} "
              f"(max {mx[0]}), alpha RMSE {ra:.2f} (max {mx[1]}) -> {'OK' if good else 'FAIL'}")
        ok &= good
    print("  VERIFY:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["prepare", "put", "xple", "verify-xple"])
    ap.add_argument("src", nargs="?"); ap.add_argument("dst", nargs="?")
    ap.add_argument("--colour"); ap.add_argument("--alpha"); ap.add_argument("--out"); ap.add_argument("--glb")
    ap.add_argument("--normal", help="M17 (D3): own hair normal map (OpenGL PNG); default: flat")
    ap.add_argument("--alpha-linearize", action="store_true"); ap.add_argument("--alpha-sharpen", action="store_true")
    ap.add_argument("--res"); ap.add_argument("--id", type=int); ap.add_argument("--body"); ap.add_argument("--work")
    ap.add_argument("--stubs"); ap.add_argument("--write", action="append", default=[]); ap.add_argument("--png")
    a = ap.parse_args()
    dict(prepare=prepare, put=put, xple=xple)[a.mode](a) if a.mode != "verify-xple" else verify_xple(a)


if __name__ == "__main__":
    main()
