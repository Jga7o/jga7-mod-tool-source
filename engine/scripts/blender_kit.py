"""Build the Blender kit: amicia_base_chapter3.glb (all P_AMICIA meshes, skinned to the decoded skeleton) and
mannequin.glb (a body proxy made by insetting clothing shells, plus head and hands as reference).

Coordinates are the game's, unchanged: meters, +Y up, Amicia faces +Z, her left is +X (right-handed, as glTF).
Blender's glTF importer turns this into Z up with Amicia facing -Y (she faces the Front view).
Skeleton (skel.py): 340 joints, node i = game bone i, names from analysis/bone_names.json (asobo64 hashes matched to
Mixamo-style names) or bone_NNN. Joint transforms are rotation + translation only (rotation from the local quaternion
chain, translation from the stored world bind); the seven head-attachment bones with scale -1 / 0.711 (269-282) are
stored without scale. Inverse bind matrices are the exact inverses, so the rest pose shows every mesh in the game's
bind pose. Vertex groups: JOINTS_0 (u16) + WEIGHTS_0 (float = u16 / 65535; rigid stride-44 hair = one bone, 1.0).
Meshes: one node per Mesh_Z named <part>_<Mxx>_<mesh id>, one primitive per submesh, material = the submesh's base-color
texture (1024 px mip, PNG embedded). Extras on each node: game mesh id, label, vertex/triangle budget.
Triangles are wound counter-clockwise (glTF); the game's are clockwise, so they are flipped on export.

Usage: python -I blender_kit.py <output_dir>
"""
import json
import math
import os
import struct
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths  # noqa: E402  (the app's work folder and tables)
import skel  # noqa: E402
from bitmaps import decode  # noqa: E402
from bmppreview import decode_mip, from_unpacked, pick_mip  # noqa: E402
from meshfmt import parse  # noqa: E402

PART = {
    "M00": "hands", "M01": "hair_lock_right", "M02": "hair_braid", "M03": "ground_strip_B03", "M04": "belt_piece_hip",
    "M05": "belt_layer_B43_unseen", "M06": "hair_lock_left", "M07": "hair_cap", "M08": "hair_braid_tip", "M09": "hair_side_right",
    "M10": "belt_piece_hip_low", "M11": "belt_piece_hip_small", "M12": "hood_front_clasp", "M13": "hood_ruffles",
    "M14": "hair_braid_tip2", "M15": "hair_main", "M16": "hair_top_right", "M17": "belt_piece_hip", "M18": "pants",
    "M19": "hair_layer", "M20": "hair_top_left", "M21": "belt_waist_thighs", "M22": "shield_right_elbow",
    "M23": "belt_waist_thighs", "M24": "hair_top", "M25": "corset", "M26": "teeth_mouth", "M27": "hair_side_left",
    "M28": "eyes", "M29": "wrap_right_forearm", "M30": "neck_clothes_rolled_sleeves", "M31": "belt_piece_hip",
    "M32": "hood", "M33": "shirt", "M34": "hair_braid_back", "M35": "belt_piece_hip_low", "M36": "eyelashes",
    "M37": "face_head", "M38": "boots", "M39": "belt_piece_hip_small", "M40": "hair_top_front", "M41": "hair_upper",
    "M42": "wrap_right_hand", "M43": "belt_waist_thighs", "M44": "belt_piece_hip",
}
BASE = {"B01", "B03", "B05", "B10", "B12", "B15", "B18", "B20", "B21", "B24", "B25", "B27", "B29", "B31", "B34", "B37", "B40", "B43"}
ALPHA = {"B01", "B34"}  # hair strands, lashes: alpha-tested
SKEL_ID = -3311511499466089408


# ---------- 4x4 helpers (column-vector math, lists of rows) ----------
def mat_from_rows(R, t):
    """Game row-vector rotation rows R and translation t -> column-vector 4x4."""
    return [[R[0][0], R[1][0], R[2][0], t[0]], [R[0][1], R[1][1], R[2][1], t[1]], [R[0][2], R[1][2], R[2][2], t[2]], [0, 0, 0, 1]]


def mmul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def rigid_inv(m):
    R = [[m[j][i] for j in range(3)] for i in range(3)]
    t = [-sum(R[i][k] * m[k][3] for k in range(3)) for i in range(3)]
    return [R[0] + [t[0]], R[1] + [t[1]], R[2] + [t[2]], [0, 0, 0, 1]]


def colmajor(m):
    return [m[r][c] for c in range(4) for r in range(4)]


def joint_worlds(bones):
    comp = skel.compose(bones)
    return [mat_from_rows(comp[b["index"]][0], skel.world_pos(b)) for b in bones]


