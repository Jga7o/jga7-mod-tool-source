"""Build the face kit (M8): output\\blender_kit\\face_kit\\

- Texture PNGs at full size (mip 0, decoded): B05 face skin, B31 iris, B34 eyelashes (RGBA, alpha kept), and copies with
  the UV layout drawn on top (*_uv.png). Edit the plain PNG and give it to texpatch.py apply.
- face_kit.glb: the face meshes in bind pose (meters, glTF +Y up, she faces +Z), one object each:
  face_M37 (face + second head part, 2 primitives), mouth_M26 (teeth + mouth interior), eyes_M28, lashes_M36.
  No armature: only positions matter for facepatch.py. Textures are the full-size PNGs above.
  Every vertex carries an ID in a second UV map (TEXCOORD_1, Blender name "UVMap.001"): u = vertex index in its game
  vertex block, v = mesh number x 10 + part (M37 part 0 = 370, part 1 = 371, M26 = 260, M28 = 280, M36 = 360).
  facepatch.py uses it to find each vertex after Blender reorders or splits them. Do not edit or delete that UV map.
Usage: python -I facekit.py [<out_dir>]
"""
import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paths  # noqa: E402  (the app's work folder and tables)
import dpc  # noqa: E402
import texcodec as tc  # noqa: E402
from bitmaps import decode  # noqa: E402
from blender_kit import GLB, load_mesh  # noqa: E402
from bmpdump import body, bitmaps  # noqa: E402

OUT = paths.OUT
R = os.path.join(OUT, "P_AMICIA_unpacked", "resources")
MESHES = [("face_M37", "M37", 4840249479922744736, 37), ("mouth_M26", "M26", 360281120925488824, 26),
          ("eyes_M28", "M28", 806504510931861565, 28), ("lashes_M36", "M36", 4363979579306007351, 36)]
# texture label -> (id, file name, editable in the kit)
TEX = {"B05": (-5588537412492053000, "B05_face_skin"), "B31": (7468423702097631877, "B31_iris"),
       "B34": (-6192000328531762344, "B34_eyelashes"), "B25": (4215333268473590943, None),
       "B27": (4411794518544929908, None), "B29": (-415096154365161564, None)}
UV_DRAW = {"B05": [("M37", 0, (255, 230, 0)), ("M37", 1, (0, 220, 255))], "B31": [("M28", None, (255, 230, 0))],
           "B34": [("M36", None, (255, 230, 0))]}


def material_textures():
    """{material id: base-color texture label} from pamicia_refs.tsv."""
    lab = {}
    for line in open(os.path.join(paths.TABLES, "previews", "P_AMICIA_bitmaps.tsv")):
        p = line.split("\t")
        if p[0] != "label":
            lab[int(p[1])] = p[0]
    out = {}
    for line in open(os.path.join(paths.TABLES, "pamicia_refs.tsv")):
        a, ac, b, bc = line.rstrip("\n").split("\t")
        if ac == "Material_Z" and bc == "Bitmap_Z" and lab.get(int(b)) in TEX:
            out.setdefault(int(a), lab[int(b)])
    return out


def line(img, w, h, p, q, col):
    x0, y0, x1, y1 = int(p[0] * w), int(p[1] * h), int(q[0] * w), int(q[1] * h)
    dx, dy = abs(x1 - x0), -abs(y1 - y0)
    sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
    err = dx + dy
    for _ in range(4 * (w + h)):
        if 0 <= x0 < w and 0 <= y0 < h:
            o = 4 * (y0 * w + x0)
            img[o:o + 4] = bytes(col) + b"\xff"
        if x0 == x1 and y0 == y1:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy


def main():
    dst = sys.argv[1] if len(sys.argv) > 1 else os.path.join(OUT, "blender_kit", "face_kit")
    os.makedirs(dst, exist_ok=True)
    work = os.path.join(OUT, "import_work", "facekit")
    os.makedirs(work, exist_ok=True)
    buf = open(os.path.join(paths.ORIG, "P_AMICIA.DPC"), "rb").read()
    info = dpc.parse(buf)
    bms = {dpc.s64(d["name"]): d for d in bitmaps(info)}
    pix = {}
    for lab, (bid, fname) in TEX.items():
        d = bms[bid]
        bi = decode(buf, d)
        img = tc.decode_bc(body(buf, d, work), bi["fmt_label"], bi["w"], bi["h"])
        pix[lab] = (bi, img)
        if fname:
            tc.png_write(os.path.join(dst, fname + ".png.tmp"), bi["w"], bi["h"], img, alpha=bi["fmt_label"] == "BC3")
            print(f"{fname}.png: {bi['w']}x{bi['h']} {bi['fmt_label']}, id {bid}")
    mtex = material_textures()
    meshes = {}
    for name, label, mid, num in MESHES:
        meshes[label] = load_mesh(os.path.join(R, f"{mid}.Mesh_Z"))
    # UV layout copies
    for lab, draws in UV_DRAW.items():
        bi, img = pix[lab]
        w, h = bi["w"], bi["h"]
        out = bytearray(img)
        if bi["fmt_label"] == "BC3":  # lashes: show over grey so the strands are visible
            for i in range(0, len(out), 4):
                a = out[i + 3]
                for c in range(3):
                    out[i + c] = (out[i + c] * a + 128 * (255 - a)) // 255
                out[i + 3] = 255
        for label, part, col in draws:
            m, prims = meshes[label]
            for k, (vs, tris, mat, used) in enumerate(prims):
                if (part is not None and k != part) or (part is None and mtex.get(mat) != lab):
                    continue
                edges = {tuple(sorted((t[i], t[(i + 1) % 3]))) for t in tris for i in range(3)}
                for a, b in edges:
                    line(out, w, h, vs[a]["uv"], vs[b]["uv"], col)
        fname = TEX[lab][1]
        tc.png_write(os.path.join(dst, fname + "_uv.png.tmp"), w, h, out)
        print(f"{fname}_uv.png")
    # glb
    g = GLB()
    g.doc["asset"]["generator"] = "modlab facekit.py"
    tex_index = {}
    mat_index = {}

    def material(mid):
        if mid in mat_index:
            return mat_index[mid]
        lab = mtex.get(mid)
        m = dict(name=f"{lab}_MAT_{mid}", pbrMetallicRoughness=dict(metallicFactor=0.0, roughnessFactor=1.0))
        if lab:
            if lab not in tex_index:
                bi, img = pix[lab]
                alpha = bi["fmt_label"] == "BC3"
                iv = g.view(tc.png_bytes(bi["w"], bi["h"], img, alpha))
                g.doc["images"].append(dict(name=lab, mimeType="image/png", bufferView=iv))
                g.doc["textures"].append(dict(sampler=0, source=len(g.doc["images"]) - 1))
                tex_index[lab] = len(g.doc["textures"]) - 1
            m["pbrMetallicRoughness"]["baseColorTexture"] = dict(index=tex_index[lab])
            if lab == "B34":
                m["alphaMode"], m["alphaCutoff"] = "MASK", 0.35
        g.doc["materials"].append(m)
        mat_index[mid] = len(g.doc["materials"]) - 1
        return mat_index[mid]

    summary = {}
    for name, label, mid, num in MESHES:
        m, prims = meshes[label]
        gp = []
        nparts = len(m["ibs"])
        for k, (vs, tris, mat, used) in enumerate(prims):
            part = k if nparts > 1 else 0
            n = len(vs)
            P = [v["p"] for v in vs]
            attr = dict(
                POSITION=g.acc(struct.pack(f"<{3 * n}f", *[x for p in P for x in p]), 5126, "VEC3", n,
                               minmax=([min(p[i] for p in P) for i in range(3)], [max(p[i] for p in P) for i in range(3)])),
                NORMAL=g.acc(struct.pack(f"<{3 * n}f", *[x for v in vs for x in v["n"]]), 5126, "VEC3", n),
                TEXCOORD_0=g.acc(struct.pack(f"<{2 * n}f", *[x for v in vs for x in v["uv"]]), 5126, "VEC2", n),
                TEXCOORD_1=g.acc(struct.pack(f"<{2 * n}f", *[x for i in used for x in (float(i), float(num * 10 + part))]), 5126, "VEC2", n),
            )
            idx = [x for a, b2, c in tris for x in (a, c, b2)]  # game winding -> glTF counter-clockwise
            p = dict(attributes=attr, indices=g.acc(struct.pack(f"<{len(idx)}H", *idx), 5123, "SCALAR", len(idx), target=34963),
                     mode=4, material=material(mat))
            gp.append(p)
            summary.setdefault(name, []).append((n, len(tris)))
        g.doc["meshes"].append(dict(name=name, primitives=gp))
        g.doc["nodes"].append(dict(name=name, mesh=len(g.doc["meshes"]) - 1,
                                   extras=dict(game_mesh_id=str(mid), label=label,
                                               lipsync="8 lip-sync offset shapes (kept)" if any(v["flags"] == 258 for v in m["vbs"]) else "none")))
        g.doc["scenes"][0]["nodes"].append(len(g.doc["nodes"]) - 1)
        print(f"{name}: " + ", ".join(f"{a} vertices / {b} triangles" for a, b in summary[name]))
    glb = os.path.join(dst, "face_kit.glb")
    g.save(glb + ".tmp")
    json.dump(summary, open(os.path.join(work, "facekit_summary.json"), "w"))
    print("wrote (as .tmp)", glb)


if __name__ == "__main__":
    main()
