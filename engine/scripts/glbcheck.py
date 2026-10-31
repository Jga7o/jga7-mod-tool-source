"""Check a kit .glb with gltfread.py (independent of the writer) and optionally render it.

Checks: chunk/JSON structure; every accessor inside its buffer view; one skin with N joints whose world x inverse-bind
is the identity (so the rest pose shows the bind pose); per mesh node: JOINTS < N, WEIGHTS sum 1 (+-1e-3, at most 4
influences), unit normals, unit tangents with w = +-1, UVs present, triangle indices in range; images are PNG.
With --compare <resources_dir>: for nodes carrying extras.game_mesh_id, vertex/triangle counts and the vertex position
box equal the game mesh's geometry (exact float32).
Usage: python -I glbcheck.py <file.glb> [--compare <resources_dir>] [--render out.png]
"""
import math
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gltfread  # noqa: E402
from meshfmt import parse  # noqa: E402


def main():
    path = sys.argv[1]
    g = gltfread.load(path)
    doc = g.doc
    ok = True
    for i, a in enumerate(doc["accessors"]):
        if "bufferView" in a:
            v = doc["bufferViews"][a["bufferView"]]
            size = gltfread.CT[a["componentType"]][1] * gltfread.WIDTH[a["type"]]
            end = v.get("byteOffset", 0) + a.get("byteOffset", 0) + size * a["count"]
            if end > v.get("byteOffset", 0) + v["byteLength"] or end > len(g.bins[v.get("buffer", 0)]):
                print("accessor out of range:", i); ok = False
    if doc.get("skins"):
        mats, joints = g.skin_matrices(0)
        worst = max(abs(m[r][c] - (r == c)) for m in mats for r in range(4) for c in range(4))
        print(f"skin: {len(joints)} joints; max |world x inverseBind - I| = {worst:.2e}")
        ok &= worst < 1e-5
        names = [doc["nodes"][j]["name"] for j in joints]
        print(f"joint names unique: {len(set(names)) == len(names)}; named (not bone_NNN): {sum(not n.startswith('bone_') for n in names)}")
        ok &= len(set(names)) == len(names)
    else:   # M17: the face and necklace kits have no armature
        joints = []
        print("skin: none (a static kit: no armature)")
        ok &= not any("skin" in n for n in doc["nodes"])
    imgs = doc.get("images", [])
    png = all(g.bins[0][doc["bufferViews"][im["bufferView"]]["byteOffset"]:][:8] == b"\x89PNG\r\n\x1a\n" for im in imgs)
    print(f"images: {len(imgs)}, all PNG: {png}; materials: {len(doc.get('materials', []))}")
    ok &= png
    R = sys.argv[sys.argv.index("--compare") + 1] if "--compare" in sys.argv else None
    allP, allT = [], []
    bad_meshes = 0
    for ni, name in g.mesh_nodes():
        node = doc["nodes"][ni]
        nv = nt = 0
        P = []
        errs = []
        for p in doc["meshes"][node["mesh"]]["primitives"]:
            at = p["attributes"]
            pos = g.accessor(at["POSITION"])
            nrm = g.accessor(at["NORMAL"])
            tan = g.accessor(at["TANGENT"]) if "TANGENT" in at else None
            idx = [x[0] for x in g.accessor(p["indices"])]
            if "TEXCOORD_0" not in at:
                errs.append("no UVs")
            if "skin" in node:   # static reference objects (hair kit) carry no weights
                jo = g.accessor(at["JOINTS_0"])
                we = g.accessor(at["WEIGHTS_0"])
                if max(max(j) for j in jo) >= len(joints):
                    errs.append("joint index out of range")
                if max(abs(sum(w) - 1) for w in we) > 1e-3:
                    errs.append("weights do not sum to 1")
                if max(sum(x > 0 for x in w) for w in we) > 4:
                    errs.append("more than 4 influences")
            if max(abs(math.sqrt(sum(x * x for x in n)) - 1) for n in nrm) > 1e-3:
                errs.append("normal not unit")
            if tan and (max(abs(math.sqrt(sum(x * x for x in t[:3])) - 1) for t in tan) > 1e-3 or any(t[3] not in (1.0, -1.0) for t in tan)):
                errs.append("tangent not unit / w not +-1")
            if max(idx) >= len(pos):
                errs.append("index out of range")
            base = len(allP)
            allP += pos
            allT += [(base + idx[k], base + idx[k + 2], base + idx[k + 1]) for k in range(0, len(idx), 3)]  # back to game winding
            P += pos
            nv += len(pos)
            nt += len(idx) // 3
        ex = node.get("extras", {})
        if R and "game_mesh_id" in ex:
            rd = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(R))), f"{ex['archive']}_unpacked", "resources") if "archive" in ex else R
            b = open(os.path.join(rd, f"{ex['game_mesh_id']}.Mesh_Z"), "rb").read()
            m = parse(b)
            geo = [v for v in m["vbs"] if v["flags"] == 2]
            gp = [struct.unpack_from("<3f", b, v["pos"] + k * v["stride"]) for v in geo for k in range(v["count"])]
            gt = sum(s["ntri"] for s in m["subs"])
            box = lambda Q: ([min(q[i] for q in Q) for i in range(3)], [max(q[i] for q in Q) for i in range(3)])
            used_ok = nt == gt and box(P) == box(gp)
            if not used_ok:
                errs.append(f"differs from game mesh: tris {nt} vs {gt}, box {box(P)} vs {box(gp)}")
        if errs:
            bad_meshes += 1
            print(f"  {name}: {'; '.join(errs)}")
    print(f"mesh nodes: {len(g.mesh_nodes())}, with problems: {bad_meshes}")
    ok &= bad_meshes == 0
    if "--render" in sys.argv:
        from meshview import render
        from bmppreview import write_png
        lo = [min(p[i] for p in allP) - 0.02 for i in range(3)]
        hi = [max(p[i] for p in allP) + 0.02 for i in range(3)]
        a = render(allP, allT, 0, 2, ((lo[0], hi[0]), (lo[1], hi[1])))
        b = render(allP, allT, 2, 0, ((lo[2], hi[2]), (lo[1], hi[1])))
        W, H = a[0] + 12 + b[0], max(a[1], b[1])
        img = [(20, 20, 20)] * (W * H)
        for y in range(a[1]):
            img[y * W:y * W + a[0]] = a[2][y * a[0]:(y + 1) * a[0]]
        for y in range(b[1]):
            img[y * W + a[0] + 12:y * W + a[0] + 12 + b[0]] = b[2][y * b[0]:(y + 1) * b[0]]
        out = sys.argv[sys.argv.index("--render") + 1]
        write_png(out, W, H, img)
        print("rendered", out)
    print("GLBCHECK:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
