"""Make output\\blender_kit\\mock_body.glb (M14b): the atlas rehearsal split into two parts, written the way a Blender
export of the user's own body model should look. It is the self-test of the full_body edit and an example file.

Parts (spec files output\\mod\\mock_body_<part>.json, atlasmerge.py format):
  leather  -> host M38: boots M38, pants M18, corset M25, 11 belt pieces          (colour x 0.9406 host)
  fabric   -> host M32: hood M32, shirt M33 (outfit option 1), linen M30     (M32's own material; M12's UV area reserved)
Per part: one object, one material, one UV map; the textures embedded as PNG in Blender's conventions:
  baseColorTexture = the atlas colour as the game stores it (use "compensate": false when building)
  normalTexture    = OpenGL tangent-space (G = +Y up): game A -> R, 255 - game G -> G, z rebuilt -> B
  metallicRoughnessTexture = R 255 (unused), G = roughness (game normal B), B = metalness (game normal R)
The masks are not part of glTF: output\\blender_kit\\mock_body_mask_<part>.png (grey).
Armature: one joint per used game bone, named as in the kit (bone_names.json, else bone_NNN), placed at the bone's
rest position with an inverse bind matrix that undoes it, so blendimport keeps every position exactly.
M16 rehearsal: --parts leather,fabric,skin --out output\\blender_kit\\mock_body_m16.glb --mask-prefix mock_body_m16_mask_
  adds 'skin' -> host M42 (spec mod\\mock_body_skin.json): the two right-hand wraps M42 + M29 (skin-like material).
Usage: python -I mockbody.py [--out <file.glb>] [--parts leather,fabric] [--mask-prefix mock_body_mask_]
"""
import argparse
import json
import math
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paths  # noqa: E402  (the app's work folder and tables)
import fsx  # noqa: E402  (renames with a retry: Windows may hold a new file)
import atlasmerge  # noqa: E402
import skel  # noqa: E402
import texcodec as tc  # noqa: E402
from meshfmt import parse  # noqa: E402

OUT = paths.OUT
RES = os.path.join(OUT, "P_AMICIA_unpacked", "resources")
SKEL_ID = -3311511499466089408
PARTS = {"leather": "mock_body_leather.json", "fabric": "mock_body_fabric.json", "skin": "mock_body_skin.json"}