def bone_names(bones, out_dir):
    p = os.path.join(paths.TABLES, "analysis", "bone_names.json")
    known = {int(k): v for k, v in json.load(open(p)).items()} if os.path.exists(p) else {}
    return [known.get(b["index"], f"bone_{b['index']:03d}") for b in bones]


# ---------- glb writer ----------
class GLB:
    def __init__(self):
        self.bin = bytearray()
        self.doc = dict(asset=dict(version="2.0", generator="modlab blender_kit.py"), scene=0, scenes=[dict(nodes=[])],
                        nodes=[], meshes=[], accessors=[], bufferViews=[], buffers=[], materials=[], textures=[],
                        images=[], samplers=[dict(magFilter=9729, minFilter=9987, wrapS=10497, wrapT=10497)], skins=[])

    def view(self, data, target=None):
        while len(self.bin) % 4:
            self.bin.append(0)
        v = dict(buffer=0, byteOffset=len(self.bin), byteLength=len(data))
        if target:
            v["target"] = target
        self.bin.extend(data)
        self.doc["bufferViews"].append(v)
        return len(self.doc["bufferViews"]) - 1

    def acc(self, data, ctype, typ, count, normalized=False, minmax=None, target=34962):
        a = dict(bufferView=self.view(data, target), componentType=ctype, count=count, type=typ)
        if normalized:
            a["normalized"] = True
        if minmax:
            a["min"], a["max"] = minmax
        self.doc["accessors"].append(a)
        return len(self.doc["accessors"]) - 1

    def save(self, path):
        doc = {k: v for k, v in self.doc.items() if v != []}
        doc["buffers"] = [dict(byteLength=len(self.bin))]
        js = json.dumps(doc, separators=(",", ":")).encode()
        js += b" " * (-len(js) % 4)
        bn = bytes(self.bin) + b"\0" * (-len(self.bin) % 4)
        out = struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(bn))
        out += struct.pack("<II", len(js), 0x4E4F534A) + js + struct.pack("<II", len(bn), 0x004E4942) + bn
        open(path, "wb").write(out)


def png_bytes(W, H, rows_rgba):
    raw = b"".join(b"\0" + bytes(c for p in rows_rgba[y * W:(y + 1) * W] for c in p) for y in range(H))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def add_skeleton(g, bones, names):
    W = joint_worlds(bones)
    for b in bones:
        local = W[b["index"]] if b["parent"] < 0 else mmul(rigid_inv(W[b["parent"]]), W[b["index"]])
        g.doc["nodes"].append(dict(name=names[b["index"]], matrix=colmajor(local), extras=dict(game_bone=b["index"])))
    for b in bones:
        kids = [c["index"] for c in bones if c["parent"] == b["index"]]
        if kids:
            g.doc["nodes"][b["index"]]["children"] = kids
    ibm = b"".join(struct.pack("<16f", *colmajor(rigid_inv(w))) for w in W)
    ia = g.acc(ibm, 5126, "MAT4", len(bones), target=None)
    g.doc["skins"].append(dict(name="amicia_skeleton", joints=list(range(len(bones))), inverseBindMatrices=ia, skeleton=0))
    g.doc["scenes"][0]["nodes"].append(0)


def mesh_vertices(b, vb):
    """Vertex dicts of one geometry block (game layout, see mesh_format.md)."""
    st = vb["stride"]
    out = []
    for k in range(vb["count"]):
        o = vb["pos"] + k * st
        t = struct.unpack_from("<4b", b, o + 12)
        n = struct.unpack_from("<4b", b, o + 16)
        bi = struct.unpack_from("<4H", b, o + 28)
        if st == 48:
            w = [x / 65535 for x in struct.unpack_from("<4H", b, o + 36)]
        else:
            w = [struct.unpack_from("<f", b, o + 36)[0], 0.0, 0.0, 0.0]
            bi = (bi[0], 0, 0, 0)
        nl = math.sqrt(sum(x * x for x in n[:3])) or 1
        tl = math.sqrt(sum(x * x for x in t[:3])) or 1
        out.append(dict(p=struct.unpack_from("<3f", b, o), n=[x / nl for x in n[:3]], t=[x / tl for x in t[:3]] + [1.0 if t[3] > 0 else -1.0],
                        uv=struct.unpack_from("<2e", b, o + 20), j=bi, w=w, c=b[o + st - 4:o + st]))
    return out


