"""M17 session 4 (session 65): engine copy of analysis\fullrun3\texlibrary2.py. Usage: python -I seamless.py <library dir>
(Original docstring follows.)
Session 55, item 4: the seamless texture library, better (rebuilds output\\blender_kit_v2\\"Amicia Seamless Textures").

Per entry (texlibrary.SOURCES of session 54; the fur stays out: it has no solid area):
1. EXTRACTED: the largest clean window (texlibrary.best_window, same rules) made seamless by the wrap cross-fade:
   <name>_extracted_<S>_<map>.png (S = its size, 64-512 px).
2. SYNTHESISED: larger tiles at the game's own texel size, made from the same clean window ("sample") by wrap-around
   patch quilting: a canvas of N x N (N = 512, 1024, 2048) on a torus is covered by square patches of P = STEP + STEP/4
   texels placed every STEP texels; each patch is the best of K random sample positions (sum of squared differences
   with what is already on the canvas in its overlaps, colour and normal together, every fourth texel); patches are
   blended with linear ramps over the overlaps that add up to exactly 1 everywhere (feathered quilting), so the result
   tiles in both directions by construction. The colour and the game normal (A = X, G = Y, B = roughness, R = metal)
   are quilted with the same patch layout, so the maps stay aligned: <name>_synth_<N>_<map>.png.
3. Seam check (wrap edge vs inside, same 4 x 4 block phase as session 54) for every file.
Maps: colour (sRGB), normal (OpenGL: R = X, G = Y up, B = Z), roughness (grey, from the game's B), metal (grey, metal
group only); the pore detail map is a normal map only. One PNG per map, no atlas, no combined sheet.
README.md in the library; review: output\\texture_library_review\\library_sheet.png + library_sheet.md; timings in
analysis\\fullrun3\\texlibrary2.txt.
Usage: python -I texlibrary2.py   (run when no build runs: about 150 MB of memory)
"""
import array
import math
import os
import random
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paths  # noqa: E402
import fsx  # noqa: E402  (renames with a retry: Windows may hold a new file)
import zlib  # noqa: E402
OUT = paths.OUT
import texcodec as tc  # noqa: E402
import texlib  # noqa: E402
import seamless_src as T1  # noqa: E402   (session 54: sources, coverage, windows, cross-fade, wrap ratio)

LIB = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.join(OUT, "kits", "Amicia Seamless Textures")
REV = os.path.join(OUT, "texture_library_review")
SIZES = [512, 1024, 2048]
K = 12
os.makedirs(os.path.dirname(LIB), exist_ok=True)
log = open(LIB + "_log.txt", "w")


def say(s=""):
    print(s, flush=True)
    log.write(s + "\n")
    log.flush()


def quilt(sample, S, chans, N, seed):
    """sample: S x S, `chans` bytes per texel (bytearray). Returns an N x N tiling image (bytearray) with the same
    channel count. STEP 64 for samples of 192 px or more, else 32 (and 16 for tiny samples)."""
    step = 64 if S >= 192 else (32 if S >= 96 else 16)
    O = step // 4
    P = step + O
    n = N // step
    rnd = random.Random(seed)
    acc = array.array("f", bytes(4 * N * N * chans))
    wsum = array.array("f", bytes(4 * N * N))
    ramp = [min(1.0, (i + 0.5) / O) if i < O else (max(0.0, (P - i - 0.5) / O) if i >= step else 1.0) for i in range(P)]
    for gy in range(n):
        for gx in range(n):
            x0, y0 = gx * step, gy * step
            best, bc = None, None
            cands = [(rnd.randrange(0, S - P + 1), rnd.randrange(0, S - P + 1)) for _ in range(K)]
            for sx, sy in cands:
                c = 0.0
                for yy in range(0, P, 4):
                    cy = (y0 + yy) % N
                    for xx in range(0, P, 4):
                        if O <= xx < step and O <= yy < step:
                            continue   # inside the patch's own interior: nothing to match there
                        cx = (x0 + xx) % N
                        w = wsum[cy * N + cx]
                        if w <= 0:
                            continue
                        o = (cy * N + cx) * chans
                        so = ((sy + yy) * S + sx + xx) * chans
                        for ch in range(chans):
                            d = acc[o + ch] / w - sample[so + ch]
                            c += d * d
                if bc is None or c < bc:
                    bc, best = c, (sx, sy)
            sx, sy = best
            for yy in range(P):
                cy = (y0 + yy) % N
                ry = ramp[yy]
                row = cy * N
                srow = (sy + yy) * S + sx
                for xx in range(P):
                    w = ry * ramp[xx]
                    if w <= 0:
                        continue
                    cx = (x0 + xx) % N
                    wsum[row + cx] += w
                    o = (row + cx) * chans
                    so = (srow + xx) * chans
                    for ch in range(chans):
                        acc[o + ch] += w * sample[so + ch]
    out = bytearray(N * N * chans)
    for i in range(N * N):
        w = wsum[i] or 1.0
        for ch in range(chans):
            out[i * chans + ch] = max(0, min(255, round(acc[i * chans + ch] / w)))
    return out


