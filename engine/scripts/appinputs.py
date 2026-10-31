"""M17 session 1: the app's inputs -> working copies the engine builds from (plan §1.15, §0.2 v5, §18). The user's files
are only read; everything is written into a work folder.

model(cloth, skin, out)  one body.glb with two objects, "Cloth" and "Skin":
  - each file's skinned mesh objects are joined into one object (their primitives, unchanged); they must share one
    material (the same images), else an error;
  - the Cloth's joints are mapped by name onto the Skin file's armature, which must be the same (names, rest transforms
    and inverse binds); its material, textures and images are copied;
  - images chosen on the page replace the material's own: colour, normal, roughness / metallic (separate grey images are
    combined into one metallic-roughness image: G = roughness, B = metalness, factors 1).
hair(src, out)  hair.glb with one object "hair" (joined as above) and the hair images: colour + alpha (grey), or one RGBA
  image, or (none given) the glb's own base colour image; written as hair_colour.png + hair_alpha.png at 2048 (the hair
  textures' size: resampled with a note when another size); an optional normal map is passed on as it is.
Texture size per part: the size of its colour image (square; 1024, 2048 or 4096; larger is an error, a non-square image a
note). Every finding is {level, code, text, advice} (the codes are listed in CODES).
"""
import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fsx  # noqa: E402  (renames with a retry: Windows may hold a new file)
import texcodec  # noqa: E402

MAX_TEX = 4096
HAIR_SIZE = 2048
IRIS_SIZE = 512
CODES = ("read_failed", "gltf_uri", "no_mesh", "no_skin_weights", "several_materials", "armatures_differ", "joint_missing",
         "image_read", "image_too_large", "image_not_square", "image_resized", "image_size_mismatch", "iris_size")


class InputError(Exception):
    def __init__(self, code, text, advice=""):
        super().__init__(text)
        self.item = dict(level="ERROR", code=code, text=text, advice=advice)


def note(notes, code, text, advice=""):
    notes.append(dict(level="WARNING", code=code, text=text, advice=advice))


# ---------- glTF in and out (one embedded buffer) ----------
def load(path):
    """(doc, blob) with every buffer and image embedded in one blob: .glb, or .gltf with its .bin and image files."""
    try:
        raw = open(path, "rb").read()
        if raw[:4] == b"glTF":
            n = struct.unpack_from("<I", raw, 12)[0]
            doc = json.loads(raw[20:20 + n])
            blob = bytearray()
            if len(raw) > 28 + n:
                bl = struct.unpack_from("<I", raw, 20 + n)[0]
                blob = bytearray(raw[28 + n:28 + n + bl])
            return doc, blob
        doc = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError, struct.error) as e:
        raise InputError("read_failed", f"{os.path.basename(path)} cannot be read: {e}",
                         "export again as glTF Binary (.glb) or glTF Separate (.gltf + .bin + textures)")
    base = os.path.dirname(os.path.abspath(path))
    blob, starts = bytearray(), []
    for b in doc.get("buffers", []):
        uri = b.get("uri", "")
        if not uri or uri.startswith("data:"):
            raise InputError("gltf_uri", f"{os.path.basename(path)}: embedded data in a .gltf is not supported",
                             "export as glTF Binary (.glb) or glTF Separate")
        blob += b"\0" * (-len(blob) % 4)
        starts.append(len(blob))
        blob += open(os.path.join(base, uri.replace("/", os.sep)), "rb").read()
    for v in doc.get("bufferViews", []):
        v["byteOffset"] = v.get("byteOffset", 0) + starts[v.get("buffer", 0)]
        v["buffer"] = 0
    for im in doc.get("images", []):
        if "uri" in im:
            data = open(os.path.join(base, im.pop("uri").replace("/", os.sep)), "rb").read()
            im["bufferView"] = add_view(doc, blob, data)
            im.setdefault("mimeType", "image/png" if data[:4] == b"\x89PNG" else "image/jpeg")
    doc["buffers"] = [dict(byteLength=len(blob))]
    return doc, blob


def save(path, doc, blob):
    doc["buffers"] = [dict(byteLength=len(blob) + (-len(blob) % 4))]
    js = json.dumps(doc, separators=(",", ":")).encode()
    js += b" " * (-len(js) % 4)
    blob = bytes(blob) + b"\0" * (-len(blob) % 4)
    out = struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(blob))
    out += struct.pack("<II", len(js), 0x4E4F534A) + js + struct.pack("<II", len(blob), 0x004E4942) + blob
    open(path + ".tmp", "wb").write(out)
    fsx.replace(path + ".tmp", path)