def add_mesh(g, name, prims, extras, skin=0):
    """prims: [(vertices, triangles(game winding), material index or None)]."""
    gp = []
    for vs, tris, mat in prims:
        n = len(vs)
        P = [v["p"] for v in vs]
        attr = dict(
            POSITION=g.acc(struct.pack(f"<{3 * n}f", *[x for p in P for x in p]), 5126, "VEC3", n,
                           minmax=([min(p[i] for p in P) for i in range(3)], [max(p[i] for p in P) for i in range(3)])),
            NORMAL=g.acc(struct.pack(f"<{3 * n}f", *[x for v in vs for x in v["n"]]), 5126, "VEC3", n),
            TANGENT=g.acc(struct.pack(f"<{4 * n}f", *[x for v in vs for x in v["t"]]), 5126, "VEC4", n),
            TEXCOORD_0=g.acc(struct.pack(f"<{2 * n}f", *[x for v in vs for x in v["uv"]]), 5126, "VEC2", n),
            COLOR_0=g.acc(bytes(x for v in vs for x in v["c"]), 5121, "VEC4", n, normalized=True),
            JOINTS_0=g.acc(struct.pack(f"<{4 * n}H", *[x for v in vs for x in v["j"]]), 5123, "VEC4", n),
            WEIGHTS_0=g.acc(struct.pack(f"<{4 * n}f", *[x for v in vs for x in v["w"]]), 5126, "VEC4", n),
        )
        idx = [x for a, b2, c in tris for x in (a, c, b2)]
        p = dict(attributes=attr, indices=g.acc(struct.pack(f"<{len(idx)}H", *idx), 5123, "SCALAR", len(idx), target=34963), mode=4)
        if mat is not None:
            p["material"] = mat
        gp.append(p)
    g.doc["meshes"].append(dict(name=name, primitives=gp))
    g.doc["nodes"].append(dict(name=name, mesh=len(g.doc["meshes"]) - 1, skin=skin, extras=extras))
    g.doc["scenes"][0]["nodes"].append(len(g.doc["nodes"]) - 1)


