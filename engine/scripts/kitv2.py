"""Kits version 2 (M17 review): clean kit files for the modder, made from the version-1 kits (which are made from the
game files by blender_kit.py, hairkit.py, facekit.py, mockbody.py).

Rules (the user's): every object is REF_ (reference only: do not export, do not import or use) or YOUR_ (what the
modder edits or replaces, or exports with the model); short names; objects grouped under two empties "REFERENCE (do not
export)" and "EDIT" (glTF has no collections). The importer's own bone shapes (its glTF_not_exported collection) are
left as they are. The armature is "YOUR_armature": a plain parent node of the skeleton, so the importer names the armature
object after it; it is exported with the model and never changed.
Kits (output\\blender_kit_v2\\):
- "Amicia Clean slate + weight body"\\clean_slate.glb (M17 v4, was model_kit\\model_kit.glb): YOUR_armature;
  REF_mannequin (the weighted body to model on: one object), REF_example_cloth_leather, REF_example_cloth_fabric,
  REF_example_skin (the M16 example body).
- "Amicia Parts"\\amicia_parts.glb (was model_kit\\model_kit_game_pieces.glb): YOUR_armature + the 45 game pieces of
  the main chapters as REF_<label>_<part>: reference only.
- hair_kit\\hair_kit.glb: YOUR_armature, REF_head_bald, REF_hair_chapter_I / _chapter_II / _main_chapters /
  _final_chapters (each group's hair pieces merged into one object, no outfit pieces); the hair textures as PNG files.
- head_kit\\head_kit.glb: YOUR_head (with the ID map: the only editable mesh in version 1), REF_mouth, REF_lashes,
  REF_eyes (no ID map: never changed); no armature and no weights on purpose (only positions are written; the game's
  own weights stay); the head skin and iris images next to it.
No preview images in the kits (M17 v4).
Nothing else is copied: unused meshes, materials, images and buffer data are dropped.
Usage: python -I kitv2.py [<v1 kit dir> [<v2 kit dir>]]
"""
import json
import os
import shutil
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paths  # noqa: E402  (the app's work folder and tables)
import fsx  # noqa: E402  (renames with a retry: Windows may hold a new file)
OUT = paths.OUT
REF_GROUP = "REFERENCE (do not export)"
EDIT_GROUP = "EDIT"


def load(path):
    raw = open(path, "rb").read()
    jl = struct.unpack_from("<I", raw, 12)[0]
    doc = json.loads(raw[20:20 + jl])
    bl = struct.unpack_from("<I", raw, 20 + jl)[0]
    return doc, raw[28 + jl:28 + jl + bl]


