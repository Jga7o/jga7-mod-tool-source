"""Blender hair kit (M15): output\\blender_kit\\hair_kit\\hair_kit.glb, for modelling one new whole hair.

Contents (game coordinates, like blender_kit.py: meters, +Y up, she faces +Z; Blender shows Z up, facing -Y):
- the P_AMICIA armature (340 joints, names from analysis\\bone_names.json); hair bones: head, hair1-hair5 (the braid's
  physics chain);
- head_game_M37: the game's face/head mesh with its skin texture (skinned, the bind pose);
- mannequin_head_M37 / mannequin_shoulders_M33: the mannequin's head (same shape, plain skin colour) and its shoulders
  (the shirt moved 15 mm inward), to judge length and clearance;
- weights_example_hair_braid_M02: the game's skinned braid with its real weights (head + hair1-5), to copy weights from;
- REF_<archive> groups (empties): every hair piece of every Amicia archive as a static reference object named
  <archive>_<label>_<what>, textured with the hair texture (B01; CHP1's neck hair with 1-00), plus the outfit pieces
  the hair has to clear (hood, ruffles, END's shoulder fur, the chapter I/II tops) in grey. Reference objects are not
  skinned and are never imported.
Usage: python -I hairkit.py [<dest dir>]
"""
import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paths  # noqa: E402  (the app's work folder and tables)
import fsx  # noqa: E402  (renames with a retry: Windows may hold a new file)
import skel  # noqa: E402
from bitmaps import decode  # noqa: E402
from blender_kit import GLB, SKEL_ID, add_mesh, add_skeleton, bone_names, load_mesh, png_bytes  # noqa: E402
from bmppreview import decode_mip, from_unpacked, pick_mip  # noqa: E402

OUT = paths.OUT
ARCH = [("P_AMICIA", "chapters_III-XVI"), ("P_AMICIA_CHP1", "chapter_I"), ("P_AMICIA_CHP2", "chapter_II"),
        ("P_AMICIA_END", "chapter_XVII"), ("P_AMICIA_UNIVERSITY", "university_unused")]
# outfit pieces next to the hair (grey references)
CLEAR = {"P_AMICIA": [("M32", "hood"), ("M13", "ruffles")], "P_AMICIA_END": [("E-29", "shoulder_fur")],
         "P_AMICIA_CHP1": [("C1-02", "top")], "P_AMICIA_CHP2": [("C2-10", "top")]}
WHAT = {"M02": "braid", "M34": "braid_core", "M07": "cap", "M08": "braid_tip", "M14": "braid_tip2", "M15": "main",
        "M01": "lock_right", "M06": "lock_left", "M09": "side_right", "M27": "side_left", "M16": "top_right",
        "M20": "top_left", "M19": "layer", "M24": "top_parting", "M40": "top_front", "M41": "upper"}


def static_mesh(g, name, prims, extras, parent):
    """A mesh node without skin (game bind-pose positions), child of the parent empty."""
    gp = []
    for vs, tris, mat in prims:
        n = len(vs)
        P = [v["p"] for v in vs]
        attr = dict(POSITION=g.acc(struct.pack(f"<{3 * n}f", *[x for p in P for x in p]), 5126, "VEC3", n,
                                   minmax=([min(p[i] for p in P) for i in range(3)], [max(p[i] for p in P) for i in range(3)])),
                    NORMAL=g.acc(struct.pack(f"<{3 * n}f", *[x for v in vs for x in v["n"]]), 5126, "VEC3", n),
                    TEXCOORD_0=g.acc(struct.pack(f"<{2 * n}f", *[x for v in vs for x in v["uv"]]), 5126, "VEC2", n))
        idx = [x for a, b2, c in tris for x in (a, c, b2)]
        gp.append(dict(attributes=attr, indices=g.acc(struct.pack(f"<{len(idx)}H", *idx), 5123, "SCALAR", len(idx), target=34963),
                       mode=4, material=mat))
    g.doc["meshes"].append(dict(name=name, primitives=gp))
    g.doc["nodes"].append(dict(name=name, mesh=len(g.doc["meshes"]) - 1, extras=extras))
    g.doc["nodes"][parent].setdefault("children", []).append(len(g.doc["nodes"]) - 1)


