"""M17 session 4 (session 65): engine copy of analysis\fullrun2\texlibrary.py for seamless.py; paths from paths.py.
(Original docstring follows.)
Item 4: a library of seamless tiling textures from the game's own textures (generated locally from the player's game
files; nothing of it is ever shipped inside the tool).

Per entry (SOURCES): the game texture(s) are decoded at full resolution (mip 0, texlib; from the unpacked archives).
1. Coverage: the UV triangles of the meshes that use the texture are rasterised on a 256 x 256 grid (blocks); blocks
   touching an island edge are dropped (one-block erosion), so a window never crosses a UV seam.
2. Clean window: block means of luminance (and of the normal map's X / Y when there is one); for each window size from
   the list (largest first), the window with the lowest low-frequency variation (std of block luminance + std of
   block normal X / Y) among fully covered windows (optional channel condition, e.g. metal only). The largest size whose
   best score is at most ACCEPT x the best score at 128 px (or at the smallest size found) is taken.
3. Seamless (method "wrap cross-fade"): a crop of S + b texels (b = S / 8) becomes an S x S tile: for the first b columns
   out[x] = t C[x] + (1 - t) C[x + S] with t = x / b, so column 0 continues column S - 1; then the same for rows. Normal
   X / Y blended and renormalised; roughness / metal blended like colour.
   A source that already tiles (its own wrap-edge step no larger than WRAP_OK x its average neighbour step) is
   exported whole, unchanged.
4. Seam check: the mean step across the wrap edge (last column to first, last row to first) divided by the mean step
   between neighbouring columns / rows inside AT THE SAME PLACE IN THE 4 x 4 COMPRESSION BLOCKS (the game textures are
   BC1 / BC3: steps between blocks are larger than inside them) (1.0 = the edge looks like any other column). Before
   (the raw crop) and after.
Output: output\\blender_kit_v2\\Amicia Seamless Textures\\<group>\\<name>_<size>_<map>.png (colour sRGB; normal OpenGL
(R = X, G = Y up, B = Z); roughness, metal grey), README.md; review: output\\texture_library_review\\contact_sheet.png
(each tile 2 x 2 to show the seams: colour | normal) + contact_sheet.md; timing in analysis\\fullrun2\\texlibrary.txt.
Usage: python -I texlibrary.py
"""
import math
import os
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paths  # noqa: E402
import fsx  # noqa: E402  (renames with a retry: Windows may hold a new file)
OUT = paths.OUT
import texcodec as tc  # noqa: E402
import texlib  # noqa: E402
from meshfmt import parse  # noqa: E402