class Builder:
    """Copies parts of source glTFs into one new glTF, remapping every index; only what is used is copied."""

    def __init__(self):
        self.doc = {"asset": {"version": "2.0", "generator": "kitv2.py (M17)"}, "scene": 0, "scenes": [{"nodes": []}],
                    "nodes": [], "meshes": [], "accessors": [], "bufferViews": [], "buffers": [], "materials": [],
                    "textures": [], "images": [], "samplers": [], "skins": []}
        self.blob = bytearray()
        self.memo = {}

    def _view(self, src, i):
        k = ("view", id(src[0]), i)
        if k not in self.memo:
            doc, blob = src
            v = dict(doc["bufferViews"][i])
            data = blob[v.get("byteOffset", 0):v.get("byteOffset", 0) + v["byteLength"]]
            while len(self.blob) % 4:
                self.blob.append(0)
            v.update(buffer=0, byteOffset=len(self.blob))
            self.blob += data
            self.doc["bufferViews"].append(v)
            self.memo[k] = len(self.doc["bufferViews"]) - 1
        return self.memo[k]

    def _acc(self, src, i):
        k = ("acc", id(src[0]), i)
        if k not in self.memo:
            a = dict(src[0]["accessors"][i])
            if "bufferView" in a:
                a["bufferView"] = self._view(src, a["bufferView"])
            if "sparse" in a:
                raise ValueError("sparse accessors are not supported")
            self.doc["accessors"].append(a)
            self.memo[k] = len(self.doc["accessors"]) - 1
        return self.memo[k]

    def _tex(self, src, i):
        k = ("tex", id(src[0]), i)
        if k not in self.memo:
            t = dict(src[0]["textures"][i])
            if "source" in t:
                kk = ("img", id(src[0]), t["source"])
                if kk not in self.memo:
                    im = dict(src[0]["images"][t["source"]])
                    if "bufferView" in im:
                        im["bufferView"] = self._view(src, im["bufferView"])
                    self.doc["images"].append(im)
                    self.memo[kk] = len(self.doc["images"]) - 1
                t["source"] = self.memo[kk]
            if "sampler" in t:
                self.doc["samplers"].append(dict(src[0]["samplers"][t["sampler"]]))
                t["sampler"] = len(self.doc["samplers"]) - 1
            self.doc["textures"].append(t)
            self.memo[k] = len(self.doc["textures"]) - 1
        return self.memo[k]

    def _mat(self, src, i):
        k = ("mat", id(src[0]), i)
        if k not in self.memo:
            m = json.loads(json.dumps(src[0]["materials"][i]))
            pbr = m.get("pbrMetallicRoughness", {})
            for key in ("baseColorTexture", "metallicRoughnessTexture"):
                if key in pbr:
                    pbr[key]["index"] = self._tex(src, pbr[key]["index"])
            for key in ("normalTexture", "occlusionTexture", "emissiveTexture"):
                if key in m:
                    m[key]["index"] = self._tex(src, m[key]["index"])
            self.doc["materials"].append(m)
            self.memo[k] = len(self.doc["materials"]) - 1
        return self.memo[k]

    def _joints(self, src, i, remap):
        """A JOINTS_0 accessor rewritten as u16 with remapped joint numbers (another skin's joint order)."""
        doc, blob = src
        a = doc["accessors"][i]
        v = doc["bufferViews"][a["bufferView"]]
        ct = a["componentType"]
        size = {5121: 1, 5123: 2}[ct]
        stride = v.get("byteStride", 4 * size)
        base = v.get("byteOffset", 0) + a.get("byteOffset", 0)
        fmt = "<4B" if ct == 5121 else "<4H"
        out = bytearray()
        for k in range(a["count"]):
            out += struct.pack("<4H", *[remap[j] for j in struct.unpack_from(fmt, blob, base + k * stride)])
        while len(self.blob) % 4:
            self.blob.append(0)
        self.doc["bufferViews"].append(dict(buffer=0, byteOffset=len(self.blob), byteLength=len(out)))
        self.blob += out
        self.doc["accessors"].append(dict(bufferView=len(self.doc["bufferViews"]) - 1, componentType=5123, count=a["count"], type="VEC4"))
        return len(self.doc["accessors"]) - 1

    def prims(self, src, node_name, remap=None, drop=()):
        doc = src[0]
        ni = next((i for i, n in enumerate(doc["nodes"]) if n.get("name") == node_name), None)
        if ni is None:
            raise ValueError(f"no node '{node_name}'")
        n = doc["nodes"][ni]
        if any(k in n for k in ("matrix", "translation", "rotation", "scale")):
            raise ValueError(f"'{node_name}' has its own transform (not supported: the kits use game coordinates)")
        out = []
        for p in doc["meshes"][n["mesh"]]["primitives"]:
            q = dict(p)
            q["attributes"] = {a: (self._joints(src, x, remap) if remap and a == "JOINTS_0" else self._acc(src, x))
                               for a, x in p["attributes"].items() if a not in drop}
            if "indices" in p:
                q["indices"] = self._acc(src, p["indices"])
            if "material" in p:
                q["material"] = self._mat(src, p["material"])
            q.pop("targets", None)
            out.append(q)
        return out, n.get("skin") is not None, n.get("extras")

    def node(self, name, parent=None, **kw):
        self.doc["nodes"].append(dict(name=name, **kw))
        i = len(self.doc["nodes"]) - 1
        if parent is None:
            self.doc["scenes"][0]["nodes"].append(i)
        else:
            self.doc["nodes"][parent].setdefault("children", []).append(i)
        return i

    def skeleton(self, src, parent):
        """Copy the source's skin joints (whole joint subtree) under parent; returns the new skin index."""
        doc = src[0]
        sk = doc["skins"][0]
        joints = sk["joints"]
        jset = set(joints)
        new = {}
        for j in joints:   # joint nodes keep their names and local matrices
            n = {k: v for k, v in doc["nodes"][j].items() if k != "children"}
            self.doc["nodes"].append(n)
            new[j] = len(self.doc["nodes"]) - 1
        for j in joints:
            ch = [new[c] for c in doc["nodes"][j].get("children", []) if c in jset]
            if ch:
                self.doc["nodes"][new[j]]["children"] = ch
        roots = [j for j in joints if not any(j in doc["nodes"][p].get("children", []) for p in joints)]
        for r in roots:
            self.doc["nodes"][parent].setdefault("children", []).append(new[r])
        self.doc["skins"].append(dict(joints=[new[j] for j in joints], skeleton=new[roots[0]],
                                      inverseBindMatrices=self._acc(src, sk["inverseBindMatrices"])))
        return len(self.doc["skins"]) - 1

    def mesh_node(self, name, parent, parts, skin=None, extras=None, remap=None, drop=()):
        """parts: [(src, node name)]: their primitives become one object; remap: {source joint number: kit joint number};
        drop: vertex attributes left out."""
        prims, skinned = [], False
        for src, nn in parts:
            p, s, _ = self.prims(src, nn, remap, drop)
            prims += p
            skinned |= s
        self.doc["meshes"].append(dict(name=name, primitives=prims))
        kw = dict(mesh=len(self.doc["meshes"]) - 1)
        if skinned:
            if skin is None:
                raise ValueError(f"{name}: skinned parts need the armature in this kit")
            kw["skin"] = skin
        if extras:
            kw["extras"] = extras
        return self.node(name, parent, **kw)

    def write(self, path):
        for k in ("materials", "textures", "images", "samplers", "skins"):
            if not self.doc[k]:
                del self.doc[k]
        while len(self.blob) % 4:
            self.blob.append(0)
        self.doc["buffers"] = [{"byteLength": len(self.blob)}]
        js = json.dumps(self.doc, separators=(",", ":")).encode()
        js += b" " * (-len(js) % 4)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(struct.pack("<III", 0x46546C67, 2, 28 + len(js) + len(self.blob)))
            f.write(struct.pack("<II", len(js), 0x4E4F534A) + js)
            f.write(struct.pack("<II", len(self.blob), 0x004E4942) + bytes(self.blob))
        fsx.replace(tmp, path)


