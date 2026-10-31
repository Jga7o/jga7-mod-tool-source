"""TEST ONLY (M15 geometry test): a copy of a hair glb whose UVs are moved onto strand areas of the game's hair atlas B01,
so a hair made for its own texture is visible with the game's see-through strand material. The source glb is never
written; only the copy's TEXCOORD_0 (and TANGENT w of flipped islands, if the glb has tangents) differ.

Mode "islands" (default): each UV island (connected triangles) is shifted and uniformly scaled (0.75 / 1.0 / 1.25, no
rotation), optionally flipped vertically, onto the B01 area whose alpha best matches the island's own alpha in the glb's
own colour texture (as hairmock.nape_map did for chapter I's nape). Coarse search on 128 x 128 mean-alpha maps, then a
fine search at full B01 resolution. Islands with identical UVs (stacked cards) share one result.
Mode "strip": the whole mesh's UV box is mapped linearly onto B01's dense strand strip (u 0.40-0.57); used automatically
when there are more than MAX_ISLANDS distinct islands or the glb has no colour texture.
Report (json): per island the placement, the mean alpha difference and the drawn share (alpha >= 128) before and after.
Usage: python -I hairuvmap.py <in.glb> <out.glb> [--object hair] [--mode islands|strip] [--report <json>]
"""
import argparse
import json
import os
import random
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paths  # noqa: E402
import fsx  # noqa: E402  (renames with a retry: Windows may hold a new file)
import gltfread  # noqa: E402
import hairedit  # noqa: E402
import texcodec  # noqa: E402
import texlib  # noqa: E402
from hairmock import islands  # noqa: E402

OUT = paths.OUT
C = 128                                 # coarse map size
SCALES = (0.75, 1.0, 1.25)
STRIP = (0.40, 0.57)                    # B01's densest strand strip (u), full height
MAX_ISLANDS = 3000