def gl_images(col, nrm, A):
    """Game-layout atlas images -> (base colour, OpenGL normal, metallic-roughness) RGBA images."""
    n = A * A
    normal = bytearray(n * 4)
    mr = bytearray(n * 4)
    for i in range(n):
        x = max(-1.0, (nrm[4 * i + 3] - 128) / 127)
        y = max(-1.0, (nrm[4 * i + 1] - 128) / 127)
        z = math.sqrt(max(0.0, 1 - x * x - y * y))
        normal[4 * i:4 * i + 4] = bytes((round((x * 0.5 + 0.5) * 255), round((-y * 0.5 + 0.5) * 255), round((z * 0.5 + 0.5) * 255), 255))
        mr[4 * i:4 * i + 4] = bytes((255, nrm[4 * i + 2], nrm[4 * i], 255))
    return col, normal, mr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(OUT, "blender_kit", "mock_body.glb"))
    ap.add_argument("--work", default=os.path.join(OUT, "import_work", "mockbody"))
    ap.add_argument("--parts", default="leather,fabric")
    ap.add_argument("--mask-prefix", default="mock_body_mask_")
    a = ap.parse_args()
    os.makedirs(a.work, exist_ok=True)
    names = {int(k): v for k, v in json.load(open(os.path.join(paths.TABLES, "analysis", "bone_names.json"))).items()}
    bones = skel.load(os.path.join(RES, f"{SKEL_ID}.Skel_Z"))
    blob = bytearray()
    views, accs, images, textures, materials, meshes, nodes = [], [], [], [], [], [], []

    def view(data, target=None):
        while len(blob) % 4:
            blob.append(0)
        v = dict(buffer=0, byteOffset=len(blob), byteLength=len(data))
        if target:
            v["target"] = target
        views.append(v)
        blob.extend(data)
        return len(views) - 1

    def acc(data, ctype, typ, count, target=34962, mn=None, mx=None, normalized=False):
        x = dict(bufferView=view(data, target), componentType=ctype, count=count, type=typ)
        if mn is not None:
            x["min"], x["max"] = mn, mx
        if normalized:
            x["normalized"] = True
        accs.append(x)
        return len(accs) - 1

    def png(img, w, h, name):
        p = os.path.join(a.work, f"{name}.png")
        tc.png_write(p, w, h, img)
        images.append(dict(name=name, mimeType="image/png", bufferView=view(open(p, "rb").read())))
        textures.append(dict(source=len(images) - 1))
        return len(textures) - 1

    parts, used = [], set()
    for part in a.parts.split(","):
        specf = PARTS[part]
        spec = json.load(open(os.path.join(paths.TABLES, "mod", specf)))
        mesh, col, nrm, msk, rep = atlasmerge.build(spec, RES)
        A, M = spec["atlas_size"], spec["mask_size"]
        m = parse(mesh)
        vb, ib = m["vbs"][0], m["ibs"][0]
        V = [mesh[vb["pos"] + 48 * k:vb["pos"] + 48 * (k + 1)] for k in range(vb["count"])]
        idx = struct.unpack_from(f"<{ib['count']}H", mesh, ib["pos"])
        for v in V:
            bi = struct.unpack_from("<4H", v, 28)
            wt = struct.unpack_from("<4H", v, 36)
            used |= {b for b, w in zip(bi, wt) if w}
        parts.append((part, spec, V, idx, col, nrm, msk, A, M, rep))
    joints = sorted(used)
    jindex = {b: k for k, b in enumerate(joints)}
    # armature: root + one joint per used bone at its rest position
    root = len(nodes)
    nodes.append(dict(name="Armature", children=[]))
    for b in joints:
        p = skel.world_pos(bones[b])
        nodes.append(dict(name=names.get(b, f"bone_{b:03d}"), translation=[float(x) for x in p]))
        nodes[root]["children"].append(len(nodes) - 1)
    ibm = b"".join(struct.pack("<16f", 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, -p[0], -p[1], -p[2], 1)
                   for p in (skel.world_pos(bones[b]) for b in joints))
    skin = dict(name="Armature", joints=[root + 1 + k for k in range(len(joints))], skeleton=root,
                inverseBindMatrices=acc(ibm, 5126, "MAT4", len(joints), target=None))
    summary = []
    for part, spec, V, idx, col, nrm, msk, A, M, rep in parts:
        n = len(V)
        P = [struct.unpack_from("<3f", v, 0) for v in V]
        T = [struct.unpack_from("<4b", v, 12) for v in V]
        N = [struct.unpack_from("<3b", v, 16) for v in V]
        UV = [struct.unpack_from("<2e", v, 20) for v in V]
        J = [[jindex.get(b, 0) if w else 0 for b, w in zip(struct.unpack_from("<4H", v, 28), struct.unpack_from("<4H", v, 36))] for v in V]
        W = [[w / 65535 for w in struct.unpack_from("<4H", v, 36)] for v in V]
        C = [v[44:48] for v in V]

        def unit(q):
            l = math.sqrt(sum(x * x for x in q)) or 1
            return [x / l for x in q]
        attr = dict(
            POSITION=acc(struct.pack(f"<{3 * n}f", *[x for p in P for x in p]), 5126, "VEC3", n,
                         mn=[min(p[k] for p in P) for k in range(3)], mx=[max(p[k] for p in P) for k in range(3)]),
            NORMAL=acc(struct.pack(f"<{3 * n}f", *[x for q in N for x in unit([c / 127 for c in q])]), 5126, "VEC3", n),
            TANGENT=acc(struct.pack(f"<{4 * n}f", *[x for t in T for x in unit([c / 127 for c in t[:3]]) + [1.0 if t[3] >= 0 else -1.0]]), 5126, "VEC4", n),
            TEXCOORD_0=acc(struct.pack(f"<{2 * n}f", *[x for u in UV for x in u]), 5126, "VEC2", n),
            COLOR_0=acc(b"".join(C), 5121, "VEC4", n, normalized=True),
            JOINTS_0=acc(struct.pack(f"<{4 * n}H", *[x for j in J for x in j]), 5123, "VEC4", n),
            WEIGHTS_0=acc(struct.pack(f"<{4 * n}f", *[x for w in W for x in w]), 5126, "VEC4", n))
        tri = []
        for k in range(0, len(idx), 3):   # game order is clockwise; glTF counter-clockwise
            tri += [idx[k], idx[k + 2], idx[k + 1]]
        ia = acc(struct.pack(f"<{len(tri)}I", *tri), 5125, "SCALAR", len(tri), target=34963)
        bc, nm, mr = gl_images(col, nrm, A)
        t0, t1, t2 = png(bc, A, A, f"{part}_basecolor"), png(nm, A, A, f"{part}_normal"), png(mr, A, A, f"{part}_metallic_roughness")
        materials.append(dict(name=f"{part}_material", pbrMetallicRoughness=dict(baseColorTexture=dict(index=t0),
                              metallicRoughnessTexture=dict(index=t2), metallicFactor=1.0, roughnessFactor=1.0),
                              normalTexture=dict(index=t1)))
        meshes.append(dict(name=part, primitives=[dict(attributes=attr, indices=ia, material=len(materials) - 1)]))
        nodes.append(dict(name=part, mesh=len(meshes) - 1, skin=0))
        mk = bytearray(b for i in range(M * M) for b in (msk[4 * i],) * 3 + (255,))
        mp = os.path.join(os.path.dirname(a.out), f"{a.mask_prefix}{part}.png")
        tc.png_write(mp + ".tmp", M, M, mk)
        fsx.replace(mp + ".tmp", mp)
        summary.append(f"{part} -> {spec['host']}: {n} vertices, {len(idx) // 3} triangles, {len(rep['pieces'])} pieces, "
                       f"density cap {rep['density_cap_texels_per_m']} texels/m, atlas {100 * rep['atlas_fill']:.1f} % used; mask {mp}")
        json.dump(rep, open(os.path.join(a.work, f"atlas_report_{part}.json"), "w"), indent=1)
    doc = dict(asset=dict(version="2.0", generator="modlab mockbody.py (M14b self-test)"), scene=0,
               scenes=[dict(nodes=[root] + [k for k, x in enumerate(nodes) if "mesh" in x])], nodes=nodes, skins=[skin],
               meshes=meshes, materials=materials, textures=textures, images=images, samplers=[],
               buffers=[dict(byteLength=0)], bufferViews=views, accessors=accs)
    del doc["samplers"]
    while len(blob) % 4:
        blob.append(0)
    doc["buffers"][0]["byteLength"] = len(blob)
    js = json.dumps(doc).encode()
    js += b" " * (-len(js) % 4)
    glb = struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(blob)) + struct.pack("<II", len(js), 0x4E4F534A) + js \
        + struct.pack("<II", len(blob), 0x004E4942) + blob
    tmp = a.out + ".tmp"
    open(tmp, "wb").write(glb)
    import gltfread   # check: it loads and every object has its skin and material
    g = gltfread.load(tmp)
    assert len(g.mesh_nodes()) == len(parts)
    fsx.replace(tmp, a.out)
    for x in summary:
        print(x)
    print(f"wrote {a.out} ({len(glb)} bytes, {len(joints)} joints)")


if __name__ == "__main__":
    main()