def groups(b):
    return b.node(REF_GROUP), b.node(EDIT_GROUP)


def names(src):
    return [n.get("name") for n in src[0]["nodes"] if "mesh" in n]


CLEAN_SLATE = "Amicia Clean slate + weight body"   # M17 v4 folder names (the user's)
PARTS = "Amicia Parts"


def model_kit(v1, v2):
    man, mock, base = load(os.path.join(v1, "mannequin.glb")), load(os.path.join(v1, "mock_body_m16.glb")), \
        load(os.path.join(v1, "amicia_base_chapter3.glb"))
    b = Builder()
    ref, edit = groups(b)
    arm = b.node("YOUR_armature", edit)
    skin = b.skeleton(man, arm)
    b.mesh_node("REF_mannequin", ref, [(man, n) for n in names(man)], skin, {"kit": "body proxy to model on (head and hands are the game's)"})
    kit_j = {man[0]["nodes"][j]["name"]: n for n, j in enumerate(man[0]["skins"][0]["joints"])}
    remap = {n: kit_j[mock[0]["nodes"][j]["name"]] for n, j in enumerate(mock[0]["skins"][0]["joints"])}   # by bone name
    for nn, new in (("leather", "REF_example_cloth_leather"), ("fabric", "REF_example_cloth_fabric"), ("skin", "REF_example_skin")):
        b.mesh_node(new, ref, [(mock, nn)], skin, {"kit": "example body (M16 rehearsal)"}, remap)
    os.makedirs(os.path.join(v2, CLEAN_SLATE), exist_ok=True)
    b.write(os.path.join(v2, CLEAN_SLATE, "clean_slate.glb"))
    g = Builder()
    ref, edit = groups(g)
    arm = g.node("YOUR_armature", edit)
    skin = g.skeleton(base, arm)
    for nn in names(base):
        part, lab = nn.split("_M")[0], "M" + nn.split("_M")[1].split("_")[0]
        g.mesh_node(f"REF_{lab}_{part}", ref, [(base, nn)], skin, {"label": lab})
    os.makedirs(os.path.join(v2, PARTS), exist_ok=True)
    g.write(os.path.join(v2, PARTS, "amicia_parts.glb"))