def to_rgba(src, n, chans, first):
    out = bytearray(4 * n)
    for i in range(n):
        out[4 * i:4 * i + 4] = src[i * chans + first:i * chans + first + 4]
    return out


def write_maps(name, group, tag, S, col, nrm):
    d = os.path.join(LIB, group)
    os.makedirs(d, exist_ok=True)
    files = []

    def write(kind, rgba):
        p = os.path.join(d, f"{name}_{tag}_{S}_{kind}.png")
        tc.png_write(p + ".tmp", S, S, rgba)
        fsx.replace(p + ".tmp", p)
        files.append(os.path.basename(p))
    if col:
        for i in range(S * S):
            col[4 * i + 3] = 255
        write("colour", col)
    if nrm:
        n_, r_, m_ = bytearray(4 * S * S), bytearray(4 * S * S), bytearray(4 * S * S)
        for i in range(S * S):
            x, y = nrm[4 * i + 3] / 255 * 2 - 1, nrm[4 * i + 1] / 255 * 2 - 1
            l = math.hypot(x, y)
            if l > 1:
                x, y = x / l, y / l
            z = math.sqrt(max(0.0, 1 - x * x - y * y))
            n_[4 * i], n_[4 * i + 1], n_[4 * i + 2], n_[4 * i + 3] = round(128 + 127 * x), round(128 - 127 * y), round(128 + 127 * z), 255
            r_[4 * i] = r_[4 * i + 1] = r_[4 * i + 2] = nrm[4 * i + 2]
            r_[4 * i + 3] = m_[4 * i + 3] = 255
            m_[4 * i] = m_[4 * i + 1] = m_[4 * i + 2] = nrm[4 * i]
        write("normal", n_)
        if name != "skin_pores_detail":
            write("roughness", r_)
        if group == "metal":
            write("metal", m_)
    return files


