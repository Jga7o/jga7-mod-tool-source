"""M15 mock rehearsal: an archive's own hair as ONE Blender object "hair" (glb on the kit armature), for the "hair" edit.

Every hair piece of the archive (hairedit.hair_set) is copied with its game positions, normals, tangents, UVs and vertex
colours. Weights: skinned pieces keep theirs; rigid pieces (one bone each) keep their own bone with weight 1, except the
head attachment bones (kit 269-282, which carry a -1 / 0.711 scale in their bind matrix, and CHP1's own 279 / 280 that
no other skeleton has): those pieces go to "head", as a user's hair would. Bones are mapped to the kit armature by
64-bit name hash (skeleton record +296), never by recovered text names. So the braid tips that are rigid on hair5 in
CHP2 / END (C2-18, C2-25, E-12, E-26) stay on hair5 (session 38's mock put them on head: static tips in game).
Differences from the original: the attachment-bone locks no longer follow their bones (animated in about 30 % of the
animations), and every piece gets the host's material (shader flags 0x...802 instead of 0x...81A on 10 base pieces).
CHP1's nape piece C1-20 draws 1-00, which the hair edit overwrites with B01. Each of its UV islands is moved (shift +
uniform scale 0.6-1.5, optional vertical flip with the tangent handedness flipped, no rotation) onto the B01 area whose alpha best matches the island's own 1-00 alpha at the same
points (nape_map), so its see-through fade survives; its colours become B01's. Report: <out>.nape.json.
Usage: python -I hairmock.py <archive> [<out.glb>]        (default output\\mod\\hairmock\\hairmock_<archive>.glb)
"""
import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paths  # noqa: E402
import fsx  # noqa: E402  (renames with a retry: Windows may hold a new file)
import hairedit  # noqa: E402
import skel  # noqa: E402
import texlib  # noqa: E402
from blender_kit import GLB, SKEL_ID, add_mesh, add_skeleton, bone_names, load_mesh  # noqa: E402

OUT = paths.OUT
HEAD = 129                                  # P_AMICIA "head"
ATTACH = range(269, 283)                    # kit head-attachment bones of the rigid locks


def islands(tris, n):
    par = list(range(n))

    def f(a):
        while par[a] != a:
            par[a] = par[par[a]]
            a = par[a]
        return a
    for a, b, c in tris:
        par[f(b)] = f(a)
        par[f(c)] = f(a)
    out = {}
    for t in tris:
        out.setdefault(f(t[0]), []).append(t)
    return list(out.values())


def nape_map(vs, tris, res, report):
    """{vertex index: new uv}: each UV island shifted / scaled onto the B01 area with the closest alpha (mean absolute
    alpha difference against the island's own texture 1-00 at the same sample points)."""
    w, h, _, own = texlib.load(res, hairedit.NAPE)
    _, _, _, b01 = texlib.load(os.path.join(OUT, "P_AMICIA_unpacked", "resources"), hairedit.B01)
    rnd = random.Random(15)
    new = {}
    for isl in islands(tris, len(vs)):
        idx = sorted({i for t in isl for i in t})
        U = {i: vs[i]["uv"] for i in idx}
        u0, v0 = min(U[i][0] for i in idx), min(U[i][1] for i in idx)
        wu, hv = max(U[i][0] for i in idx) - u0, max(U[i][1] for i in idx) - v0
        pts = []
        for _ in range(600):
            t = isl[rnd.randrange(len(isl))]
            r1, r2 = rnd.random(), rnd.random()
            if r1 + r2 > 1:
                r1, r2 = 1 - r1, 1 - r2
            p = [U[t[0]][k] + r1 * (U[t[1]][k] - U[t[0]][k]) + r2 * (U[t[2]][k] - U[t[0]][k]) for k in range(2)]
            pts.append((p[0] - u0, p[1] - v0, texlib.texel(own, w, h, p[0], p[1])[3]))

        def alpha(s, a, b, fl, x, y):
            return texlib.texel(b01, w, h, a + s * x, b + s * (hv - y if fl else y), wrap=False)[3]

        def score(s, a, b, fl, sub):
            return sum(abs(al - alpha(s, a, b, fl, x, y)) for x, y, al in sub) / len(sub)
        best = []
        for fl in (False, True):
            for s in (0.6, 0.8, 1.0, 1.25, 1.5):
                if s * wu > 1 or s * hv > 1:
                    continue
                for a in [k / 64 for k in range(int((1 - s * wu) * 64) + 1)]:
                    for b in [k / 64 for k in range(int((1 - s * hv) * 64) + 1)]:
                        best.append((score(s, a, b, fl, pts[:120]), s, a, b, fl))
        best.sort()
        fine = []
        for _, s, a, b, fl in best[:8]:
            for da in range(-4, 5):
                for db in range(-4, 5):
                    aa, bb = a + da / 256, b + db / 256
                    if 0 <= aa <= 1 - s * wu and 0 <= bb <= 1 - s * hv:
                        fine.append((score(s, aa, bb, fl, pts), s, aa, bb, fl))
        sc, s, a, b, fl = min(fine)
        for i in idx:
            new[i] = ((a + s * (U[i][0] - u0), b + s * (hv - (U[i][1] - v0) if fl else U[i][1] - v0)), fl)
        al_new = [alpha(s, a, b, fl, x, y) for x, y, _ in pts]
        report.append(dict(vertices=len(idx), uv_box=[round(u0, 4), round(v0, 4), round(wu, 4), round(hv, 4)], scale=s, flipped_v=fl,
                           to=[round(a, 4), round(b, 4)], mean_abs_alpha_diff=round(sc, 1),
                           alpha_mean_own=round(sum(al for _, _, al in pts) / len(pts)),
                           alpha_mean_b01=round(sum(al_new) / len(pts)),
                           drawn_own=round(sum(al >= 100 for _, _, al in pts) / len(pts), 2),
                           drawn_b01=round(sum(x >= 100 for x in al_new) / len(pts), 2)))
    return new