def texture(g, res, bid, name):
    for buf, d in from_unpacked(res):
        n = d["name"] - (1 << 64) if d["name"] >= 1 << 63 else d["name"]
        if n == bid:
            bi = decode(buf, d)
            off, w, h = pick_mip(bi, 1024)
            px, pw, _ = decode_mip(buf[d["body_pos"] + off:], bi["fmt"], w, h)
            iv = g.view(png_bytes(w, h, [px[y * pw + x] for y in range(h) for x in range(w)]))
            g.doc["images"].append(dict(name=name, mimeType="image/png", bufferView=iv))
            g.doc["textures"].append(dict(sampler=0, source=len(g.doc["images"]) - 1))
            return len(g.doc["textures"]) - 1
    raise ValueError(f"texture {bid} not in {res}")


def export_textures(dst, rows):
    """B01 (colour + alpha) and B02 (normal) at full size, and B01 with the base hair pieces' UV edges (yellow) drawn
    over a grey background, for laying out new hair UVs on the game's atlas (Route A of notes\\hair_plan.md)."""
    import texcodec as tc
    import texlib
    from facekit import line
    R = os.path.join(OUT, "P_AMICIA_unpacked", "resources")
    out = []
    for bid, fname in ((68700188137309512, "B01_hair"), (-6742760145741188495, "B02_hair_normal")):
        w, h, fmt, img = texlib.load(R, bid)
        tc.png_write(os.path.join(dst, fname + ".png.tmp"), w, h, img, alpha=True)
        fsx.replace(os.path.join(dst, fname + ".png.tmp"), os.path.join(dst, fname + ".png"))
        out.append(f"{fname}.png: {w}x{h} {fmt}, id {bid}")
        if fname == "B01_hair":
            ov = bytearray(img)
            for i in range(0, len(ov), 4):
                a = ov[i + 3]
                for c in range(3):
                    ov[i + c] = (ov[i + c] * a + 96 * (255 - a)) // 255
                ov[i + 3] = 255
            for r in rows:
                if r["archive"] == "P_AMICIA" and r["hair"]:
                    m, prims = load_mesh(os.path.join(R, f"{r['id']}.Mesh_Z"))
                    for vs, tris, _, _ in prims:
                        edges = {tuple(sorted((t[i], t[(i + 1) % 3]))) for t in tris for i in range(3)}
                        for a, c in edges:
                            pa, pc = [x % 1.0 for x in vs[a]["uv"]], [x % 1.0 for x in vs[c]["uv"]]
                            if max(abs(pa[0] - pc[0]), abs(pa[1] - pc[1])) < 0.5:   # skip edges cut by the fold
                                line(ov, w, h, pa, pc, (255, 230, 0))
            tc.png_write(os.path.join(dst, "B01_hair_uv.png.tmp"), w, h, ov)
            fsx.replace(os.path.join(dst, "B01_hair_uv.png.tmp"), os.path.join(dst, "B01_hair_uv.png"))
            out.append("B01_hair_uv.png: the base hair pieces' UVs (yellow; tiled UVs folded into 0..1)")
    return out