def add_view(doc, blob, data):
    blob += b"\0" * (-len(blob) % 4)
    doc.setdefault("bufferViews", []).append(dict(buffer=0, byteOffset=len(blob), byteLength=len(data)))
    blob += data
    return len(doc["bufferViews"]) - 1


def view_bytes(doc, blob, bvi):
    v = doc["bufferViews"][bvi]
    return bytes(blob[v.get("byteOffset", 0):v.get("byteOffset", 0) + v["byteLength"]])


# ---------- joining a file's objects into one ----------
def tex_key(doc, blob, mat):
    """What a material draws with: its texture images' bytes and factors (two materials with the same key are the same)."""
    pbr = mat.get("pbrMetallicRoughness", {})
    key = []
    for ref in (pbr.get("baseColorTexture"), mat.get("normalTexture"), pbr.get("metallicRoughnessTexture")):
        if ref is None:
            key.append(None)
        else:
            im = doc["images"][doc["textures"][ref["index"]]["source"]]
            key.append(hash(view_bytes(doc, blob, im["bufferView"])) if "bufferView" in im else im.get("uri"))
    key += [json.dumps(pbr.get(k)) for k in ("baseColorFactor", "metallicFactor", "roughnessFactor")]
    return tuple(key)


def join(doc, blob, label, name):
    """All skinned mesh objects of the file joined into the first one, renamed `name`; returns that node's index."""
    nodes = [i for i, n in enumerate(doc.get("nodes", [])) if "mesh" in n]
    if not nodes:
        raise InputError("no_mesh", f"{label}: no mesh object in the file", "select the object(s) too when exporting")
    bare = [doc["nodes"][i].get("name", "?") for i in nodes if "skin" not in doc["nodes"][i]]
    if bare:
        raise InputError("no_skin_weights", f"{label}: {', '.join(bare)} not weighted to the armature",
                         "parent it to the kit's armature with vertex groups and export with Data > Skinning on")
    first = nodes[0]
    prims, keys, names = [], {}, []
    for i in nodes:
        n = doc["nodes"][i]
        names.append(n.get("name", "?"))
        if n["skin"] != doc["nodes"][first]["skin"]:
            raise InputError("armatures_differ", f"{label}: the objects use different armatures", "use only the kit's armature")
        for p in doc["meshes"][n["mesh"]]["primitives"]:
            prims.append(p)
            m = p.get("material")
            keys.setdefault(tex_key(doc, blob, doc["materials"][m]) if m is not None else None, []).append(
                doc["materials"][m].get("name", str(m)) if m is not None else "none")
    if len(keys) > 1:
        raise InputError("several_materials", f"{label}: {len(keys)} different materials ({', '.join(sorted({x for v in keys.values() for x in v}))})",
                         "one material per part: bake the textures into one image set")
    mat0 = prims[0].get("material")
    for p in prims:
        p["material"] = mat0
    doc["meshes"].append(dict(name=name, primitives=prims))
    for i in nodes:
        del doc["nodes"][i]["mesh"]
        if i != first:
            doc["nodes"][i].pop("skin", None)
    doc["nodes"][first]["mesh"] = len(doc["meshes"]) - 1
    doc["nodes"][first]["name"] = name
    return first, names


# ---------- images ----------
def png_info(data):
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return struct.unpack(">II", data[16:24])


def read_image(path, what):
    try:
        w, h, img, _ = texcodec.png_read(path)
    except Exception as e:   # texcodec raises plain errors on unsupported PNGs
        raise InputError("image_read", f"{what}: {os.path.basename(path)} cannot be read ({e})", "save it as an 8-bit PNG")
    return w, h, img


def set_image(doc, blob, mat, slot, data):
    """Embed PNG bytes and point the material's texture slot at them."""
    doc.setdefault("images", []).append(dict(bufferView=add_view(doc, blob, data), mimeType="image/png"))
    doc.setdefault("textures", []).append(dict(source=len(doc["images"]) - 1))
    ref = dict(index=len(doc["textures"]) - 1)
    if slot == "normalTexture":
        mat["normalTexture"] = ref
    else:
        mat.setdefault("pbrMetallicRoughness", {})[slot] = ref


def embedded_png(doc, blob, ref):
    if not ref:
        return None
    im = doc["images"][doc["textures"][ref["index"]]["source"]]
    return view_bytes(doc, blob, im["bufferView"]) if "bufferView" in im else None