def main():
    t_all = time.time()
    if os.path.isdir(LIB):
        shutil.rmtree(LIB)
    os.makedirs(LIB)
    entries = []
    for name, group, a, cid, nid, labs, cond, note in T1.SOURCES:
        if name == "fur_collar":
            entries.append(dict(name=name, group=group, note=note, rows=[], skipped="not usable: the fur is cut-out strands "
                                "on hair cards, with no solid area to sample"))
            continue
        t0 = time.time()
        res = T1.res_dir(a)
        col = texlib.load(res, cid) if cid else None
        nrm = texlib.load(res, nid) if nid else None
        base = col or nrm
        W, H = base[0], base[1]
        same = nrm is not None and (nrm[0], nrm[1]) == (W, H)
        say(f"\n== {name} ({note})")
        if cond == "whole":   # already tiling in the game (pores) or a small whole texture (linen): the whole image is the sample
            x0 = y0 = 0
            S = W
            if name == "skin_pores_detail":
                tile_c, tile_n, made, before, after = None, nrm[3], "already tiling in the game (exported whole)", None, None
            else:
                tile_c = tile_n = None
        rows = []
        if cond != "whole" or name != "skin_pores_detail":
            # the clean window, as in session 54
            cov = T1.coverage(a, labs) if labs else bytearray([1]) * (T1.G * T1.G)
            lum = T1.block_means(col[3], W, H, [0, 1, 2]) if col else None
            nf = T1.block_means(nrm[3], W, H, [3, 1, 0]) if same else None
            ok = bytearray(cov)
            if cond == "skin":
                reds = sorted(lum[i][0] for i in range(T1.G * T1.G) if cov[i])
                rmin = reds[len(reds) // 2]
                for i in range(T1.G * T1.G):
                    r, g_, b_ = lum[i]
                    if not (r >= rmin and r > g_ + 8 and g_ > b_):
                        ok[i] = 0
            feats = []
            for i in range(T1.G * T1.G):
                f = [0.3 * lum[i][0] + 0.59 * lum[i][1] + 0.11 * lum[i][2]] if lum else []
                if nf:
                    f += [nf[i][0], nf[i][1]]
                    if cond == "metal" and nf[i][2] < 200:
                        ok[i] = 0
                    if cond == "nonmetal" and nf[i][2] > 40:
                        ok[i] = 0
                feats.append(f)
            cands = T1.best_window(feats, ok, W)
            ref = next((c[1] for c in cands if c[0] == 128), cands[-1][1])
            S, sc, bx, by = next(c for c in cands if c[1] <= T1.ACCEPT * max(ref, 1e-6))
            b = S // 8
            x0, y0 = (bx * W // T1.G) // 4 * 4, (by * H // T1.G) // 4 * 4
            cimg = T1.crop(col[3], W, x0, y0, S + b, S + b) if col else None
            nimg = T1.crop(nrm[3], W, x0, y0, S + b, S + b) if same else None
            refimg = cimg or nimg
            chans = [0, 1, 2] if cimg else [3, 1]
            before = T1.wrap_ratio(T1.crop(refimg, S + b, 0, 0, S, S), S, S, chans)
            tile_c = T1.crossfade(cimg, S + b, S + b, S, b) if cimg else None
            tile_n = T1.crossfade(nimg, S + b, S + b, S, b, normal=True) if nimg else None
            after = T1.wrap_ratio(tile_c or tile_n, S, S, chans)
            made = "extracted, edges blended (wrap cross-fade)"
        files = write_maps(name, group, "extracted", S, bytearray(tile_c) if tile_c else None, bytearray(tile_n) if tile_n else None)
        rows.append(dict(kind="extracted", size=S, made=made, files=files, before=before, after=after, at=(x0, y0),
                         thumb=(tile_c or tile_n, S, tile_c is None)))
        say(f"   extracted {S} px at ({x0}, {y0}): {made}; wrap ratio "
            + (f"{before[0]:.2f}/{before[1]:.2f} -> {after[0]:.2f}/{after[1]:.2f}" if after else "as in the game") + f"; {files}")
        # the sample for synthesis: the raw clean window (S x S), colour + game normal interleaved (8 channels)
        Ssm = S
        if name == "skin_pores_detail":
            sample_c, sample_n = None, nrm[3]
        else:
            sample_c = T1.crop(col[3], W, x0, y0, S, S) if col else None
            sample_n = T1.crop(nrm[3], W, x0, y0, S, S) if same else None
        chans = 4 * ((sample_c is not None) + (sample_n is not None))
        sample = bytearray(Ssm * Ssm * chans)
        for i in range(Ssm * Ssm):
            o = i * chans
            if sample_c is not None:
                sample[o:o + 4] = sample_c[4 * i:4 * i + 4]
                o += 4
            if sample_n is not None:
                sample[o:o + 4] = sample_n[4 * i:4 * i + 4]
        for N in SIZES:
            t1 = time.time()
            q = quilt(sample, Ssm, chans, N, seed=zlib.crc32(f"{name}/{N}".encode()) & 0xFFFF)
            qc = to_rgba(q, N * N, chans, 0) if sample_c is not None else None
            qn = to_rgba(q, N * N, chans, 4 if sample_c is not None else 0) if sample_n is not None else None
            wr = T1.wrap_ratio(qc or qn, N, N, [0, 1, 2] if qc else [3, 1])
            files = write_maps(name, group, "synth", N, bytearray(qc) if qc else None, bytearray(qn) if qn else None)
            rows.append(dict(kind="synthesised", size=N, made=f"synthesised from the {S} px sample (patch quilting)", files=files,
                             before=None, after=wr, thumb=(qc or qn, N, qc is None)))
            say(f"   synthesised {N} px: wrap ratio {wr[0]:.2f}/{wr[1]:.2f}; {time.time() - t1:.0f} s; {files}")
        entries.append(dict(name=name, group=group, note=note, rows=rows, secs=time.time() - t0))
        say(f"   {name}: {time.time() - t0:.0f} s")
    total = time.time() - t_all
    say(f"\ntotal generation time: {total:.0f} s ({total / 60:.1f} min)")
    readme(entries, total)
    log.close()


QUALITY = {"metal_shield": "poor: the shield plate is small and carries a rivet pattern; the synthesised tiles repeat it",
           "cloth_shirt": "a printed pattern: the synthesised tiles break the pattern into patches",
           "skin_pores_detail": "the game's own tiling pore map; the synthesised tiles are larger variations of it"}


def readme(entries, total):
    with open(os.path.join(LIB, "README.md"), "w", encoding="utf-8") as f:
        f.write("# Amicia Seamless Textures\n\nTiling textures made on your computer from your own game files (A Plague Tale: "
                "Innocence, Amicia). They are not part of the tool and must not be shared. Every map is its own PNG.\n\n"
                "**Two kinds, marked in the file name:**\n"
                "- `_extracted_`: a clean area cut from the game's texture, its edges blended so it repeats.\n"
                "- `_synth_`: **synthesised**: a larger tile assembled from small pieces of that same clean area (patch "
                "quilting), at the game's own texel size; it repeats seamlessly by construction. Up close it can show "
                "soft blends between pieces.\n\n"
                "**Use in Blender:** *_colour -> Base Color (sRGB); *_normal -> Normal Map node (OpenGL, Non-Color); "
                "*_roughness -> Roughness (Non-Color); *_metal -> Metallic (Non-Color). The number is the tile size in "
                "pixels; a 2048 synth tile covers four times the area of a 512 one at the same sharpness. Bake into your "
                "model's own texture before export.\n\n| Texture | From | Files | Notes |\n|---|---|---|---|\n")
        for e in entries:
            if not e["rows"]:
                f.write(f"| {e['group']}/{e['name']} | {e['note']} | none | {e['skipped']} |\n")
                continue
            kinds = "; ".join(f"{r['kind']} {r['size']}" for r in e["rows"])
            f.write(f"| {e['group']}/{e['name']} | {e['note']} | {kinds} | {QUALITY.get(e['name'], '')} |\n")
        f.write("\nMade from your own game files by the tool.\n")


def sheet(entries):
    os.makedirs(REV, exist_ok=True)
    C = 192
    rows = [e for e in entries if e["rows"]]
    cols = 4   # extracted (2 x 2), synth 512, 1024, 2048 (each scaled to C)
    W, H = cols * (C + 4) + 4, len(rows) * (C + 4) + 4
    img = bytearray([40, 40, 40, 255] * (W * H))
    for r, e in enumerate(rows):
        for k, row in enumerate(e["rows"][:4]):
            t, S, is_n = row["thumb"]
            reps = 2 if k == 0 else 1
            for y in range(C):
                for x in range(C):
                    sx, sy = (x * reps * S // C) % S, (y * reps * S // C) % S
                    o = 4 * (sy * S + sx)
                    px = (t[o + 3], 255 - t[o + 1], 255) if is_n else (t[o], t[o + 1], t[o + 2])
                    d = 4 * ((4 + r * (C + 4) + y) * W + 4 + k * (C + 4) + x)
                    img[d:d + 3] = bytes(px)
    p = os.path.join(REV, "library_sheet.png")
    tc.png_write(p + ".tmp", W, H, img)
    fsx.replace(p + ".tmp", p)
    with open(os.path.join(REV, "library_sheet.md"), "w", encoding="utf-8") as f:
        f.write("# Seamless texture library: contact sheet (review only, not in the library)\n\nlibrary_sheet.png: one row per "
                "texture (colour, or the normal for the pore map); columns: the extracted tile repeated 2 x 2 (a seam would "
                "show as a cross in the middle), then the synthesised 512, 1024 and 2048 tiles, each shrunk to the same "
                "square (so the 2048 one shows the most repeats of the material's grain).\n\n"
                "| Row | Texture | Extracted | Wrap edge (1.0 = invisible): extracted, synth 512 / 1024 / 2048 |\n|---|---|---|---|\n")
        for r, e in enumerate(rows, 1):
            ex = e["rows"][0]
            vals = [f"{x['after'][0]:.2f}/{x['after'][1]:.2f}" if x["after"] else "game" for x in e["rows"]]
            f.write(f"| {r} | {e['group']}/{e['name']} | {ex['size']} px | {', '.join(vals)} |\n")
        for e in entries:
            if not e["rows"]:
                f.write(f"\nNot made: {e['name']}: {e['skipped']}\n")


if __name__ == "__main__":
    main()