def hair_kit(v1, v2):
    src = load(os.path.join(v1, "hair_kit", "hair_kit.glb"))
    doc = src[0]
    kids = {doc["nodes"][i]["name"]: [doc["nodes"][c]["name"] for c in doc["nodes"][i].get("children", [])]
            for i in range(len(doc["nodes"])) if doc["nodes"][i].get("name", "").startswith("REF_P_AMICIA")}

    def hair_of(group):
        return [n for n in kids[group] if "clearance" not in n]
    b = Builder()
    ref, edit = groups(b)
    arm = b.node("YOUR_armature", edit)
    skin = b.skeleton(src, arm)
    b.mesh_node("REF_head_bald", ref, [(src, "head_game_M37")], skin, {"kit": "the game's head without hair"})
    for grp, new in (("REF_P_AMICIA_CHP1_chapter_I", "REF_hair_chapter_I"), ("REF_P_AMICIA_CHP2_chapter_II", "REF_hair_chapter_II"),
                     ("REF_P_AMICIA_chapters_III-XVI", "REF_hair_main_chapters"),
                     ("REF_P_AMICIA_END_chapter_XVII", "REF_hair_final_chapters")):
        b.mesh_node(new, ref, [(src, n) for n in hair_of(grp)], None, {"kit": f"the game's hair, {len(hair_of(grp))} pieces"})
    os.makedirs(os.path.join(v2, "hair_kit"), exist_ok=True)
    b.write(os.path.join(v2, "hair_kit", "hair_kit.glb"))
    for f in os.listdir(os.path.join(v1, "hair_kit")):
        if f.endswith(".png") and not f.endswith("_uv.png"):
            shutil.copy2(os.path.join(v1, "hair_kit", f), os.path.join(v2, "hair_kit", f))


HEAD_IMAGES = {"B05_face_skin.png": "B05_head_skin.png", "B31_iris.png": "B31_iris.png"}


def head_kit(v1, v2):
    """M17 v4: the head kit (was the face kit), made from the version-1 face kit. No weights: only positions are written."""
    src = load(os.path.join(v1, "face_kit", "face_kit.glb"))
    b = Builder()
    ref, edit = groups(b)
    nowt = ("JOINTS_0", "WEIGHTS_0")
    _, _, ex = b.prims(src, "face_M37")   # extras only (the primitives are copied below)
    b.mesh_node("YOUR_head", edit, [(src, "face_M37")], None, ex, drop=nowt)
    for nn, new, what in (("mouth_M26", "REF_mouth", "the inside of the mouth"), ("lashes_M36", "REF_lashes", "the eyelashes"),
                          ("eyes_M28", "REF_eyes", "the eyes (the eye colour is an image)")):   # v1: only the head is edited
        b.mesh_node(new, ref, [(src, nn)], None, {"kit": f"{what}: reference only"}, drop=nowt + ("TEXCOORD_1",))
    os.makedirs(os.path.join(v2, "head_kit"), exist_ok=True)
    b.write(os.path.join(v2, "head_kit", "head_kit.glb"))
    for f, new in HEAD_IMAGES.items():
        shutil.copy2(os.path.join(v1, "face_kit", f), os.path.join(v2, "head_kit", new))


def main():
    v1 = sys.argv[1] if len(sys.argv) > 1 else os.path.join(OUT, "blender_kit")
    v2 = sys.argv[2] if len(sys.argv) > 2 else os.path.join(OUT, "blender_kit_v2")
    model_kit(v1, v2)
    hair_kit(v1, v2)
    head_kit(v1, v2)
    print("wrote", v2)


if __name__ == "__main__":
    main()