LIB = os.path.join(OUT, "blender_kit_v2", "Amicia Seamless Textures")
REV = os.path.join(OUT, "texture_library_review")
G = 256
ACCEPT = 1.8   # x the best score at 128 px (or the smallest size tried)
WRAP_OK = 1.6
SIZES = [1024, 768, 512, 384, 320, 256, 192, 160, 128, 96, 64]
P = "P_AMICIA"
# name, group, archive, colour id, normal id (game layout: A = X, G = Y down, R = metal, B = roughness) or None,
# meshes (labels -> ids via the parts tables) for coverage, condition, note
SOURCES = [
    ("skin_face", "skin", P, -5588537412492053000, 1218632715061691073, ["M37"], "skin", "B05 / B07: the head (main chapters)"),
    ("skin_hand_right", "skin", P, 8012746092182889576, -368835965779867660, ["M00"], "skin", "B18 / B19: right hand (darker)"),
    ("skin_hand_left", "skin", P, -712431051473869666, -368835965779867660, ["M00"], "skin", "B20 / B19: left hand"),
    ("skin_face_chapter1", "skin", "P_AMICIA_CHP1", -64390501502983496, 6738449388349379969, ["C1-19"], "skin", "1-02 / 1-11: Chapter I head"),
    ("skin_hands_chapter1", "skin", "P_AMICIA_CHP1", -8932397376110028685, -6629550837092335613, ["C1-22"], "skin", "1-04 / 1-03: Chapter I hands and wrists"),
    ("skin_pores_detail", "skin", P, None, 8409255432906607791, [], "whole", "B08: the head's tiling detail normal (pores), used 20x over the face"),
    ("linen", "cloth", P, 7367250464636474543, -4276926054389570666, ["M30"], "whole", "B21 / B23: inner clothes and sleeves (tiled in game)"),
    ("wool_hood", "cloth", P, 4936225294147582657, -1866728945048302088, ["M32"], None, "B15 / B16: the shoulder hood"),
    ("cloth_shirt", "cloth", P, -3060217940231028703, 8583613315098523416, ["M33"], "nonmetal", "B37 / B38: the shirt (outfit option 1)"),
    ("leather_pants_boots", "leather", P, 6925564721753714648, -4430282810869589791, ["M18", "M25", "M38"], None, "B12 / B13: pants, corset, boots"),
    ("leather_belt", "leather", P, 8181817961854187753, -2299214514773885863, ["M21", "M23", "M43"], "nonmetal", "B40 / B41: belts"),
    ("metal_shield", "metal", P, -3060217940231028703, 8583613315098523416, ["M22"], "metal", "B37 / B38: the elbow shield plate"),
    ("fur_collar", "fur", "P_AMICIA_END", 5725407798126703752, -1372992972552366560, ["E-14", "E-29"], "opaque", "END-09 / END-00: the fur collar (final chapters)"),
]
QUALITY = {"metal_shield": "; POOR: the plate is small and carries a rivet pattern (64 px)",
           "cloth_shirt": "; a printed pattern: the repeat is visible by nature"}
log = open(os.devnull, "w")   # seamless.py keeps the log


def say(s=""):
    print(s, flush=True)
    log.write(s + "\n")
    log.flush()


def res_dir(a):
    return os.path.join(OUT, f"{a}_unpacked", "resources")


def mesh_ids(a):
    return {r.split("\t")[0]: int(r.split("\t")[1]) for r in open(os.path.join(paths.TABLES, "analysis", f"{a}_parts.tsv")).read().split("\n")[1:] if r}


def coverage(a, labels):
    """G x G blocks covered by the meshes' UV triangles (every submesh), eroded by one block."""
    cov = bytearray(G * G)
    ids = mesh_ids(a)
    for lab in labels:
        b = open(os.path.join(res_dir(a), f"{ids[lab]}.Mesh_Z"), "rb").read()
        m = parse(b)
        geo = [v for v in m["vbs"] if v["flags"] == 2]
        for k, s in enumerate(m["subs"]):
            vb = geo[k] if len(geo) > 1 else geo[0]
            ib = m["ibs"][k] if len(m["ibs"]) > 1 else m["ibs"][0]
            idx = struct.unpack_from(f"<{s['ntri'] * 3}H", b, ib["pos"] + 2 * s["start"])
            uv = lambda i: struct.unpack_from("<2e", b, vb["pos"] + i * vb["stride"] + 20)
            for t in range(0, len(idx), 3):
                tri = [idx[t + q] for q in range(3)]
                if max(tri) >= vb["count"]:
                    tri = [i - s["base"] for i in tri]
                if min(tri) < 0 or max(tri) >= vb["count"]:
                    continue
                pts = [(uv(i)[0] * G, uv(i)[1] * G) for i in tri]
                x0, x1 = int(max(0, math.floor(min(p[0] for p in pts)))), int(min(G - 1, math.ceil(max(p[0] for p in pts))))
                y0, y1 = int(max(0, math.floor(min(p[1] for p in pts)))), int(min(G - 1, math.ceil(max(p[1] for p in pts))))
                (ax, ay), (bx, by), (cx, cy) = pts
                d = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
                if abs(d) < 1e-12:
                    continue
                for y in range(y0, y1 + 1):
                    for x in range(x0, x1 + 1):
                        px, py = x + 0.5, y + 0.5
                        l1 = ((by - cy) * (px - cx) + (cx - bx) * (py - cy)) / d
                        l2 = ((cy - ay) * (px - cx) + (ax - cx) * (py - cy)) / d
                        if l1 >= -0.02 and l2 >= -0.02 and 1 - l1 - l2 >= -0.02:
                            cov[y * G + x] = 1
    er = bytearray(G * G)
    for y in range(1, G - 1):
        for x in range(1, G - 1):
            if all(cov[(y + dy) * G + x + dx] for dy in (-1, 0, 1) for dx in (-1, 0, 1)):
                er[y * G + x] = 1
    return er


