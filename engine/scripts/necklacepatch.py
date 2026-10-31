"""Positions-only patcher for Amicia's necklace (M11): CHP1's C1-14 and CHP2's C2-04 (the only archives with a necklace).

Same method as facepatch.py: only vertex positions change; normals and tangents are rotated with the surface; UVs,
bones, weights, colors, indices and everything else stay byte for byte; bounds grow only if needed.
The kit (necklacekit.py) shows the necklace WELDED: one Blender vertex per distinct game position (the game mesh has
several vertices at one position: CHP1's necklace is 910 separate triangles, 2730 vertices on 498 positions). Every
kit vertex carries an ID in its second UV map (TEXCOORD_1 / "UVMap.001"): u = a game vertex index, v = the code below.
The patcher moves every game vertex at that vertex's original position to the new position, so the triangles stay
joined.
M17: without the ID map (another exporter, or the second UV map dropped) vertices are matched to the KIT's vertices by UV +
nearest kit position (as facepatch's fallback), the kit file giving each kit vertex its ID. Splits, welds and any order
never stop: only real geometry changes are reported as warnings (an added vertex is ignored, a removed position keeps its
original place, copies moved apart take their average). Errors: nothing matched; in fallback mode, faces that differ
beyond max(20, 2 %) without added or removed vertices to explain them (the matching failed).
Usage: python -I necklacepatch.py <edit.glb> --archive CHP1|CHP2 --dpc <in.DPC> [--out <out.DPC>] [--work <dir>]
         [--report <json>] [--dry]
"""
import argparse
import collections
import json
import math
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paths  # noqa: E402
import dpc  # noqa: E402
import gltfread  # noqa: E402
from bmpdump import body  # noqa: E402
from facepatch import GameMesh, gltf_vertices, match_fallback, patch_mesh  # noqa: E402
from patch_hide import find  # noqa: E402

OUT = paths.OUT
NECKLACES = {
    "CHP1": dict(archive="P_AMICIA_CHP1.DPC", mesh=3431154373098848609, label="C1-14", code=1140,
                 face=6286021572469189072, face_built="import_work/mod_var1_chp1/face1/M37.Mesh_Z",
                 refs={"C1-02_shirt_collar": -5923220261662420637, "C1-20_neck_hair": 6693113423734178536,
                       "C1-00_hair": -7983905044356577461}),
    "CHP2": dict(archive="P_AMICIA_CHP2.DPC", mesh=-6692578592939386441, label="C2-04", code=2040,
                 face=-8233843334373610856, face_built="import_work/mod_var1_chp2/face1/M37.Mesh_Z",
                 refs={"C2-10_shirt_collar": -3708268314585959537, "C2-15_hair": 2142539133781801251,
                       "C2-17_hair": 3754281534498632335}),
}


def mesh_from_dpc(buf, info, mid, work):
    d, e = find(info, {mid})[mid]
    lh = d["body_pos"] - d["link_header_pos"]
    data = bytearray(64) + bytes(buf[d["link_header_pos"]:d["body_pos"]]) + body(buf, d, work)
    struct.pack_into("<I", data, 0x2C, lh)
    return GameMesh(bytes(data))


def groups(gm):
    """{original position: [game vertex keys]} and key -> position."""
    g = collections.defaultdict(list)
    for k in sorted(gm.P):
        g[tuple(gm.P[k])].append(k)
    return g