def apply_images(doc, blob, node, images, label, work, notes):
    """Images chosen on the page replace the part's material's own; returns the texture size of the part."""
    from hosttex import resample
    prims = doc["meshes"][doc["nodes"][node]["mesh"]]["primitives"]
    m = prims[0].get("material")
    if m is None:
        doc.setdefault("materials", []).append(dict(name=label))
        m = len(doc["materials"]) - 1
        for p in prims:
            p["material"] = m
    mat = doc["materials"][m] = json.loads(json.dumps(doc["materials"][m]))   # this part's own copy
    pbr = mat.setdefault("pbrMetallicRoughness", {})
    for role, slot in (("colour", "baseColorTexture"), ("normal", "normalTexture")):
        if images.get(role):
            read_image(images[role], f"{label} {role}")
            set_image(doc, blob, mat, slot, open(images[role], "rb").read())
            if role == "colour":
                pbr["baseColorFactor"] = [1.0, 1.0, 1.0, 1.0]
    if images.get("roughness") or images.get("metallic"):
        old = embedded_png(doc, blob, pbr.get("metallicRoughnessTexture"))
        base = None
        if old:
            p = os.path.join(work, f"{label}_old_mr.png")
            open(p, "wb").write(old)
            base = read_image(p, f"{label} metallic-roughness")
        chans = {}
        for role in ("roughness", "metallic"):
            if images.get(role):
                chans[role] = read_image(images[role], f"{label} {role}")
        W = max([c[0] for c in chans.values()] + ([base[0]] if base else []))
        H = max([c[1] for c in chans.values()] + ([base[1]] if base else []))
        out = bytearray(b"\xff" * (4 * W * H))
        rf, mf = pbr.get("roughnessFactor", 1.0), pbr.get("metallicFactor", 1.0)
        b = resample(base[2], base[0], base[1], W, H) if base else None
        for role, ch, factor, default in (("roughness", 1, rf, 200), ("metallic", 2, mf, 0)):
            if role in chans:
                w, h, img = chans[role]
                out[ch::4] = resample(img, w, h, W, H)[0::4]
            elif b:
                out[ch::4] = bytes(min(255, round(v * factor)) for v in b[ch::4])
            else:
                out[ch::4] = bytes([default]) * (W * H)
        p = os.path.join(work, f"{label}_metal_rough.png")
        texcodec.png_write(p, W, H, out)
        set_image(doc, blob, mat, "metallicRoughnessTexture", open(p, "rb").read())
        pbr["roughnessFactor"] = pbr["metallicFactor"] = 1.0
        note(notes, "image_combined", f"{label}: roughness and metallic combined into one image ({W} x {H})")
    data = embedded_png(doc, blob, pbr.get("baseColorTexture"))
    size = png_info(data) if data else None
    if not size:
        return None
    w, h = size
    if max(w, h) > MAX_TEX:
        raise InputError("image_too_large", f"{label} color image is {w} x {h}; the largest size is {MAX_TEX} x {MAX_TEX}",
                         f"save it at {MAX_TEX} x {MAX_TEX} (or 2048, 1024)")
    if w != h:
        note(notes, "image_not_square", f"{label} color image is {w} x {h}: it is stretched to a square texture",
             "use a square image (1024, 2048 or 4096)")
    nd = embedded_png(doc, blob, mat.get("normalTexture"))
    if nd and png_info(nd) and max(png_info(nd)) > MAX_TEX:
        raise InputError("image_too_large", f"{label} normal image is larger than {MAX_TEX} x {MAX_TEX}",
                         f"save it at {MAX_TEX} x {MAX_TEX} or smaller")
    return max(w, h)


# ---------- the model: Cloth + Skin in one file ----------
def joints_by_name(doc, blob, node):
    """{joint name: (index in the skin, inverse bind matrix, [translation, rotation, scale])}."""
    sk = doc["skins"][doc["nodes"][node]["skin"]]
    a = doc["accessors"][sk["inverseBindMatrices"]]
    data = view_bytes(doc, blob, a["bufferView"])[a.get("byteOffset", 0):]
    out = {}
    for k, j in enumerate(sk["joints"]):
        n = doc["nodes"][j]
        out[n.get("name", f"#{j}")] = (k, struct.unpack_from("<16f", data, 64 * k),
                                       [n.get(t) for t in ("translation", "rotation", "scale")])
    return out


def close(a, b, tol=1e-4):
    if a is None or b is None:
        return a == b
    return len(a) == len(b) and all(abs(x - y) <= tol for x, y in zip(a, b))