def block_means(img, W, H, chans):
    """[G*G] tuples of the mean of each channel (index into rgba) per block."""
    bw, bh = W // G, H // G
    out = []
    step = max(1, bw // 4)
    for by in range(G):
        for bx in range(G):
            acc = [0.0] * len(chans)
            n = 0
            for y in range(by * bh, (by + 1) * bh, step):
                row = y * W
                for x in range(bx * bw, (bx + 1) * bw, step):
                    o = 4 * (row + x)
                    for k, c in enumerate(chans):
                        acc[k] += img[o + c]
                    n += 1
            out.append([a / n for a in acc])
    return out


def integral(vals):
    I = [[0.0] * (G + 1) for _ in range(G + 1)]
    for y in range(G):
        s = 0.0
        for x in range(G):
            s += vals[y * G + x]
            I[y + 1][x + 1] = I[y][x + 1] + s
    return I


def rect(I, x, y, w):
    return I[y + w][x + w] - I[y][x + w] - I[y + w][x] + I[y][x]


def best_window(feats, ok, W):
    """feats: list of per-block feature lists; ok: per-block 0/1. Returns [(size_px, score, bx, by)] best per size."""
    nf = len(feats[0])
    Is = [integral([f[k] for f in feats]) for k in range(nf)]
    Iq = [integral([f[k] * f[k] for f in feats]) for k in range(nf)]
    Io = integral([float(v) for v in ok])
    res = []
    for S in SIZES:
        need = S + S // 8
        w = math.ceil(need * G / W)
        if w >= G:
            continue
        best = None
        for by in range(0, G - w + 1):
            for bx in range(0, G - w + 1):
                if rect(Io, bx, by, w) < w * w:
                    continue
                n = w * w
                sc = sum(math.sqrt(max(0.0, rect(Iq[k], bx, by, w) / n - (rect(Is[k], bx, by, w) / n) ** 2)) for k in range(nf))
                if best is None or sc < best[1]:
                    best = (S, sc, bx, by)
        if best:
            res.append(best)
    return res


def wrap_ratio(img, W, H, chans):
    """(columns, rows): mean |step| across the wrap edge / mean |step| between neighbouring columns (rows) inside."""
    def colstep(x0, x1):
        return sum(abs(img[4 * (y * W + x0) + c] - img[4 * (y * W + x1) + c]) for y in range(0, H, max(1, H // 256)) for c in chans)
    def rowstep(y0, y1):
        return sum(abs(img[4 * (y0 * W + x) + c] - img[4 * (y1 * W + x) + c]) for x in range(0, W, max(1, W // 256)) for c in chans)
    xs = [x for x in range(3, W - 1, 4)][::max(1, W // 128)]   # steps from the last column of a 4 x 4 block to the next
    ys = [y for y in range(3, H - 1, 4)][::max(1, H // 128)]   # block: the same phase as the wrap edge (W, H multiples of 4)
    inner_c = sum(colstep(x, x + 1) for x in xs) / len(xs)
    inner_r = sum(rowstep(y, y + 1) for y in ys) / len(ys)
    return colstep(W - 1, 0) / max(1e-6, inner_c), rowstep(H - 1, 0) / max(1e-6, inner_r)


def crop(img, W, x0, y0, w, h):
    out = bytearray(4 * w * h)
    for y in range(h):
        o = 4 * ((y0 + y) * W + x0)
        out[4 * y * w:4 * (y + 1) * w] = img[o:o + 4 * w]
    return out


def crossfade(img, w, h, S, b, normal=False):
    """(S + b) x (S + b) -> S x S, wrap cross-fade along x then y (see the docstring)."""
    def blend(p, q, t):
        return bytes(round(t * p[k] + (1 - t) * q[k]) for k in range(4))
    tmp = bytearray(4 * S * h)
    for y in range(h):
        for x in range(S):
            o = 4 * (y * w + x)
            if x < b:
                t = x / b
                tmp[4 * (y * S + x):4 * (y * S + x) + 4] = blend(img[o:o + 4], img[o + 4 * S:o + 4 * S + 4], t)
            else:
                tmp[4 * (y * S + x):4 * (y * S + x) + 4] = img[o:o + 4]
    out = bytearray(4 * S * S)
    for y in range(S):
        for x in range(S):
            o = 4 * (y * S + x)
            if y < b:
                t = y / b
                out[o:o + 4] = blend(tmp[o:o + 4], tmp[o + 4 * S * S:o + 4 * S * S + 4], t)
            else:
                out[o:o + 4] = tmp[o:o + 4]
    if normal:   # renormalise X (A) / Y (G); z is rebuilt on export
        for i in range(S * S):
            x, y = out[4 * i + 3] / 255 * 2 - 1, out[4 * i + 1] / 255 * 2 - 1
            l = math.hypot(x, y)
            if l > 1:
                out[4 * i + 3], out[4 * i + 1] = round(128 + 127 * x / l), round(128 + 127 * y / l)
    return out


def export_maps(name, group, S, col, nrm, notes):
    """Colour; from the game normal: normal (OpenGL), roughness (B); metal (R) only for the metal group. B08 (the pore
    detail map) is a normal map only: its R / B are not roughness or metal."""
    d = os.path.join(LIB, group)
    os.makedirs(d, exist_ok=True)
    files = []

    def write(kind, rgba):
        p = os.path.join(d, f"{name}_{S}_{kind}.png")
        tc.png_write(p + ".tmp", S, S, rgba)
        fsx.replace(p + ".tmp", p)
        files.append(os.path.basename(p))
    if col:
        write("colour", col)
    if nrm:
        n = bytearray(4 * S * S)
        r = bytearray(4 * S * S)
        mt = bytearray(4 * S * S)
        metal = False
        for i in range(S * S):
            x, y = nrm[4 * i + 3] / 255 * 2 - 1, nrm[4 * i + 1] / 255 * 2 - 1
            z = math.sqrt(max(0.0, 1 - x * x - y * y))
            n[4 * i], n[4 * i + 1], n[4 * i + 2] = nrm[4 * i + 3], 255 - nrm[4 * i + 1], round(128 + 127 * z)
            r[4 * i] = r[4 * i + 1] = r[4 * i + 2] = nrm[4 * i + 2]
            mt[4 * i] = mt[4 * i + 1] = mt[4 * i + 2] = nrm[4 * i]
            metal |= nrm[4 * i] > 16
        write("normal", n)
        if name != "skin_pores_detail":
            write("roughness", r)
        if metal and group == "metal":
            write("metal", mt)
    return files


def main():
    t_all = time.time()
    entries = []
    for name, group, a, cid, nid, labs, cond, note in SOURCES:
        t0 = time.time()
        res = res_dir(a)
        col = texlib.load(res, cid) if cid else None
        nrm = texlib.load(res, nid) if nid else None
        base = col or nrm
        W, H = base[0], base[1]
        if nrm and col and (nrm[0], nrm[1]) != (W, H):
            nrm_scaled = True
        else:
            nrm_scaled = False
        say(f"\n== {name} ({note}): {W}x{H}" + (f", normal {nrm[0]}x{nrm[1]}" if nrm else ""))
        if cond == "whole":
            chans = [0, 1, 2] if col else [3, 1]
            cr, rr = wrap_ratio(base[3], W, H, chans)
            tiles = cr < WRAP_OK and rr < WRAP_OK
            say(f"   whole texture wrap-edge ratio columns {cr:.2f}, rows {rr:.2f} -> {'already tiles in the game' if tiles else 'does not tile'}")
            if tiles:
                S = W
                files = export_maps(name, group, S, col[3] if col else None, nrm[3] if nrm and (nrm[0], nrm[1]) == (W, H) else (nrm[3] if not col else None), [])
                entries.append(dict(name=name, group=group, note=note, size=S, made="already tiling in the game (exported whole)",
                                    before=(cr, rr), after=(cr, rr), files=files, tile=(col or nrm)[3], ntile=nrm[3] if nrm else None,
                                    secs=time.time() - t0))
                say(f"   exported whole: {files}")
                continue
        cov = coverage(a, labs) if labs else bytearray([1]) * (G * G)
        if cond == "opaque":   # fur cards: only opaque texels (alpha) count
            am = block_means(col[3], W, H, [3])
            cov = bytearray(c if m[0] > 200 else 0 for c, m in zip(cov, am))
            say(f"   opaque covered blocks: {sum(cov)} of {G * G}")
        lum = block_means(col[3], W, H, [0, 1, 2]) if col else None
        nf = None
        if nrm:
            nf = block_means(nrm[3], nrm[0], nrm[1], [3, 1, 0]) if (nrm[0], nrm[1]) == (W, H) else None
        feats = []
        ok = bytearray(cov)
        if cond == "skin":   # skin-coloured blocks only (R > G > B) among the brighter 50 % of the covered ones: not scalp,
            reds = sorted(lum[i][0] for i in range(G * G) if cov[i])   # brows, lips or dark creases
            rmin = reds[len(reds) // 2]
            for i in range(G * G):
                r, g_, b_ = lum[i]
                if not (r >= rmin and r > g_ + 8 and g_ > b_):
                    ok[i] = 0
        for i in range(G * G):
            f = []
            if lum:
                f.append(0.3 * lum[i][0] + 0.59 * lum[i][1] + 0.11 * lum[i][2])
            if nf:
                f += [nf[i][0], nf[i][1]]
                if cond == "metal" and nf[i][2] < 200:
                    ok[i] = 0
                if cond == "nonmetal" and nf[i][2] > 40:
                    ok[i] = 0
            feats.append(f)
        say(f"   usable blocks (covered, eroded, condition {cond}): {sum(ok)} of {G * G}")
        cands = best_window(feats, ok, W)
        if not cands:
            say("   NO clean window found: skipped")
            entries.append(dict(name=name, group=group, note=note, size=0, made="skipped: no clean area", files=[], secs=time.time() - t0))
            continue
        ref = next((c[1] for c in cands if c[0] == 128), cands[-1][1])
        pick = next(c for c in cands if c[1] <= ACCEPT * max(ref, 1e-6))
        S, sc, bx, by = pick
        say("   best window per size (size: score): " + ", ".join(f"{c[0]}: {c[1]:.2f}" for c in cands) + f" -> {S} px")
        b = S // 8
        x0, y0 = (bx * W // G) // 4 * 4, (by * H // G) // 4 * 4   # on the 4 x 4 compression blocks
        cimg = crop(col[3], W, x0, y0, S + b, S + b) if col else None
        nimg = crop(nrm[3], nrm[0], x0, y0, S + b, S + b) if nrm and not nrm_scaled else None
        ref = cimg or nimg
        chans = [0, 1, 2] if cimg else [3, 1]
        raw = crop(ref, S + b, 0, 0, S, S)
        before = wrap_ratio(raw, S, S, chans)
        ct = crossfade(cimg, S + b, S + b, S, b) if cimg else None
        nt = crossfade(nimg, S + b, S + b, S, b, normal=True) if nimg else None
        after = wrap_ratio(ct or nt, S, S, chans)
        files = export_maps(name, group, S, ct, nt, [])
        say(f"   crop at texel ({x0}, {y0}), {S + b} px -> tile {S} px (band {b}); wrap-edge ratio before {before[0]:.2f} / {before[1]:.2f}, "
            f"after {after[0]:.2f} / {after[1]:.2f}; files {files}" + ("; normal map size differs: colour only" if nrm_scaled else ""))
        entries.append(dict(name=name, group=group, note=note, size=S, made="made seamless (wrap cross-fade)", before=before,
                            after=after, files=files, tile=ct or nt, ntile=nt, secs=time.time() - t0, at=(x0, y0)))
    say(f"\ntotal generation time: {time.time() - t_all:.0f} s")
    sheet(entries)
    readme(entries)
    log.close()


def sheet(entries):
    os.makedirs(REV, exist_ok=True)
    C = 256
    rows = [e for e in entries if e.get("files")]
    Wd, Hd = 2 * C + 12, len(rows) * (C + 6) + 6
    img = bytearray([40, 40, 40, 255] * (Wd * Hd))
    for r, e in enumerate(rows):
        for k, t in enumerate((e["tile"] if e["tile"] is not e.get("ntile") else None, e.get("ntile"))):
            if t is None:
                continue
            S = e["size"]
            is_n = k == 1
            for y in range(C):
                for x in range(C):
                    sx, sy = (x * 2 * S // C) % S, (y * 2 * S // C) % S   # 2 x 2 repeats: the seams sit in the middle
                    o = 4 * (sy * S + sx)
                    px = (t[o + 3], 255 - t[o + 1], 255) if is_n else (t[o], t[o + 1], t[o + 2])
                    d = 4 * ((6 + r * (C + 6) + y) * Wd + 4 + k * (C + 4) + x)
                    img[d:d + 3] = bytes(px)
    p = os.path.join(REV, "contact_sheet.png")
    tc.png_write(p + ".tmp", Wd, Hd, img)
    fsx.replace(p + ".tmp", p)
    with open(os.path.join(REV, "contact_sheet.md"), "w", encoding="utf-8") as f:
        f.write("# Seamless texture library: contact sheet (for review only, not part of the library)\n\n"
                "contact_sheet.png: one row per texture, top to bottom; left the colour, right the normal map (blue = flat), "
                "each tile repeated 2 x 2, so a seam would show as a line through the middle of the square.\n\n"
                "| Row | Texture | Source | Tile | How | Wrap edge before -> after (1.0 = invisible) |\n|---|---|---|---|---|---|\n")
        for r, e in enumerate(rows, 1):
            f.write(f"| {r} | {e['group']}/{e['name']} | {e['note']} | {e['size']} px | {e['made']}{QUALITY.get(e['name'], '')} | "
                    f"{e['before'][0]:.2f} / {e['before'][1]:.2f} -> {e['after'][0]:.2f} / {e['after'][1]:.2f} |\n")
        for e in entries:
            if not e.get("files"):
                f.write(f"\nNot made: {e['name']} ({e['note']}): {e['made']}\n")


def readme(entries):
    with open(os.path.join(LIB, "README.md"), "w", encoding="utf-8") as f:
        f.write("# Amicia Seamless Textures\n\n"
                "Tiling textures cut from A Plague Tale: Innocence's own Amicia textures, made on your computer from your own "
                "game files. They are not part of the tool and must not be shared.\n\n"
                "**Use in Blender:** plug *_colour into Base Color (sRGB), *_normal into a Normal Map node (OpenGL, Non-Color), "
                "*_roughness into Roughness (Non-Color), *_metal into Metallic (Non-Color). Scale the UVs (Mapping node) to "
                "repeat a tile; the number in the file name is the tile size in pixels. Bake the result into your model's own "
                "texture before export.\n\n| Folder / name | From | Tile | Maps |\n|---|---|---|---|\n")
        for e in entries:
            if e.get("files"):
                maps = ", ".join(x.rsplit("_", 1)[1][:-4] for x in e["files"])
                f.write(f"| {e['group']}/{e['name']} | {e['note']} | {e['size']} px, {e['made']} | {maps} |\n")
        f.write("\nSkin, cloth and leather tiles come from areas without seams or features; the edges were blended so the "
                "tile repeats without a visible line. They are small (the game's clean areas are small): repeat them, do not "
                "stretch them.\n")


if __name__ == "__main__":
    main()