def patch(gm, glb_path, cfg):
    g = gltfread.load(glb_path)
    names = [n for _, n in g.mesh_nodes()]
    verts, tris, used_ids = gltf_vertices(g, {cfg["code"]: 0}, cfg["label"], names)
    warns = []
    if not used_ids:
        # fallback: match to the kit's own vertices (UV + nearest kit position); the kit gives their IDs
        kit_path = cfg.get("kit") or next(p for p in (os.path.join(OUT, d, "necklace_kit", f"necklace_{cfg['archive'][9:13]}.glb")
                                                      for d in ("blender_kit",)) if os.path.isfile(p))
        kg = gltfread.load(kit_path)
        kv, _, kid = gltf_vertices(kg, {cfg["code"]: 0}, cfg["label"], [n for _, n in kg.mesh_nodes()])
        kv = [v for v in kv if v[0] not in (None, "other", "added")]
        if not kid or not kv:
            raise ValueError(f"the kit {os.path.basename(kit_path)} has no ID map: make the necklace kit again")

        class Ref:
            P = {v[0]: list(v[1]) for v in kv}
            UV = {v[0]: list(v[2]) for v in kv}
        verts = match_fallback(verts, Ref)
        warns.append("no ID UV map (TEXCOORD_1) in the file: vertices matched to the kit by UV + nearest position")
    keyed_all = verts
    added = [v for v in verts if v[0] not in gm.P]
    verts = [v for v in verts if v[0] in gm.P]
    if not verts:
        raise ValueError(f"no vertex of the file matches {cfg['label']}: export from the necklace kit object")
    grp = groups(gm)
    pos_of = {k: tuple(gm.P[k]) for k in gm.P}
    new = collections.defaultdict(list)
    for key, p, uv, name in verts:
        new[pos_of[key]].append(p)
    n_add = len({tuple(round(x, 4) for x in v[1]) for v in added if v[0] != "other"})
    if n_add:
        warns.append(f"added {n_add} vertices (new geometry): ignored, only moved vertices are used")
    missing = [q for q in grp if q not in new]
    if missing:
        warns.append(f"removed {len(missing)} vertices (missing geometry): they keep their original position")
        for q in missing:
            new[q] = [list(q)]
    split = [(q, max(math.dist(a, ps[0]) for a in ps)) for q, ps in new.items() if len(ps) > 1]
    bad = [s for s in split if s[1] > 1e-4]
    if bad:
        warns.append(f"{len(bad)} vertices were split and the copies moved apart (max {max(d for _, d in bad) * 1000:.2f} mm): "
                     "their average position is used")
    # triangles by position group (seams and splits do not count)
    gid = {q: n for n, q in enumerate(grp)}
    kg_of = [gid[pos_of[v[0]]] if v[0] in gm.P else None for v in keyed_all]
    kt = collections.Counter(tuple(sorted(kg_of[i] for i in t)) for t in tris if all(kg_of[i] is not None for i in t))
    gt = collections.Counter(tuple(sorted(gid[pos_of[k]] for k in t)) for t in gm.tris)
    kt = collections.Counter({t: n for t, n in kt.items() if len(set(t)) == 3})
    gt_set, kt_set = set(gt), set(kt)
    if gt_set != kt_set:
        rem = {gid[q] for q in missing}
        nb = set(rem)
        for t in gt_set:
            if set(t) & rem:
                nb |= set(t)
        near_add = {kg_of[i] for t in tris if any(kg_of[i] is None for i in t) for i in t if kg_of[i] is not None}
        unexplained = [t for t in gt_set - kt_set if not (set(t) & nb) and not set(t) <= near_add] + \
                      [t for t in kt_set - gt_set if not (set(t) & nb) and not (set(t) & near_add)]
        msg = (f"faces differ from the game's: {len(kt_set - gt_set)} new, {len(gt_set - kt_set)} missing, {len(unexplained)} "
               "not explained by added or removed vertices (positions are taken; faces are not)")
        if not used_ids and len(unexplained) > max(20, 0.02 * len(gt_set)):
            raise ValueError("the vertices could not be matched to the necklace kit (no ID map): " + msg)
        warns.append(msg)
    P1 = {q: [sum(p[i] for p in ps) / len(ps) for i in range(3)] for q, ps in new.items()}
    full = [(k, P1[pos_of[k]], gm.UV[k], cfg["label"]) for k in sorted(gm.P)]
    idx = {k: n for n, (k, *_) in enumerate(full)}
    rep, nb = patch_mesh(gm, full, [tuple(idx[k] for k in t) for t in gm.tris], True, cfg["label"])
    rep.update(kit_vertices=len(new), game_vertices=len(gm.P), position_groups=len(grp))
    rep["warnings"] = warns + rep.get("warnings", [])
    if n_add:
        rep["added_vertices"] = n_add
    if missing:
        rep["removed_vertices"] = len(missing)
    return rep, nb


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("glb")
    ap.add_argument("--archive", required=True, choices=sorted(NECKLACES))
    ap.add_argument("--dpc", required=True)
    ap.add_argument("--out")
    ap.add_argument("--work", default=os.path.join(OUT, "import_work", "necklacepatch"))
    ap.add_argument("--report")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    os.makedirs(a.work, exist_ok=True)
    cfg = NECKLACES[a.archive]
    buf = bytearray(open(a.dpc, "rb").read())
    info = dpc.parse(bytes(buf))
    gm = mesh_from_dpc(buf, info, cfg["mesh"], a.work)
    try:
        rep, nb = patch(gm, a.glb, cfg)
    except ValueError as ex:
        print(f"ERROR {cfg['label']}: {ex}")
        sys.exit(1)
    print(json.dumps(rep))
    if nb:
        open(os.path.join(a.work, f"{cfg['label']}.Mesh_Z"), "wb").write(nb)
        if a.out and not a.dry:
            from put_mesh import put
            print(f"put {cfg['mesh']}:", put(buf, info, cfg["mesh"], nb, a.work, allow_bounds=True))
            open(a.out, "wb").write(buf)
            print("wrote", a.out)
    if a.report:
        json.dump([rep], open(a.report, "w"), indent=1)


if __name__ == "__main__":
    main()