def main():
    dst = sys.argv[1] if len(sys.argv) > 1 else os.path.join(OUT, "blender_kit", "hair_kit")
    os.makedirs(dst, exist_ok=True)
    rows = json.load(open(os.path.join(paths.TABLES, "analysis", "m15_meshes.json")))
    R = os.path.join(OUT, "P_AMICIA_unpacked", "resources")
    bones = skel.load(os.path.join(R, f"{SKEL_ID}.Skel_Z"))
    g = GLB()
    g.doc["asset"]["generator"] = "modlab hairkit.py"
    add_skeleton(g, bones, bone_names(bones, OUT))
    t_hair = texture(g, R, 68700188137309512, "B01_hair")
    t_skin = texture(g, R, -5588537412492053000, "B05_face_skin")
    t_neck = texture(g, os.path.join(OUT, "P_AMICIA_CHP1_unpacked", "resources"), -300597880980788166, "1-00_chp1_neck_hair")
    pbr = lambda **k: dict(pbrMetallicRoughness=dict(metallicFactor=0.0, roughnessFactor=0.9, **k))
    g.doc["materials"] = [
        dict(name="hair_B01", alphaMode="MASK", alphaCutoff=0.4, doubleSided=True, **pbr(baseColorTexture=dict(index=t_hair))),
        dict(name="skin_B05", **pbr(baseColorTexture=dict(index=t_skin))),
        dict(name="mannequin_skin", **pbr(baseColorFactor=[0.80, 0.62, 0.52, 1])),
        dict(name="reference_grey", **pbr(baseColorFactor=[0.55, 0.55, 0.6, 1])),
        dict(name="neck_hair_1-00", alphaMode="MASK", alphaCutoff=0.4, doubleSided=True, **pbr(baseColorTexture=dict(index=t_neck)))]
    HAIR, SKIN, MANN, GREY, NECK = range(5)
    lines = []
    # head, mannequin, weights example (skinned to the armature)
    m, prims = load_mesh(os.path.join(R, "4840249479922744736.Mesh_Z"))
    add_mesh(g, "head_game_M37", [(vs, tris, SKIN) for vs, tris, _, _ in prims], dict(label="M37", reference=True))
    add_mesh(g, "mannequin_head_M37", [(vs, tris, MANN) for vs, tris, _, _ in prims], dict(label="M37", reference=True))
    m, prims = load_mesh(os.path.join(R, "3205405351813702291.Mesh_Z"))
    sh = []
    for vs, tris, _, _ in prims:
        nv = [dict(v, p=[v["p"][i] - v["n"][i] * 0.015 for i in range(3)]) for v in vs]
        tris = [t for t in tris if min(vs[i]["p"][1] for i in t) >= 1.0]
        keep = sorted({i for t in tris for i in t})
        re = {o: k for k, o in enumerate(keep)}
        sh.append(([nv[i] for i in keep], [tuple(re[i] for i in t) for t in tris], MANN))
    add_mesh(g, "mannequin_shoulders_M33_inset_15mm", sh, dict(label="M33", reference=True))
    m, prims = load_mesh(os.path.join(R, "-8880750388778566061.Mesh_Z"))
    add_mesh(g, "weights_example_hair_braid_M02", [(vs, tris, HAIR) for vs, tris, _, _ in prims],
             dict(label="M02", reference=True, note="the game's braid with its own weights (head, hair1-5)"))
    # reference groups per archive
    for arch, chap in ARCH:
        res = os.path.join(OUT, f"{arch}_unpacked", "resources")
        g.doc["nodes"].append(dict(name=f"REF_{arch}_{chap}"))
        parent = len(g.doc["nodes"]) - 1
        g.doc["scenes"][0]["nodes"].append(parent)
        short = arch.replace("P_AMICIA_", "").replace("P_AMICIA", "BASE")
        hair = [r for r in rows if r["archive"] == arch and any(x["tex"].get("colour", "").startswith(("B01", "1-00")) for x in r["mat"])]
        for r in hair:
            m, prims = load_mesh(os.path.join(res, f"{r['id']}.Mesh_Z"))
            mat = NECK if r["label"] == "C1-20" else HAIR
            what = WHAT.get(r["label"], "neck_hair" if r["label"] == "C1-20" else "hair")
            if arch != "P_AMICIA":
                base = next((b for b in rows if b["archive"] == "P_AMICIA" and b["vertices"] == r["vertices"] and b["hair"]), None)
                what = WHAT.get(base["label"], what) if base and r["label"] not in ("C2-02", "E-06", "U-42") else what
            name = f"{short}_{r['label']}_{what}"
            static_mesh(g, name, [(vs, tris, mat) for vs, tris, _, _ in prims],
                        dict(archive=arch, label=r["label"], game_mesh_id=str(r["id"]), reference=True), parent)
            lines.append(f"{name}: {r['vertices']} vertices, {r['triangles']} triangles, {'skinned' if r['stride'] == 48 else 'rigid'}")
        for lab, what in CLEAR.get(arch, []):
            r = next(x for x in rows if x["archive"] == arch and x["label"] == lab)
            m, prims = load_mesh(os.path.join(res, f"{r['id']}.Mesh_Z"))
            name = f"{short}_{lab}_{what}_clearance"
            static_mesh(g, name, [(vs, tris, GREY) for vs, tris, _, _ in prims],
                        dict(archive=arch, label=lab, reference=True), parent)
            lines.append(f"{name}: outfit reference")
    tmp = os.path.join(dst, "hair_kit.glb.tmp")
    g.save(tmp)
    lines += export_textures(dst, rows)
    print("\n".join(lines))
    print(f"wrote {tmp} ({os.path.getsize(tmp)} bytes); check with glbcheck.py, then rename")


if __name__ == "__main__":
    main()
