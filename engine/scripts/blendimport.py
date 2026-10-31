"""Import one mesh object from a Blender glTF export into a slot (Mesh_Z) of P_AMICIA.DPC.

Reads: positions, normals, UVs (TEXCOORD_0), tangents (computed from the UVs if missing), vertex colors (COLOR_0,
white if missing), and the skin: JOINTS_n/WEIGHTS_n mapped to game bone indices by joint name (kit names such as
"hips", "leftupleg", or "bone_NNN"; see notes\\blender_guide.md). All primitives of the object are merged into the
slot's single submesh (the slot's own material/texture is used in game).
Positions: rest pose = sum of weights x (joint world x inverse bind) x vertex, so object/armature transforms are
applied; when every joint matrix is the identity (the normal case for an exported kit), positions are kept exactly.
Weights: at most 4 influences (more: the 4 largest are kept, warned); quantized to u16. Each weight is rounded on its
own; if the sum then lies in 65519..65535 (the range the game's own meshes use) it is kept, otherwise the weights are
normalized to exactly 65535. Unused slots are (bone 0, weight 0) like the game's. Rigid hair slots (stride 44) are refused.
The geometry is padded to the slot's counts (meshio.build --pad), bounds are recomputed, and the slot is written into a
copy of the DPC (put_mesh.py). --hide zeroes the index buffers of other pieces (patch_hide.py), so they disappear.

Larger than the slot (M13): a mesh with more vertices or triangles than the slot is accepted. It is built with its own
counts (no padding) and the slot is enlarged (growdpc.py: sectors inserted, later offsets moved). Limits: 65,535
vertices (16-bit indices), and the stored resource at most 5,592,539 bytes (the largest standalone resource in the
game's archives). A mesh that fits keeps the old behaviour exactly (padded to the slot's counts, written in place).

Checks (errors stop the import): triangles only; UVs present; every vertex weighted;
every joint name known; finite values; the limits above. Warnings: >4 influences, UVs outside 0..1, vertices more than 0.45 m from
their main bone, normals pointing against the triangle winding, unused vertices.

Usage:
  python -I blendimport.py <in.glb|gltf> --object <name> --slot <Mxx|mesh_id> --dpc <in.DPC> --out <out.DPC>
         [--hide Mxx,Mxx,...] [--work <dir>] [--report <report.json>]
         [--res <variant>_unpacked\\resources]   (variant archive: slot = its mesh id, bones remapped by name hash)
         [--bone-fallback]       (bones missing there take their nearest existing parent: M15 hair, CHP1 hair1-5 -> head)
  python -I blendimport.py <in.glb> --list          (mesh objects and slot budgets)
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
import fsx  # noqa: E402  (renames with a retry: Windows may hold a new file)
import gltfread  # noqa: E402
import growdpc  # noqa: E402
import meshio  # noqa: E402
import mergemesh  # noqa: E402
import skel  # noqa: E402
from meshfmt import parse  # noqa: E402

OUT = paths.OUT
RES = os.path.join(OUT, "P_AMICIA_unpacked", "resources")
SKEL_ID = -3311511499466089408


def slot_table():
    rows = [l.split("\t") for l in open(os.path.join(paths.TABLES, "analysis", "P_AMICIA_parts.tsv")).read().split("\n")[1:] if l]
    return {r[0]: int(r[1]) for r in rows}


def bone_lookup(res=None, fallback=False):
    """Kit joint name -> game bone index. Kit names are P_AMICIA's bones; with res (a variant archive's extracted
    resources) each is mapped to the bone with the same name hash in that archive's own skeleton (bone order differs per
    archive; meshes use their archive's skeleton indices). A bone missing there maps to None, or with fallback (M15
    hair: CHP1 has no hair1-5) to its nearest P_AMICIA ancestor that exists there (hair1-5 -> head)."""
    bones = skel.load(os.path.join(RES, f"{SKEL_ID}.Skel_Z"))
    p = os.path.join(paths.TABLES, "analysis", "bone_names.json")
    known = {int(k): v for k, v in json.load(open(p)).items()}
    look = {}
    for b in bones:
        i = b["index"]
        look[f"bone_{i:03d}"] = i
        if i in known:
            look[known[i]] = i
    if res:
        import glob
        sk = glob.glob(os.path.join(res, "*.Skel_Z"))
        if len(sk) != 1:
            raise ValueError(f"{res}: expected one Skel_Z, found {len(sk)}")
        vbones = skel.load(sk[0])
        by_hash = {b["name"]: b["index"] for b in vbones}

        def find(i):
            while i >= 0 and bones[i]["name"] not in by_hash and fallback:
                i = bones[i]["parent"]
            return by_hash.get(bones[i]["name"]) if i >= 0 else None
        look = {n: find(i) for n, i in look.items()}
        bones = vbones
    return look, bones


def tangents(P, N, UV, tris):
    """Per-vertex tangents from UVs (accumulated per triangle, Gram-Schmidt); w = sign(dot(N x T, dP/dv))."""
    tan = [[0.0, 0.0, 0.0] for _ in P]
    bit = [[0.0, 0.0, 0.0] for _ in P]
    for a, b, c in tris:
        e1 = [P[b][i] - P[a][i] for i in range(3)]
        e2 = [P[c][i] - P[a][i] for i in range(3)]
        du1, dv1 = UV[b][0] - UV[a][0], UV[b][1] - UV[a][1]
        du2, dv2 = UV[c][0] - UV[a][0], UV[c][1] - UV[a][1]
        r = du1 * dv2 - du2 * dv1
        if abs(r) < 1e-12:
            continue
        T = [(e1[i] * dv2 - e2[i] * dv1) / r for i in range(3)]
        B = [(e2[i] * du1 - e1[i] * du2) / r for i in range(3)]
        for v in (a, b, c):
            tan[v] = [tan[v][i] + T[i] for i in range(3)]
            bit[v] = [bit[v][i] + B[i] for i in range(3)]
    out = []
    for k in range(len(P)):
        n = N[k]
        t = tan[k]
        d = sum(t[i] * n[i] for i in range(3))
        t = gltfread.unit([t[i] - n[i] * d for i in range(3)])
        if sum(x * x for x in t) < 0.5:  # no UV gradient: any perpendicular vector
            t = gltfread.unit([n[1], -n[0], 0.0] if abs(n[2]) < 0.9 else [0.0, n[2], -n[1]])
        cr = [n[1] * t[2] - n[2] * t[1], n[2] * t[0] - n[0] * t[2], n[0] * t[1] - n[1] * t[0]]
        out.append(t + [1.0 if sum(cr[i] * bit[k][i] for i in range(3)) >= 0 else -1.0])
    return out


def read_object(g, node_index, look):
    errors, warns = [], []
    node = g.doc["nodes"][node_index]
    mesh = g.doc["meshes"][node["mesh"]]
    skin = node.get("skin")
    if skin is None:
        if not g.doc.get("skins"):
            errors.append("the file has no armature and no weights (no glTF skin): export with the armature selected too "
                          "(Include > Limit to: Selected Objects) and Data > Skinning on")
        else:
            errors.append("the object has no armature/skin: parent it to the kit armature with vertex groups (weights)")
        return None, errors, warns
    mats, joints = g.skin_matrices(skin)
    jnames = [g.doc["nodes"][j].get("name", "") for j in joints]
    jbone = []
    for n in jnames:
        if n in look and look[n] is not None:
            jbone.append(look[n])
        else:
            jbone.append(None)
    identity = all(gltfread.is_identity(m, 1e-4) for m in mats)
    V = []
    tris = []
    weighted = set()
    for p in mesh["primitives"]:
        if p.get("mode", 4) != 4:
            errors.append("only triangles are supported (triangulate before export)")
            continue
        at = p["attributes"]
        for need in ("POSITION", "NORMAL", "TEXCOORD_0", "JOINTS_0", "WEIGHTS_0"):
            if need not in at:
                errors.append(f"missing {need} (see the export settings in blender_guide.md)")
        if errors:
            continue
        pos = g.accessor(at["POSITION"])
        nrm = g.accessor(at["NORMAL"])
        uv = g.accessor(at["TEXCOORD_0"])
        tan = g.accessor(at["TANGENT"]) if "TANGENT" in at else None
        col = g.accessor(at["COLOR_0"]) if "COLOR_0" in at else None
        if "TEXCOORD_1" in at:
            warns.append("more than one UV map: only the first (TEXCOORD_0) is used")
        js, ws = [], []
        s = 0
        while f"JOINTS_{s}" in at:
            js.append(g.accessor(at[f"JOINTS_{s}"]))
            ws.append(g.accessor(at[f"WEIGHTS_{s}"]))
            s += 1
        base = len(V)
        for k in range(len(pos)):
            infl = {}
            for jset, wset in zip(js, ws):
                for j, w in zip(jset[k], wset[k]):
                    if w > 0:
                        j = int(j)
                        if jbone[j] is None:
                            errors.append(f"joint '{jnames[j]}' is not a kit bone name" if jnames[j] not in look else
                                          f"bone '{jnames[j]}' does not exist in this archive's skeleton")
                            continue
                        infl[jbone[j]] = infl.get(jbone[j], 0.0) + w
                        weighted.add(jnames[j])
            order = [(int(j), w) for j, w in zip(js[0][k], ws[0][k])] if js else []
            V.append(dict(p=pos[k], n=nrm[k], uv=uv[k], t=tan[k] if tan else None,
                          c=[max(0, min(255, round(x * 255))) for x in (col[k] + [1.0] * (4 - len(col[k])))[:4]] if col else [255] * 4,
                          infl=infl, order=[(jbone[j] if j < len(jbone) else None, w) for j, w in order]))
        idx = [x[0] for x in g.accessor(p["indices"])] if "indices" in p else list(range(len(pos)))
        tris += [(base + idx[i], base + idx[i + 1], base + idx[i + 2]) for i in range(0, len(idx) - 2, 3)]
    if errors:
        return None, sorted(set(errors)), warns
    if not identity:
        warns.append("joint x inverse-bind is not the identity: rest-pose positions computed from the skin")
    return dict(V=V, tris=tris, identity=identity, mats=mats, jbone=jbone, weighted=weighted), errors, warns


def to_game(obj, bones, stride, warns, errors):
    """Vertex dicts in game convention: positions, normals, tangents, UVs, bones/weights (u16), colors."""
    out = []
    many = 0
    far = 0
    for v in obj["V"]:
        infl = v["infl"]
        if not infl:
            errors.append("a vertex has no weights: every vertex must be in at least one vertex group")
            break
        if len(infl) > 4:
            many += 1
        # keep the exporter's order when it already has <= 4 influences (byte-exact round trip), else the 4 largest
        if len(infl) <= 4 and len([1 for b, w in v["order"] if w > 0]) == len(infl):
            top = [(b, w) for b, w in v["order"]]
        else:
            top = sorted(infl.items(), key=lambda kv: -kv[1])[:4]
            top += [(0, 0.0)] * (4 - len(top))
        tot = sum(w for _, w in top)
        q = [int(round(w * 65535)) for _, w in top]
        if not 65519 <= sum(q) <= 65535:  # the game's own sums lie in 65519..65535 (all 5 files)
            q = [int(round(w / tot * 65535)) for _, w in top]
            q[max(range(4), key=lambda i: q[i])] += 65535 - sum(q)
        bi = [b if qq else 0 for (b, _), qq in zip(top, q)]
        main = bi[max(range(4), key=lambda i: q[i])]
        p = list(v["p"])
        n = gltfread.unit(v["n"])
        if not obj["identity"]:
            M = [[sum((w / tot) * obj["mats"][obj["jbone"].index(b)][r][c] for (b, w) in top if w > 0) for c in range(4)] for r in range(4)]
            p = gltfread.apply(M, p)
            n = gltfread.unit([sum(M[r][c] * v["n"][c] for c in range(3)) for r in range(3)])
        if math.dist(p, skel.world_pos(bones[main])) > 0.45:
            far += 1
        out.append(dict(p=p, n=n, t=v["t"], uv=list(v["uv"]), bones=bi, weights=q, c=v["c"]))
    if many:
        warns.append(f"{many} vertices had more than 4 influences: kept the 4 largest (set Bone Influences = 4 in the exporter)")
    if far:
        warns.append(f"{far} vertices are more than 0.45 m from their main bone (check the weights)")
    return out


def snorm(v, w):
    return [max(-127, min(127, round(x * 127))) for x in v[:3]] + [w]


def write_layout_gltf(vs, tris, path):
    """Write meshio's glTF layout (the import path proven in M4/M5)."""
    F32, U16, I8, U8 = 5126, 5123, 5120, 5121
    blob, views, accs = bytearray(), [], []

    def put(data, ctype, typ, count, normalized=False, target=34962):
        while len(blob) % 4:
            blob.append(0)
        views.append(dict(buffer=0, byteOffset=len(blob), byteLength=len(data), target=target))
        blob.extend(data)
        a = dict(bufferView=len(views) - 1, componentType=ctype, count=count, type=typ)
        if normalized:
            a["normalized"] = True
        accs.append(a)
        return len(accs) - 1
    n = len(vs)
    nraw = [snorm(v["n"], 127) for v in vs]
    traw = [snorm(v["t"], 127 if v["t"][3] >= 0 else -127) for v in vs]
    attr = dict(
        POSITION=put(struct.pack(f"<{3 * n}f", *[x for v in vs for x in v["p"]]), F32, "VEC3", n),
        NORMAL=put(struct.pack(f"<{3 * n}f", *[x for v in vs for x in v["n"]]), F32, "VEC3", n),
        TANGENT=put(struct.pack(f"<{4 * n}f", *[x for v in vs for x in v["t"][:3] + [1.0 if v["t"][3] >= 0 else -1.0]]), F32, "VEC4", n),
        TEXCOORD_0=put(struct.pack(f"<{2 * n}f", *[x for v in vs for x in v["uv"]]), F32, "VEC2", n),
        _NORMAL_RAW=put(struct.pack(f"<{4 * n}b", *[x for r in nraw for x in r]), I8, "VEC4", n),
        _TANGENT_RAW=put(struct.pack(f"<{4 * n}b", *[x for r in traw for x in r]), I8, "VEC4", n),
        _BONES=put(struct.pack(f"<{4 * n}H", *[x for v in vs for x in v["bones"]]), U16, "VEC4", n),
        _WEIGHTS=put(struct.pack(f"<{4 * n}H", *[x for v in vs for x in v["weights"]]), U16, "VEC4", n),
        COLOR_0=put(bytes(x for v in vs for x in v["c"]), U8, "VEC4", n, normalized=True),
    )
    idx = [x for t in tris for x in t]  # glTF (counter-clockwise) order; meshio.read_gltf flips to the game's
    ia = put(struct.pack(f"<{len(idx)}H", *idx), U16, "SCALAR", len(idx), target=34963)
    doc = dict(asset=dict(version="2.0"), meshes=[dict(primitives=[dict(attributes=attr, indices=ia)])],
               buffers=[dict(byteLength=len(blob), uri=os.path.basename(path)[:-5] + ".bin")], bufferViews=views, accessors=accs)
    open(path[:-5] + ".bin", "wb").write(blob)
    json.dump(doc, open(path, "w"))