def coarse(img, w, h):
    """C x C mean alpha."""
    bw, bh = w // C, h // C
    out = [0.0] * (C * C)
    for y in range(h):
        row = 4 * y * w + 3
        cy = (y // bh) * C
        for cx in range(C):
            out[cy + cx] += sum(img[row + 4 * x] for x in range(cx * bw, cx * bw + bw, 2))
    n = bw * bh / 2
    return [v / n for v in out]


def own_image(g, prim):
    """(w, h, rgba) of the primitive material's base colour texture, or None."""
    try:
        mat = g.doc["materials"][prim["material"]]
        tex = g.doc["textures"][mat["pbrMetallicRoughness"]["baseColorTexture"]["index"]]
        im = g.doc["images"][tex["source"]]
    except (KeyError, IndexError, TypeError):
        return None
    if "bufferView" in im:
        bv = g.doc["bufferViews"][im["bufferView"]]
        o = bv.get("byteOffset", 0)
        data = g.bins[bv.get("buffer", 0)][o:o + bv["byteLength"]]
    else:
        return None
    tmp = os.path.join(os.environ.get("TEMP", HERE), f"hairuvmap_{os.getpid()}.png")
    open(tmp, "wb").write(data)
    try:
        w, h, img, _ = texcodec.png_read(tmp)
    finally:
        fsx.remove(tmp)
    return w, h, img


def samples(isl, U, rnd, n):
    pts = []
    for _ in range(n):
        t = isl[rnd.randrange(len(isl))]
        r1, r2 = rnd.random(), rnd.random()
        if r1 + r2 > 1:
            r1, r2 = 1 - r1, 1 - r2
        pts.append(tuple(U[t[0]][k] + r1 * (U[t[1]][k] - U[t[0]][k]) + r2 * (U[t[2]][k] - U[t[0]][k]) for k in range(2)))
    return pts


def match(pts, box, own_c, own_f, b01_c, b01_f, wo, ho, wb, hb):
    """(score, scale, a, b, flip): island box corner (u0, v0) goes to (a, b)."""
    u0, v0, wu, hv = box
    rel = [(x - u0, y - v0) for x, y in pts]
    ac = [own_c[min(C - 1, int(y % 1 * C)) * C + min(C - 1, int(x % 1 * C))] for x, y in pts[:48]]
    best = []
    for fl in (False, True):
        for s in SCALES:
            if s * wu > 1 or s * hv > 1:
                continue
            nx, ny = int((1 - s * wu) * C), int((1 - s * hv) * C)
            d = [(int(s * x * C), int(s * ((hv - y) if fl else y) * C)) for x, y in rel[:48]]
            for oy in range(0, ny + 1, 2):
                for ox in range(0, nx + 1, 2):
                    sc = sum(abs(a - b01_c[min(C - 1, oy + dy) * C + min(C - 1, ox + dx)]) for a, (dx, dy) in zip(ac, d))
                    best.append((sc / len(ac), s, ox / C, oy / C, fl))
    best.sort()
    af = [own_f[4 * (min(ho - 1, int(y % 1 * ho)) * wo + min(wo - 1, int(x % 1 * wo))) + 3] for x, y in pts]

    def fine(s, a, b, fl):
        t = 0
        for (x, y), al in zip(rel, af):
            u, v = a + s * x, b + s * ((hv - y) if fl else y)
            t += abs(al - b01_f[4 * (min(hb - 1, int(v * hb)) * wb + min(wb - 1, int(u * wb))) + 3])
        return t / len(af)
    out = []
    for _, s, a, b, fl in best[:6]:
        for da in range(-4, 5):
            for db in range(-4, 5):
                aa, bb = a + da / 512, b + db / 512
                if 0 <= aa <= 1 - s * wu and 0 <= bb <= 1 - s * hv:
                    out.append((fine(s, aa, bb, fl), s, aa, bb, fl))
    return min(out), af


def write_copy(src, dst, g, prim, newuv, flipped):
    """Copy of src with TEXCOORD_0 replaced (and TANGENT w negated for flipped vertices); every other byte unchanged."""
    raw = bytearray(open(src, "rb").read())
    jl = struct.unpack_from("<I", raw, 12)[0]
    binstart = 28 + jl
    for name in ("TEXCOORD_0", "TANGENT"):
        if name not in prim["attributes"]:
            continue
        acc = g.doc["accessors"][prim["attributes"][name]]
        if acc["componentType"] != 5126:
            raise ValueError(f"{name} is not float: export without quantization")
        bv = g.doc["bufferViews"][acc["bufferView"]]
        nc = 2 if name == "TEXCOORD_0" else 4
        stride = bv.get("byteStride", 4 * nc)
        base = binstart + bv.get("byteOffset", 0) + acc.get("byteOffset", 0)
        for i in range(acc["count"]):
            if name == "TEXCOORD_0":
                struct.pack_into("<2f", raw, base + i * stride, *newuv[i])
            elif i in flipped:
                o = base + i * stride + 12
                struct.pack_into("<f", raw, o, -struct.unpack_from("<f", raw, o)[0])
    open(dst + ".tmp", "wb").write(raw)
    fsx.replace(dst + ".tmp", dst)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src"); ap.add_argument("dst")
    ap.add_argument("--object", default="hair"); ap.add_argument("--mode", default="islands", choices=("islands", "strip"))
    ap.add_argument("--report")
    a = ap.parse_args()
    if os.path.abspath(a.src) == os.path.abspath(a.dst):
        sys.exit("hairuvmap: the output must be a copy, never the source glb")
    g = gltfread.load(a.src)
    ni = g.find_node(a.object)
    prims = g.doc["meshes"][g.doc["nodes"][ni]["mesh"]]["primitives"]
    if len(prims) != 1:
        sys.exit(f"hairuvmap: {len(prims)} primitives (materials); the hair must have one")
    prim = prims[0]
    U = [tuple(x[:2]) for x in g.accessor(prim["attributes"]["TEXCOORD_0"])]
    idx = [x[0] for x in g.accessor(prim["indices"])]
    tris = [tuple(idx[i:i + 3]) for i in range(0, len(idx), 3)]
    isls = islands(tris, len(U))
    groups = {}
    for isl in isls:
        vids = sorted({i for t in isl for i in t})
        key = tuple(sorted((round(U[i][0], 4), round(U[i][1], 4)) for i in vids))
        groups.setdefault(key, []).append((isl, vids))
    img = own_image(g, prim)
    mode = a.mode
    why = ""
    if mode == "islands" and img is None:
        mode, why = "strip", "no own colour texture in the glb"
    elif mode == "islands" and len(groups) > MAX_ISLANDS:
        mode, why = "strip", f"{len(groups)} distinct islands (more than {MAX_ISLANDS})"
    wb, hb, _, b01 = texlib.load(os.path.join(OUT, "P_AMICIA_unpacked", "resources"), hairedit.B01)
    newuv, flipped, rep = list(U), set(), []
    if mode == "strip":
        u0, u1 = min(u for u, _ in U), max(u for u, _ in U)
        v0, v1 = min(v for _, v in U), max(v for _, v in U)
        for i, (u, v) in enumerate(U):
            newuv[i] = (STRIP[0] + (u - u0) / max(1e-9, u1 - u0) * (STRIP[1] - STRIP[0]), 0.01 + (v - v0) / max(1e-9, v1 - v0) * 0.98)
    else:
        wo, ho, own = img
        own_c, b01_c = coarse(own, wo, ho), coarse(b01, wb, hb)
        rnd = random.Random(15)
        for n, (key, members) in enumerate(groups.items()):
            isl, vids = members[0]
            box = (min(U[i][0] for i in vids), min(U[i][1] for i in vids))
            box += (max(U[i][0] for i in vids) - box[0], max(U[i][1] for i in vids) - box[1])
            pts = samples(isl, U, rnd, 200)
            (sc, s, aa, bb, fl), af = match(pts, box, own_c, own, b01_c, b01, wo, ho, wb, hb)
            for _, vv in members:
                for i in vv:
                    x, y = U[i][0] - box[0], U[i][1] - box[1]
                    newuv[i] = (aa + s * x, bb + s * ((box[3] - y) if fl else y))
                    if fl:
                        flipped.add(i)
            after = [b01[4 * (min(hb - 1, int((bb + s * ((box[3] - (y - box[1])) if fl else (y - box[1]))) * hb)) * wb
                              + min(wb - 1, int((aa + s * (x - box[0])) * wb))) + 3] for x, y in pts]
            rep.append(dict(copies=len(members), vertices=len(vids), uv_box=[round(x, 4) for x in box], scale=s, flipped_v=fl,
                            to=[round(aa, 4), round(bb, 4)], mean_abs_alpha_diff=round(sc, 1),
                            drawn_own=round(sum(x >= 128 for x in af) / len(af), 2),
                            drawn_b01=round(sum(x >= 128 for x in after) / len(after), 2)))
            if n % 50 == 49:
                print(f"  {n + 1} / {len(groups)} island groups placed", flush=True)
    write_copy(a.src, a.dst, g, prim, newuv, flipped)
    summary = dict(source=os.path.abspath(a.src), output=os.path.abspath(a.dst), mode=mode, why=why, islands=len(isls),
                   distinct_islands=len(groups), vertices=len(U), flipped_vertices=len(flipped))
    if rep:
        wsum = sum(r["vertices"] * r["copies"] for r in rep)
        for k in ("mean_abs_alpha_diff", "drawn_own", "drawn_b01"):
            summary[k] = round(sum(r[k] * r["vertices"] * r["copies"] for r in rep) / wsum, 2)
    if a.report:
        json.dump(dict(summary=summary, islands=rep), open(a.report, "w"), indent=1)
    print("hairuvmap (TEST ONLY, UVs onto B01):", json.dumps(summary))


if __name__ == "__main__":
    main()