def model(cloth, skin, out, work, images=None):
    """body.glb in `out` with "Cloth" and "Skin"; returns (dict(cloth=size, skin=size), notes). Raises InputError."""
    images = images or {}
    notes = []
    os.makedirs(work, exist_ok=True)
    doc, blob = load(skin)
    cd, cb = load(cloth)
    sn, snames = join(doc, blob, "Skin", "Skin")
    cn, cnames = join(cd, cb, "Cloth", "Cloth")
    js, jc = joints_by_name(doc, blob, sn), joints_by_name(cd, cb, cn)
    remap = {}
    for name, (k, ibm, trs) in jc.items():
        if name not in js:
            raise InputError("joint_missing", f"the Cloth uses the bone '{name}', which the Skin file's armature does not have",
                             "export both parts with the same kit armature")
        k2, ibm2, trs2 = js[name]
        if not close(ibm, ibm2) or not all(close(x, y) for x, y in zip(trs, trs2)):
            raise InputError("armatures_differ", f"the Cloth and Skin files have different armatures (bone '{name}' differs)",
                             "export both parts from the same scene with the same kit armature, unchanged")
        remap[k] = k2
    sizes = dict(skin=apply_images(doc, blob, sn, images.get("skin", {}), "Skin", work, notes),
                 cloth=apply_images(cd, cb, cn, images.get("cloth", {}), "Cloth", work, notes))
    # copy the Cloth's mesh, material, textures and images into the Skin's file
    vmap, tmap = {}, {}

    def copy_view(bvi):
        if bvi not in vmap:
            vmap[bvi] = add_view(doc, blob, view_bytes(cd, cb, bvi))
            for k in ("byteStride", "target"):
                if k in cd["bufferViews"][bvi]:
                    doc["bufferViews"][vmap[bvi]][k] = cd["bufferViews"][bvi][k]
        return vmap[bvi]

    def copy_tex(ti):
        if ti not in tmap:
            t = dict(cd["textures"][ti])
            im = dict(cd["images"][t["source"]])
            im["bufferView"] = copy_view(im["bufferView"])
            doc.setdefault("images", []).append(im)
            t["source"] = len(doc["images"]) - 1
            if "sampler" in t:
                doc.setdefault("samplers", []).append(cd["samplers"][t["sampler"]])
                t["sampler"] = len(doc["samplers"]) - 1
            doc.setdefault("textures", []).append(t)
            tmap[ti] = len(doc["textures"]) - 1
        return tmap[ti]

    identity = all(k == v for k, v in remap.items())
    amap = {}
    mesh = json.loads(json.dumps(cd["meshes"][cd["nodes"][cn]["mesh"]]))
    matmap = {}
    for pr in mesh["primitives"]:
        for key, ai in list(pr["attributes"].items()) + [("indices", pr.get("indices"))]:
            if ai is None:
                continue
            if (ai, key) not in amap:
                a = dict(cd["accessors"][ai])
                if key.startswith("JOINTS_") and not identity:
                    a = joints_copy(cd, cb, a, remap, doc, blob)
                else:
                    a["bufferView"] = copy_view(a["bufferView"])
                doc["accessors"].append(a)
                amap[(ai, key)] = len(doc["accessors"]) - 1
            if key == "indices":
                pr["indices"] = amap[(ai, key)]
            else:
                pr["attributes"][key] = amap[(ai, key)]
        mi = pr.get("material")
        if mi is not None:
            if mi not in matmap:
                mat = json.loads(json.dumps(cd["materials"][mi]))
                for holder in (mat, mat.get("pbrMetallicRoughness", {})):
                    for k, v in holder.items():
                        if isinstance(v, dict) and "index" in v and k.endswith("Texture"):
                            v["index"] = copy_tex(v["index"])
                doc["materials"].append(mat)
                matmap[mi] = len(doc["materials"]) - 1
            pr["material"] = matmap[mi]
    doc["meshes"].append(mesh)
    doc["nodes"].append(dict(name="Cloth", mesh=len(doc["meshes"]) - 1, skin=doc["nodes"][sn]["skin"]))
    new = len(doc["nodes"]) - 1
    parent = next((n for n in doc["nodes"] if sn in n.get("children", [])), None)
    if parent is not None:
        parent["children"].append(new)
    else:
        doc["scenes"][doc.get("scene", 0)]["nodes"].append(new)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    save(out, doc, blob)
    if len(snames) > 1 or len(cnames) > 1:
        note(notes, "objects_joined", "objects joined into one part: " + "; ".join(
            f"{lab}: {', '.join(n)}" for lab, n in (("Skin", snames), ("Cloth", cnames)) if len(n) > 1))
    return sizes, notes