def main():
    arch = sys.argv[1]
    dst = sys.argv[2] if len(sys.argv) > 2 else os.path.join(OUT, "mod", "hairmock", f"hairmock_{arch}.glb")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    res = os.path.join(OUT, f"{arch}_unpacked", "resources")
    kres = os.path.join(OUT, "P_AMICIA_unpacked", "resources")
    kb = skel.load(os.path.join(kres, f"{SKEL_ID}.Skel_Z"))
    vb = skel.load(os.path.join(res, [f for f in os.listdir(res) if f.endswith(".Skel_Z")][0]))
    to_kit = {b["index"]: next((k["index"] for k in kb if k["name"] == b["name"]), None) for b in vb}
    g = GLB()
    g.doc["asset"]["generator"] = "modlab hairmock.py"
    add_skeleton(g, kb, bone_names(kb, paths.TABLES))
    g.doc["materials"] = [dict(name="hair_B01", alphaMode="MASK", alphaCutoff=0.4, doubleSided=True,
                               pbrMetallicRoughness=dict(metallicFactor=0.0, roughnessFactor=0.9))]
    prims, lines, nv = [], [], 0
    nape_rep = []
    kit_names = bone_names(kb, paths.TABLES)
    for mid, lab, n, imp, col, _ in hairedit.hair_set(arch, res):
        m, parts = load_mesh(os.path.join(res, f"{mid}.Mesh_Z"))
        rigid = m["vbs"][0]["stride"] == 44
        how = "skinned, own weights"
        for vs, tris, _, _ in parts:
            remap = nape_map(vs, tris, res, nape_rep) if col == hairedit.NAPE else {}
            out = []
            for k, v in enumerate(vs):
                v = dict(v)
                if rigid:
                    kj = to_kit[v["j"][0]]
                    if kj is None or kj in ATTACH:
                        kj, how = HEAD, "rigid on an attachment bone -> head"
                    else:
                        how = f"rigid on {kit_names[kj]} -> kept (weight 1)"
                    v["j"], v["w"] = (kj, 0, 0, 0), [1.0, 0.0, 0.0, 0.0]
                else:
                    j = [to_kit[x] for x in v["j"]]
                    if any(x is None and w > 0 for x, w in zip(j, v["w"])):
                        raise ValueError(f"{lab}: a weighted bone has no kit counterpart")
                    v["j"] = tuple(x if x is not None else 0 for x in j)
                if k in remap:
                    v["uv"], fl = remap[k]
                    if fl:   # v runs the other way: dP/dv reverses, so the tangent handedness w flips
                        v["t"] = list(v["t"][:3]) + [-v["t"][3]]
                out.append(v)
            prims.append((out, tris, 0))
            nv += len(out)
        lines.append(f"{lab}: {n} vertices, {how}" + (", UV islands moved onto alpha-matched B01 areas" if col == hairedit.NAPE else ""))
    add_mesh(g, "hair", prims, dict(source=f"{arch} hair pieces (mock)", vertices=nv))
    g.save(dst + ".tmp")
    fsx.replace(dst + ".tmp", dst)
    if nape_rep:
        json.dump(nape_rep, open(dst + ".nape.json", "w"), indent=1)
        for r in nape_rep:
            lines.append(f"  nape island {r['vertices']} v: scale {r['scale']}{' flipped' if r['flipped_v'] else ''} -> {r['to']}; alpha mean own {r['alpha_mean_own']} / "
                         f"B01 {r['alpha_mean_b01']}, drawn (alpha >= 100) own {r['drawn_own']} / B01 {r['drawn_b01']}")
    print("\n".join(lines))
    print(f"{dst}: 1 object 'hair', {nv} vertices, {sum(len(t) for _, t, _ in prims)} triangles")


if __name__ == "__main__":
    main()
