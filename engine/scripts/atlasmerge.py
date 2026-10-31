"""Merge outfit pieces with DIFFERENT textures into one mesh in the host slot, with one atlas (M14 rehearsal).

Spec (JSON, e.g. output\\mod\\atlas1_spec.json): "host" (slot label, its mesh = template, its material = the result's),
"atlas_size" (colour/normal, 2048), "mask_size" (256), "margin" (gutter in atlas texels), "host_mult" (the host
material's colour multiplier), "mask_neutral" (mask R where a group has no host-style mask), optional "reserve" (slot
labels of kept pieces that share the host's textures: their UV boxes stay free), and "groups": each with
"pieces" (slot labels), "colour", "normal", "mask" (bitmap labels of P_AMICIA, mask may be null) and "mult" (the
group's own colour multiplier). Conventions: notes\\host_material.md.

Method:
1. UV islands (triangles sharing vertices) per group; the island boxes in source texel units (unwrapped: a tiled texture
   keeps its repeats). Islands of one group that cover the same texels (8-texel grid; mirrored or stacked islands) are
   united, so shared texels are baked once; other boxes may overlap in the source and are baked separately.
2. A scale for every box (atlas texels per source texel; x the group's optional "density"), the largest for which a shelf packer fits all boxes plus
   gutters into the atlas; boxes start on 4-texel boundaries (BC blocks). No rotation: tangents stay valid.
3. Bake: every atlas texel of a box (gutter included) samples the source bilinearly at the matching source position
   (wrapping, like the game's sampler). Colour x (group mult / host mult) in linear light; normal channels copied
   (same layout in every group); mask R from the group's mask (host style) or mask_neutral, G = B = 255.
   Unused atlas texels: colour = mean of the baked texels, normal (0, 128, 255, 128) = flat (the engine's own flat map),
   mask neutral.
4. Mesh: the pieces' vertices byte for byte, except the UV (f16 x 2 at +20) = (box position + (uv x source size - box
   origin) x s) / atlas size; indices offset; one submesh; host template; bounds recomputed (mergemesh.assemble).
Outputs in --out: <host>_atlas.Mesh_Z, game_colour.png, game_normal.png, game_mask.png, atlas_report.json.
Usage: python -I atlasmerge.py <spec.json> --res <unpacked resources> --out <dir>
"""
import argparse
import json
import math
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paths  # noqa: E402
import mergemesh  # noqa: E402
import texcodec as tc  # noqa: E402
import texlib  # noqa: E402
import uvraster  # noqa: E402
from meshfmt import parse  # noqa: E402
from meshio import write_bounds  # noqa: E402

OUT = paths.OUT
LIN = [((i / 255) / 12.92 if i / 255 <= 0.04045 else ((i / 255 + 0.055) / 1.055) ** 2.4) for i in range(256)]


def to_srgb(x):
    x = max(0.0, min(1.0, x))
    return round(255 * (12.92 * x if x <= 0.0031308 else 1.055 * x ** (1 / 2.4) - 0.055))


def labels():
    bm = {l.split("\t")[0]: int(l.split("\t")[1]) for l in open(os.path.join(paths.TABLES, "previews", "P_AMICIA_bitmaps.tsv")) if not l.startswith("label")}
    ms = {r.split("\t")[0]: int(r.split("\t")[1]) for r in open(os.path.join(paths.TABLES, "analysis", "P_AMICIA_parts.tsv")).read().split("\n")[1:] if r}
    return bm, ms


def unite(boxes, gap):
    """Merge boxes [x0, y0, x1, y1, members] that overlap or lie within gap of each other."""
    boxes = [list(b) for b in boxes]
    changed = True
    while changed:
        changed = False
        out = []
        for b in boxes:
            for o in out:
                if b[0] < o[2] + gap and o[0] < b[2] + gap and b[1] < o[3] + gap and o[1] < b[3] + gap:
                    o[0], o[1], o[2], o[3] = min(o[0], b[0]), min(o[1], b[1]), max(o[2], b[2]), max(o[3], b[3])
                    o[4] = o[4] + b[4]
                    changed = True
                    break
            else:
                out.append(b)
        boxes = out
    return boxes