def load_mesh(path):
    b = open(path, "rb").read()
    m = parse(b)
    geo = [v for v in m["vbs"] if v["flags"] == 2]
    lh = struct.unpack_from("<I", b, 0x2C)[0]
    idx_all = [struct.unpack_from(f"<{ib['count']}H", b, ib["pos"]) for ib in m["ibs"]]
    prims = []
    for k, s in enumerate(m["subs"]):
        vb = geo[0] if len(m["ibs"]) == 1 else geo[k]
        idx = idx_all[0] if len(m["ibs"]) == 1 else idx_all[k]
        vs = mesh_vertices(b, vb)
        seg = idx[s["start"]:s["start"] + 3 * s["ntri"]]
        used = sorted(set(seg))                     # keep only this submesh's vertices
        remap = {o: i for i, o in enumerate(used)}
        tris = [(remap[seg[t]], remap[seg[t + 1]], remap[seg[t + 2]]) for t in range(0, len(seg), 3)]
        mat = struct.unpack_from("<q", b, 64 + 138 + 8 * min(k, (lh - 222) // 16))[0]
        prims.append(([vs[i] for i in used], tris, mat, used))
    return m, prims


def textures(out_dir):
    lab = {}
    for line in open(os.path.join(paths.TABLES, "previews", "P_AMICIA_bitmaps.tsv")):
        p = line.split("\t")
        if p[0] != "label":
            lab[int(p[1])] = p[0]
    pix = {}
    for buf, d in from_unpacked(os.path.join(out_dir, "P_AMICIA_unpacked", "resources")):
        n = d["name"] - (1 << 64) if d["name"] >= 1 << 63 else d["name"]
        if lab.get(n) in BASE:
            pix[n] = (buf, d)
    return lab, pix


def build_base(out_dir, dst):
    R = os.path.join(out_dir, "P_AMICIA_unpacked", "resources")
    bones = skel.load(os.path.join(R, f"{SKEL_ID}.Skel_Z"))
    names = bone_names(bones, out_dir)
    refs = {}
    for line in open(os.path.join(paths.TABLES, "pamicia_refs.tsv")):
        a, ac, b, bc = line.rstrip("\n").split("\t")
        refs.setdefault(int(a), []).append((int(b), bc))
    lab, pix = textures(out_dir)
    g = GLB()
    add_skeleton(g, bones, names)
    mat_index, tex_index = {}, {}

    def material(mid):
        if mid in mat_index:
            return mat_index[mid]
        base = [t for t, c in refs.get(mid, []) if c == "Bitmap_Z" and lab.get(t) in BASE]
        m = dict(name=f"MAT_{mid}", pbrMetallicRoughness=dict(metallicFactor=0.0, roughnessFactor=1.0))
        if base:
            t = base[0]
            if t not in tex_index:
                buf, d = pix[t]
                bi = decode(buf, d)
                off, w, h = pick_mip(bi, 1024)
                px, pw, _ = decode_mip(buf[d["body_pos"] + off:], bi["fmt"], w, h)
                rows = [px[y * pw + x] for y in range(h) for x in range(w)]
                iv = g.view(png_bytes(w, h, rows))
                g.doc["images"].append(dict(name=lab[t], mimeType="image/png", bufferView=iv))
                g.doc["textures"].append(dict(sampler=0, source=len(g.doc["images"]) - 1))
                tex_index[t] = len(g.doc["textures"]) - 1
            m["name"] = f"{lab[t]}_MAT_{mid}"
            m["pbrMetallicRoughness"]["baseColorTexture"] = dict(index=tex_index[t])
            if lab[t] in ALPHA:
                m["alphaMode"], m["alphaCutoff"] = "MASK", 0.35
        g.doc["materials"].append(m)
        mat_index[mid] = len(g.doc["materials"]) - 1
        return mat_index[mid]

    rows = [l.split("\t") for l in open(os.path.join(paths.TABLES, "analysis", "P_AMICIA_parts.tsv")).read().split("\n")[1:] if l]
    for r in rows:
        label, mid = r[0], int(r[1])
        m, prims = load_mesh(os.path.join(R, f"{mid}.Mesh_Z"))
        vb, ib = m["vbs"][0], m["ibs"][0]
        name = f"{PART[label]}_{label}_{mid}"
        extras = dict(game_mesh_id=str(mid), label=label, part=PART[label], budget_vertices=vb["count"],
                      budget_triangles=sum(i["count"] for i in m["ibs"]) // 3, submeshes=len(m["subs"]),
                      morph_blocks=sum(v["flags"] != 2 for v in m["vbs"]))
        add_mesh(g, name, [(vs, tris, material(mat)) for vs, tris, mat, _ in prims], extras)
        print(f"  {name}: {sum(len(p[0]) for p in prims)} vertices, {sum(len(p[1]) for p in prims)} triangles")
    g.save(dst)
    return len(rows)


def build_mannequin(out_dir, dst):
    """Body proxy: clothing shells moved inward along their game normals (weights copied), head and hands unchanged."""
    R = os.path.join(out_dir, "P_AMICIA_unpacked", "resources")
    bones = skel.load(os.path.join(R, f"{SKEL_ID}.Skel_Z"))
    g = GLB()
    add_skeleton(g, bones, bone_names(bones, out_dir))
    g.doc["materials"] = [dict(name="proxy_skin", pbrMetallicRoughness=dict(baseColorFactor=[0.80, 0.62, 0.52, 1], metallicFactor=0, roughnessFactor=0.8)),
                          dict(name="reference", pbrMetallicRoughness=dict(baseColorFactor=[0.55, 0.55, 0.6, 1], metallicFactor=0, roughnessFactor=0.9))]
    parts = [("torso_arms", "M33", 3205405351813702291, 0.015, 0.88), ("waist", "M25", 344828307885556348, 0.022, None),
             ("neck_forearm_cuffs", "M30", 2090610271972057191, 0.008, None), ("legs", "M18", -1012814938175630016, 0.012, None),
             ("shins_feet", "M38", 4897067890524969731, 0.010, None), ("ref_head", "M37", 4840249479922744736, 0.0, None),
             ("ref_hands", "M00", -9088462236453466050, 0.0, None)]
    for part, label, mid, inset, ymin in parts:  # ymin: drop triangles below (the shirt's tunic skirt is not body)
        m, prims = load_mesh(os.path.join(R, f"{mid}.Mesh_Z"))
        out = []
        for vs, tris, mat, _ in prims:
            nv = [dict(v, p=[v["p"][i] - v["n"][i] * inset for i in range(3)]) for v in vs]
            if ymin is not None:
                tris = [t for t in tris if min(vs[i]["p"][1] for i in t) >= ymin]
                keep = sorted({i for t in tris for i in t})
                re = {o: k for k, o in enumerate(keep)}
                nv, tris = [nv[i] for i in keep], [tuple(re[i] for i in t) for t in tris]
            out.append((nv, tris, 1 if part.startswith("ref") else 0))
        name = f"mannequin_{part}_from_{label}" + (f"_inset_{int(inset * 1000)}mm" if inset else "_unchanged")
        add_mesh(g, name, out, dict(source_mesh=label, source_id=str(mid), inset_m=inset))
        print(f"  {name}")
    g.save(dst)


def main():
    out = sys.argv[1]
    kit = os.path.join(out, "blender_kit")
    os.makedirs(kit, exist_ok=True)
    n = build_base(out, os.path.join(kit, "amicia_base_chapter3.glb.tmp"))
    build_mannequin(out, os.path.join(kit, "mannequin.glb.tmp"))
    print(f"wrote {n} meshes + mannequin (temporary names; run glbcheck.py, then rename)")


if __name__ == "__main__":
    main()