def joints_copy(src, sblob, acc, remap, doc, blob):
    """A JOINTS accessor with every joint index mapped (written as tightly packed u16 VEC4)."""
    v = src["bufferViews"][acc["bufferView"]]
    size = {5121: 1, 5123: 2}[acc["componentType"]]
    stride = v.get("byteStride", 4 * size)
    base = v.get("byteOffset", 0) + acc.get("byteOffset", 0)
    out = bytearray()
    for i in range(acc["count"]):
        for c in range(4):
            o = base + i * stride + c * size
            out += struct.pack("<H", remap.get(int.from_bytes(sblob[o:o + size], "little"), 0))
    return dict(bufferView=add_view(doc, blob, bytes(out)), componentType=5123, count=acc["count"], type="VEC4")


# ---------- hair ----------
def hair(src, out_dir, colour=None, alpha=None, rgba=None):
    """hair.glb (one object "hair"), hair_colour.png and hair_alpha.png (2048) in out_dir; returns (paths, notes)."""
    from hosttex import resample
    notes = []
    os.makedirs(out_dir, exist_ok=True)
    doc, blob = load(src)
    node, names = join(doc, blob, "Hair", "hair")
    if len(names) > 1:
        note(notes, "objects_joined", f"hair objects joined into one: {', '.join(names)}")
    glb = os.path.join(out_dir, "hair.glb")
    save(glb, doc, blob)
    if colour:
        w, h, img = read_image(colour, "hair color")
        if alpha:
            wa, ha, al = read_image(alpha, "hair alpha")
            if (wa, ha) != (w, h):
                raise InputError("image_size_mismatch", f"hair color {w} x {h} and alpha {wa} x {ha} differ in size",
                                 "save both at the same size (2048 x 2048)")
            img = bytearray(img)
            img[3::4] = al[0::4]
    elif rgba:
        w, h, img = read_image(rgba, "hair image")
    else:
        prims = doc["meshes"][doc["nodes"][node]["mesh"]]["primitives"]
        mat = doc["materials"][prims[0]["material"]] if prims[0].get("material") is not None else {}
        data = embedded_png(doc, blob, mat.get("pbrMetallicRoughness", {}).get("baseColorTexture"))
        if not data:
            raise InputError("image_read", "no hair color image: none chosen and none in the hair file",
                             "choose the color and alpha images (or one RGBA image)")
        p = os.path.join(out_dir, "hair_from_glb.png")
        open(p, "wb").write(data)
        w, h, img = read_image(p, "hair image (from your hair file)")
    if max(w, h) > MAX_TEX:
        raise InputError("image_too_large", f"hair image is {w} x {h}; the largest size is {MAX_TEX} x {MAX_TEX}",
                         "save it at 2048 x 2048")
    if (w, h) != (HAIR_SIZE, HAIR_SIZE):
        img = resample(img, w, h, HAIR_SIZE, HAIR_SIZE)
        note(notes, "image_resized", f"hair image {w} x {h} resized to {HAIR_SIZE} x {HAIR_SIZE} (the game's hair textures)",
             "paint the hair image at 2048 x 2048 for full control")
    n = HAIR_SIZE * HAIR_SIZE
    col = os.path.join(out_dir, "hair_colour.png")
    texcodec.png_write(col + ".tmp.png", HAIR_SIZE, HAIR_SIZE, img, alpha=True)
    fsx.replace(col + ".tmp.png", col)
    grey = bytearray(4 * n)
    grey[0::4] = grey[1::4] = grey[2::4] = img[3::4]
    grey[3::4] = b"\xff" * n
    alp = os.path.join(out_dir, "hair_alpha.png")
    texcodec.png_write(alp + ".tmp.png", HAIR_SIZE, HAIR_SIZE, grey)
    fsx.replace(alp + ".tmp.png", alp)
    return dict(glb=glb, colour=col, alpha=alp), notes


def iris(path):
    w, h, _ = read_image(path, "eye color")
    if (w, h) != (IRIS_SIZE, IRIS_SIZE):
        raise InputError("iris_size", f"the eye color image is {w} x {h}; it must be {IRIS_SIZE} x {IRIS_SIZE}",
                         "paint on the head kit's iris image and keep its size")