def unite_cov(boxes):
    """Merge islands whose coverage cells (8-texel grid) overlap; the box is the union of their boxes."""
    boxes = [list(b) for b in boxes]
    changed = True
    while changed:
        changed = False
        out = []
        for b in boxes:
            for o in out:
                if b[0] <= o[2] and o[0] <= b[2] and b[1] <= o[3] and o[1] <= b[3] and not b[5].isdisjoint(o[5]):
                    o[0], o[1], o[2], o[3] = min(o[0], b[0]), min(o[1], b[1]), max(o[2], b[2]), max(o[3], b[3])
                    o[4] = o[4] + b[4]
                    o[5] = o[5] | b[5]
                    o[6] += b[6]
                    o[7] += b[7]
                    changed = True
                    break
            else:
                out.append(b)
        boxes = out
    return boxes


def pack(sizes, W, blocked=()):
    """Skyline bottom-left packing of (w, h) sizes (multiples of 4) into W x W, avoiding the blocked rectangles
    (x0, y0, x1, y1); returns positions or None."""
    order = sorted(range(len(sizes)), key=lambda i: (-sizes[i][1], -sizes[i][0]))
    sky = [[0, 0, W]]   # segments [x, y, width], left to right
    pos = [None] * len(sizes)
    for i in order:
        w, h = sizes[i]
        best = None
        for k in range(len(sky)):
            x = sky[k][0]
            if x + w > W:
                break
            y, rem, j = 0, w, k
            while rem > 0:
                y = max(y, sky[j][1])
                rem -= sky[j][2]
                j += 1
            moved = True
            while moved:   # step below any blocked rectangle in the way
                moved = False
                for bx0, by0, bx1, by1 in blocked:
                    if x < bx1 and bx0 < x + w and y < by1 and by0 < y + h:
                        y, moved = by1, True
            if y + h <= W and (best is None or (y + h, x) < (best[0] + h, best[1])):
                best = (y, x, k)
        if best is None:
            return None
        y, x, k = best
        pos[i] = (x, y)
        new = [x, y + h, w]
        out = []
        for seg in sky:
            sx, sy, sw = seg
            if sx + sw <= x or sx >= x + w:
                out.append(seg)
                continue
            if sx < x:
                out.append([sx, sy, x - sx])
            if sx + sw > x + w:
                out.append([x + w, sy, sx + sw - x - w])
        out.append(new)
        out.sort()
        merged = []
        for seg in out:
            if merged and merged[-1][1] == seg[1] and merged[-1][0] + merged[-1][2] == seg[0]:
                merged[-1][2] += seg[2]
            else:
                merged.append(seg)
        sky = merged
    return pos


def r4(v):
    return int(math.ceil(v / 4.0)) * 4