def import_object(glb, obj_name, slot, work, res=None, fallback=False):
    """Returns (Mesh_Z bytes, report dict) or raises with the error list. res: a variant archive's extracted resources
    (slot = its mesh id; bones remapped to its skeleton). fallback: bones missing in that skeleton go to their nearest
    existing ancestor (reported as a warning)."""
    slots = {} if res else slot_table()
    mid = slots[slot] if slot in slots else int(slot)
    label = next((k for k, v in slots.items() if v == mid), str(mid))
    tpl = os.path.join(res or RES, f"{mid}.Mesh_Z")
    tb = open(tpl, "rb").read()
    tm = parse(tb)
    if len(tm["vbs"]) != 1 or len(tm["ibs"]) != 1 or len(tm["subs"]) != 1:
        raise ValueError(f"slot {label} has morph data or several submeshes; choose a single-submesh slot (blender_guide.md)")
    vb, ib = tm["vbs"][0], tm["ibs"][0]
    if vb["stride"] != 48:
        raise ValueError(f"slot {label} is a rigid hair slot (stride 44, one bone per vertex); use a skinned stride-48 slot")
    g = gltfread.load(glb)
    look, bones = bone_lookup(res, fallback)
    obj, errors, warns = read_object(g, g.find_node(obj_name), look)
    if errors:
        raise ValueError("; ".join(errors))
    if fallback and res:
        strict, _ = bone_lookup(res)
        moved = sorted(n for n in obj["weighted"] if strict.get(n) is None)
        if moved:
            warns.append(f"bones missing in this archive, weights moved to the nearest existing parent: "
                         + ", ".join(f"{n} -> bone {look[n]}" for n in moved))
    vs = to_game(obj, bones, vb["stride"], warns, errors)
    if errors:
        raise ValueError("; ".join(sorted(set(errors))))
    tris = obj["tris"]
    if any(v["t"] is None for v in vs):
        T = tangents([v["p"] for v in vs], [v["n"] for v in vs], [v["uv"] for v in vs], [(a, c, b) for a, b, c in tris])
        for v, t in zip(vs, T):
            if v["t"] is None:
                v["t"] = t
        warns.append("no tangents in the file: computed from the UVs")
    else:
        for v in vs:
            v["t"] = gltfread.unit(v["t"][:3]) + [v["t"][3]]
    enlarged = len(vs) > vb["count"] or len(tris) > ib["count"] // 3
    if len(vs) > mergemesh.MAX_VERTS:
        raise ValueError(f"too many vertices for one mesh: {len(vs)} (the limit is {mergemesh.MAX_VERTS}: indices are 16-bit). "
                         f"Reduce the mesh, or split it across two slots (fallback)")
    if enlarged:
        warns.append(f"larger than slot {label} ({len(vs)} vertices of {vb['count']}, {len(tris)} triangles of {ib['count'] // 3}): "
                     f"the slot is enlarged")
    used = {i for t in tris for i in t}
    if len(used) < len(vs):
        warns.append(f"{len(vs) - len(used)} vertices are not used by any triangle")
    if any(not (-1e-3 <= x <= 1.001) for v in vs for x in v["uv"]):
        warns.append("UVs outside 0..1: the texture repeats there (fine for tiling textures such as B21 in M30 or B24 in M13; "
                     "on atlas textures such as B12, B15, B37, B40 it shows neighbouring regions)")
    against = 0
    for a, b, c in tris:
        e1 = [vs[b]["p"][i] - vs[a]["p"][i] for i in range(3)]
        e2 = [vs[c]["p"][i] - vs[a]["p"][i] for i in range(3)]
        cr = [e1[1] * e2[2] - e1[2] * e2[1], e1[2] * e2[0] - e1[0] * e2[2], e1[0] * e2[1] - e1[1] * e2[0]]
        against += sum(cr[i] * vs[a]["n"][i] for i in range(3)) < 0
    if against > 0.01 * max(1, len(tris)):
        warns.append(f"{against} triangles have normals against their winding (flipped faces? recalculate normals outside)")
    if any(not math.isfinite(x) for v in vs for x in v["p"] + v["n"] + v["uv"]):
        raise ValueError("non-finite values in positions/normals/UVs")
    os.makedirs(work, exist_ok=True)
    lay = os.path.join(work, f"import_{label}.gltf")
    write_layout_gltf(vs, tris, lay)
    if enlarged:   # a template with the mesh's own counts (zeros), then the proven meshio path without padding
        big = os.path.join(work, f"template_{label}.Mesh_Z")
        open(big, "wb").write(mergemesh.assemble(tb, tm, bytes(len(vs) * vb["stride"]), [0] * (3 * len(tris))))
        built = meshio.build(big, lay, pad=False, bounds=True)
    else:
        built = meshio.build(tpl, lay, pad=True, bounds=True)
    us = [v["uv"] for v in vs]
    report = dict(name=f"{obj_name} -> {label}", slot=label, mesh_id=mid, vertices=len(vs), triangles=len(tris),
                  budget_vertices=vb["count"], budget_triangles=ib["count"] // 3, warnings=warns,
                  uv_rect=[min(u[0] for u in us), min(u[1] for u in us), max(u[0] for u in us), max(u[1] for u in us)],
                  any_bones=True, any_colors=True, exact_positions=obj["identity"], enlarged=enlarged)
    return built, report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("glb")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--object"); ap.add_argument("--slot")
    ap.add_argument("--dpc"); ap.add_argument("--out")
    ap.add_argument("--hide", default="")
    ap.add_argument("--work", default=os.path.join(OUT, "import_work"))
    ap.add_argument("--report")
    ap.add_argument("--res", help="a variant archive's extracted resources dir (slot = its mesh id; bones remapped by name)")
    ap.add_argument("--bone-fallback", action="store_true",
                    help="bones missing in the variant's skeleton take their nearest existing parent (M15 hair, CHP1)")
    a = ap.parse_args()
    if a.list:
        g = gltfread.load(a.glb)
        for i, n in g.mesh_nodes():
            ex = g.doc["nodes"][i].get("extras", {})
            print(n, {k: ex[k] for k in ("budget_vertices", "budget_triangles", "submeshes") if k in ex})
        return
    try:
        built, rep = import_object(a.glb, a.object, a.slot, a.work, a.res, a.bone_fallback)
    except (ValueError, KeyError) as e:
        print("ERROR:", e)
        sys.exit(1)
    for w in rep["warnings"]:
        print("WARNING:", w)
    print(f"{rep['name']}: {rep['vertices']} vertices of {rep['budget_vertices']}, {rep['triangles']} triangles of {rep['budget_triangles']}"
          + (" (slot enlarged)" if rep["enlarged"] else ""))
    mz = os.path.join(a.work, f"{rep['slot']}.Mesh_Z")
    open(mz, "wb").write(built)
    if a.report:
        json.dump(rep, open(a.report, "w"), indent=1)
    if a.dpc and a.out:
        import dpc as dpcmod
        from put_mesh import put
        from patch_hide import find, ib_ranges, slot_limits  # noqa: F401
        if rep["enlarged"]:
            try:
                g = growdpc.write(a.dpc, a.out, {rep["mesh_id"]: dict(resource=growdpc.make_resource(a.dpc, rep["mesh_id"], built, a.work))})
            except ValueError as e:
                print("ERROR:", e)
                sys.exit(1)
            print(f"put: slot enlarged by {g['sectors_inserted']} sectors {g['grown']}")
        else:
            buf = bytearray(open(a.dpc, "rb").read())
            info = dpcmod.parse(bytes(buf))
            print("put:", put(buf, info, rep["mesh_id"], built, a.work, allow_bounds=True))
            open(a.out, "wb").write(buf)
        hide = [h for h in a.hide.split(",") if h]
        if hide:
            import subprocess
            slots = slot_table()
            ids = ",".join(str(slots[h] if h in slots else int(h)) for h in hide)
            tmp = a.out + ".hide.DPC"
            subprocess.run([sys.executable, "-I", os.path.join(HERE, "patch_hide.py"), a.out, tmp, a.work, "--ids", ids], check=True)
            fsx.replace(tmp, a.out)
            print("hidden:", ", ".join(hide))
        print("wrote", a.out)


if __name__ == "__main__":
    main()