def build(spec, res):
    bm, ms = labels()
    A, M, m = spec["atlas_size"], spec["mask_size"], spec["margin"]
    pieces = []   # dict(label, group, bytes, parse, uv, tris)
    boxes = []    # [x0, y0, x1, y1, [(piece index, island vertex set)], group index, source size]
    for gi, g in enumerate(spec["groups"]):
        src = texlib.info(os.path.join(res, f"{bm[g['colour']]}.Bitmap_Z"))
        S = src["w"]
        gboxes = []
        for lab in g["pieces"]:
            b = open(os.path.join(res, f"{ms[lab]}.Mesh_Z"), "rb").read()
            p = parse(b)
            if len(p["vbs"]) != 1 or p["vbs"][0]["flags"] != 2 or len(p["ibs"]) != 1 or len(p["subs"]) != 1 or p["vbs"][0]["stride"] != 48:
                raise ValueError(f"{lab}: not a single-block skinned (stride 48) mesh")
            uv, tris = uvraster.mesh_uv(b)
            pi = len(pieces)
            pieces.append(dict(label=lab, group=gi, b=b, m=p, uv=uv))
            used = set()
            for ts, verts in uvraster.islands(uv, tris):
                us = [uv[v][0] * S for v in verts]
                vs = [uv[v][1] * S for v in verts]
                cells = set()   # coverage on an 8-texel grid (unwrapped), to unite islands that share texels

                def emit(x, y, *_):
                    cells.add((x, y))
                for k in ts:
                    uvraster.raster([(uv[i][0] * S / 8, uv[i][1] * S / 8) for i in tris[k]], 0, 0, emit)
                    for i in tris[k]:
                        emit(int(uv[i][0] * S / 8 // 1), int(uv[i][1] * S / 8 // 1))
                a3 = at = 0.0   # 3D area (m^2) and UV area (source texels^2): texel density of the island
                for k in ts:
                    P = [struct.unpack_from("<3f", b, p["vbs"][0]["pos"] + 48 * i) for i in tris[k]]
                    e1 = [P[1][j] - P[0][j] for j in range(3)]
                    e2 = [P[2][j] - P[0][j] for j in range(3)]
                    cr = (e1[1] * e2[2] - e1[2] * e2[1], e1[2] * e2[0] - e1[0] * e2[2], e1[0] * e2[1] - e1[1] * e2[0])
                    a3 += math.sqrt(sum(c * c for c in cr)) / 2
                    q = [(uv[i][0] * S, uv[i][1] * S) for i in tris[k]]
                    at += abs((q[1][0] - q[0][0]) * (q[2][1] - q[0][1]) - (q[2][0] - q[0][0]) * (q[1][1] - q[0][1])) / 2
                gboxes.append([min(us), min(vs), max(us), max(vs), [(pi, verts)], cells, a3, at])
                used |= verts
            unused = set(range(p["vbs"][0]["count"])) - used   # vertices of no triangle: keep them in the first box
            pieces[-1]["unused"] = unused
        for bx in unite_cov(gboxes):
            dens = math.sqrt(bx[7] / bx[6]) if bx[6] > 1e-9 and bx[7] > 0 else 0.0   # source texels per metre
            boxes.append(bx[:5] + [gi, S, 1.0, dens, g.get("density", 1.0)])
            # [x0, y0, x1, y1, members, group, size, scale (set below), texels per metre, group weight]
    # scale: the largest s that packs (binary search)
    # reserved areas (spec "reserve": kept pieces that share the host's textures keep their texels; the boxes avoid
    # the UV boxes of their islands, 4-aligned with a 4-texel border)
    blocked = []
    for lab in spec.get("reserve", []):
        uv, tris = uvraster.mesh_uv(open(os.path.join(res, f"{ms[lab]}.Mesh_Z"), "rb").read())
        for ts, verts in uvraster.islands(uv, tris):
            us = [uv[v][0] * A for v in verts]
            vs = [uv[v][1] * A for v in verts]
            blocked.append((max(0, int(min(us)) // 4 * 4 - 4), max(0, int(min(vs)) // 4 * 4 - 4),
                            min(A, r4(max(us)) + 4), min(A, r4(max(vs)) + 4)))
    # equal on-body texel density: every box keeps its own density up to a common cap D (texels per metre in the atlas);
    # denser boxes are scaled down to D. The largest D that packs is used.
    def scales(D):
        return [min(1.0, D * b[9] / b[8]) if b[8] > 0 else 1.0 for b in boxes]

    def attempt(D):
        sc = scales(D)
        sizes = [(r4((b[2] - b[0]) * k + 2 * m), r4((b[3] - b[1]) * k + 2 * m)) for b, k in zip(boxes, sc)]
        pos = pack(sizes, A, blocked)
        return (D, sc, sizes, pos) if pos else None
    top = max(b[8] for b in boxes) / min(b[9] for b in boxes)
    best = attempt(top)
    lo, hi = 1.0, top
    for _ in range(30 if not best else 0):
        D = math.sqrt(lo * hi)
        r = attempt(D)
        if r:
            best, lo = r, D
        else:
            hi = D
    if not best:
        raise ValueError("the islands do not fit the atlas")
    D, sc, sizes, pos = best
    for b, k in zip(boxes, sc):
        b[7] = k
    s = D
    # vertex -> box
    vbox = {}
    for k, b in enumerate(boxes):
        for pi, verts in b[4]:
            for v in verts:
                vbox[(pi, v)] = k
    for pi, p in enumerate(pieces):
        for v in p["unused"]:
            vbox[(pi, v)] = next(k for k, b in enumerate(boxes) if b[5] == p["group"])
    # new UVs and the merged mesh
    host = spec["host"]
    tb = open(os.path.join(res, f"{ms[host]}.Mesh_Z"), "rb").read()
    tm = parse(tb)
    if pieces[0]["label"] != host:
        raise ValueError("the host must be the first piece of the first group")
    verts, idx, base, prep = bytearray(), [], 0, []
    for pi, p in enumerate(pieces):
        vb, ib = p["m"]["vbs"][0], p["m"]["ibs"][0]
        raw = bytearray(p["b"][vb["pos"]:vb["pos"] + vb["count"] * 48])
        for v in range(vb["count"]):
            k = vbox[(pi, v)]
            b, (ax, ay) = boxes[k], pos[k]
            u, w = p["uv"][v]
            S = b[6]
            nu = (ax + m + (u * S - b[0]) * b[7]) / A
            nv = (ay + m + (w * S - b[1]) * b[7]) / A
            struct.pack_into("<2e", raw, 20 + 48 * v, nu, nv)
        verts += raw
        idx += [x + base for x in struct.unpack_from(f"<{ib['count']}H", p["b"], ib["pos"])]
        prep.append(dict(label=p["label"], group=spec["groups"][p["group"]]["name"], first_vertex=base, vertices=vb["count"],
                         triangles=ib["count"] // 3))
        base += vb["count"]
    mesh = bytearray(mergemesh.assemble(tb, tm, bytes(verts), idx))
    write_bounds(mesh, [struct.unpack_from("<3f", verts, k * 48) for k in range(base)])
    # bake
    col = bytearray(A * A * 4)
    nrm = bytearray(bytes((0, 128, 255, 128)) * (A * A))
    msk = bytearray(bytes((spec["mask_neutral"], 255, 255, 255)) * (M * M))
    filled = bytearray(A * A)
    hm = spec["host_mult"]
    for gi, g in enumerate(spec["groups"]):
        S = None
        cw, ch, _, cimg = texlib.load(res, bm[g["colour"]])
        nw, nh, _, nimg = texlib.load(res, bm[g["normal"]])
        mk = texlib.load(res, bm[g["mask"]]) if g.get("mask") else None
        f = g["mult"] / hm
        lut = [to_srgb(LIN[v] * f) for v in range(256)] if abs(f - 1) > 1e-6 else None
        for k, b in enumerate(boxes):
            if b[5] != gi:
                continue
            S = b[6]
            (ax, ay), (bw, bh) = pos[k], sizes[k]
            sb = b[7]
            for ty in range(ay, min(A, ay + bh)):
                sy = b[1] + (ty + 0.5 - ay - m) / sb          # source texel units
                for tx in range(ax, min(A, ax + bw)):
                    sx = b[0] + (tx + 0.5 - ax - m) / sb
                    u, v = sx / S, sy / S
                    c = texlib.bilinear(cimg, cw, ch, u, v)
                    n = texlib.bilinear(nimg, nw, nh, u, v)
                    o = 4 * (ty * A + tx)
                    for q in range(3):
                        val = max(0, min(255, round(c[q])))
                        col[o + q] = lut[val] if lut else val
                    col[o + 3] = 255
                    nrm[o:o + 4] = bytes(max(0, min(255, round(x))) for x in n)
                    filled[ty * A + tx] = 1
            # mask: atlas mask texels whose centre falls in this box
            r = A // M
            for my in range(ay // r, min(M, (ay + bh) // r + 1)):
                cy = (my + 0.5) * r
                if not ay <= cy < ay + bh:
                    continue
                for mx in range(ax // r, min(M, (ax + bw) // r + 1)):
                    cx = (mx + 0.5) * r
                    if not ax <= cx < ax + bw:
                        continue
                    if mk:
                        u = (b[0] + (cx - ax - m) / sb) / S
                        v = (b[1] + (cy - ay - m) / sb) / S
                        val = texlib.bilinear(mk[3], mk[0], mk[1], u, v)[0]
                        msk[4 * (my * M + mx)] = max(0, min(255, round(val)))
        del cimg, nimg
    n = sum(filled)
    mean = [round(sum(col[4 * i + q] for i in range(A * A) if filled[i]) / max(1, n)) for q in range(3)]
    for i in range(A * A):
        if not filled[i]:
            col[4 * i:4 * i + 4] = bytes(mean + [255])
    loss = {}
    for b in boxes:
        for pi, verts in b[4]:
            lab = pieces[pi]["label"]
            w = (b[2] - b[0]) * (b[3] - b[1]) / max(1, len(b[4]))
            loss.setdefault(lab, [0.0, 0.0, b[8]])
            loss[lab][0] += w * b[7]
            loss[lab][1] += w
    piece_scale = {k: round(v[0] / v[1], 3) if v[1] else None for k, v in loss.items()}
    for x in prep:
        x["scale"] = piece_scale.get(x["label"])
        x["source_texels_per_m"] = round(loss.get(x["label"], [0, 0, 0])[2])
    rep = dict(reserved=blocked, density_cap_texels_per_m=round(s, 1), scale_min=round(min(b[7] for b in boxes), 3), densities={g["name"]: g.get("density", 1.0) for g in spec["groups"]}, atlas_fill=round(n / (A * A), 4), boxes=[dict(group=spec["groups"][b[5]]["name"], source=[round(x, 1) for x in b[:4]],
                                                                       atlas=list(pos[k]) + list(sizes[k]), scale=round(b[7], 3), texels_per_m=round(b[8]), members=sorted({pieces[pi]["label"] for pi, _ in b[4]}))
                                                                  for k, b in enumerate(boxes)],
               pieces=prep, vertices=base, triangles=len(idx) // 3, mean_fill_colour=mean)
    return bytes(mesh), col, nrm, msk, rep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("spec")
    ap.add_argument("--res", default=os.path.join(OUT, "P_AMICIA_unpacked", "resources"))
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    spec = json.load(open(a.spec))
    os.makedirs(a.out, exist_ok=True)
    try:
        mesh, col, nrm, msk, rep = build(spec, a.res)
    except ValueError as e:
        sys.exit(f"ERROR: {e}")
    A, M = spec["atlas_size"], spec["mask_size"]
    open(os.path.join(a.out, f"{spec['host']}_atlas.Mesh_Z"), "wb").write(mesh)
    tc.png_write(os.path.join(a.out, "game_colour.png"), A, A, col)
    tc.png_write(os.path.join(a.out, "game_normal.png"), A, A, nrm, alpha=True)
    tc.png_write(os.path.join(a.out, "game_mask.png"), M, M, msk)
    json.dump(rep, open(os.path.join(a.out, "atlas_report.json"), "w"), indent=1)
    print(f"merged {len(rep['pieces'])} pieces: {rep['vertices']} vertices, {rep['triangles']} triangles; {len(rep['boxes'])} "
          f"boxes; density cap {rep['density_cap_texels_per_m']} texels/m; atlas filled {100 * rep['atlas_fill']:.1f} %")
    for x in rep["pieces"]:
        print(f"   {x['label']:4s} {x['group']:15s} {x['vertices']:5d} v {x['triangles']:5d} t; source {x['source_texels_per_m']:5d} texels/m; "
              f"linear scale {x['scale']}")


if __name__ == "__main__":
    main()
