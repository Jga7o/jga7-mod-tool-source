"""The host material's three textures (M14): your images -> the game's layouts -> written into the DPC.

Host M38 (notes\\host_material.md): colour B12 (2048² BC1), normal B13 (2048² BC3), mask B14 (256² BC1, LZ4).
Game layouts (decoded in M14 step 1):
  colour  RGB as stored (the material multiplies it by 0.9406; --compensate pre-divides in linear light)
  normal  A = normal X (+u, image right), G = normal Y (+v, image DOWN: DirectX), z rebuilt from x, y;
          R = metalness (0 non-metal .. 255 metal), B = roughness (255 matte .. low shiny)       [R, B: likely]
  mask    R = per-material overlay weight (meaning unknown; host background 148), G = B = 255
Your images (8-bit PNG, any size; each is resampled to the slot's size: area average when larger, bilinear when smaller,
so a smaller image only looks softer and a non-square image is stretched to the square texture, which is exact in UV
terms because UVs run 0..1 either way. The textures cannot be made larger than the slot.):
  --colour  RGB (alpha ignored)
  --normal  a standard tangent-space normal map as Blender bakes it (OpenGL: R = +X right, G = +Y UP, B = Z)
  --metal   grey (white = metal)        default 0
  --rough   grey (white = rough)        default 200 (cloth/leather; the host leather averages 193-231)
  --mask    grey (the mask's R value)   default 148 (the host material's background)
  --gltf    a .glb/.gltf from Blender: baseColorTexture (x baseColorFactor), normalTexture, metallicRoughnessTexture
            (G = roughness, B = metalness, x the factors) of the object's material (--object) or the first material.
            Embedded PNG only. occlusionTexture is ignored (no AO channel in the host material). Separate options
            override the glTF's images.
Missing inputs: the defaults above (flat normal = A 128, G 128). The textures are written with texpatch.py (same size,
mips regenerated with a 2x2 box filter, BC1/BC3 encoded, LZ4 slot recompressed), one after another.

Usage:
  python -I hosttex.py export <in.DPC> --out <dir>         (your-format PNGs of the host's current textures)
  python -I hosttex.py apply <in.DPC> <out.DPC> [--colour c.png] [--normal n.png] [--metal m.png] [--rough r.png]
         [--mask k.png] [--gltf model.glb [--object name]] [--compensate] [--work dir] [--report r.json]
  python -I hosttex.py layouts [same inputs] --out <dir>    (only write the game-layout PNGs, nothing else)
Other hosts (M14b): --host <slot> (e.g. M32, M42; default M38) takes the slot's own textures, sizes, mask channel and
neutral value from its Material_Z (hosts.py). --keep M12,... : kept pieces that share the host's textures get their
original texels pasted back under their UVs (reserve()).
"""
import argparse
import base64
import json
import math
import os
import fsx
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paths  # noqa: E402
import texcodec as tc  # noqa: E402
import texlib  # noqa: E402

OUT = paths.OUT
RES = os.path.join(OUT, "P_AMICIA_unpacked", "resources")
HOST = dict(colour=6925564721753714648, normal=-4430282810869589791, mask=-6073175909851113788)   # B12, B13, B14
SIZE = dict(colour=2048, normal=2048, mask=256)
MULT = 0.9406  # host Material_Z colour multiplier (body +0..+8)
ROUGH_DEFAULT, MASK_DEFAULT = 200, 148
LIN = [((i / 255) / 12.92 if i / 255 <= 0.04045 else ((i / 255 + 0.055) / 1.055) ** 2.4) for i in range(256)]


def to_srgb(x):
    x = max(0.0, min(1.0, x))
    return round(255 * (12.92 * x if x <= 0.0031308 else 1.055 * x ** (1 / 2.4) - 0.055))


def resample(img, w, h, W, H):
    """RGBA bytearray w x h -> W x H: area average when shrinking, bilinear (edge clamp) when enlarging."""
    if (w, h) == (W, H):
        return bytearray(img)
    out = bytearray(W * H * 4)
    if w >= W and h >= H:
        fx, fy = w / W, h / H
        for y in range(H):
            y0, y1 = int(y * fy), max(int(y * fy) + 1, int((y + 1) * fy))
            for x in range(W):
                x0, x1 = int(x * fx), max(int(x * fx) + 1, int((x + 1) * fx))
                n = (y1 - y0) * (x1 - x0)
                o = 4 * (y * W + x)
                for c in range(4):
                    out[o + c] = round(sum(img[4 * (yy * w + xx) + c] for yy in range(y0, y1) for xx in range(x0, x1)) / n)
        return out
    for y in range(H):
        for x in range(W):
            p = texlib.bilinear(img, w, h, (x + 0.5) / W, (y + 0.5) / H, wrap=False)
            o = 4 * (y * W + x)
            out[o:o + 4] = bytes(max(0, min(255, round(v))) for v in p)
    return out


def read_png(path):
    w, h, img, _ = tc.png_read(path)
    return w, h, img


def grey(img):
    """First channel of an RGBA image."""
    return img[0::4]


def constant(n, rgba):
    return bytearray(bytes(rgba) * n)


def default_spec():
    """The M38 host (B12 / B13 / B14), as used by the atlas edit."""
    return dict(label="M38", colour=dict(id=HOST["colour"], w=SIZE["colour"], h=SIZE["colour"]),
                normal=dict(id=HOST["normal"], w=SIZE["normal"], h=SIZE["normal"]),
                mask=dict(id=HOST["mask"], w=SIZE["mask"], h=SIZE["mask"], style="R", neutral=MASK_DEFAULT), mult=MULT)


def host_spec(label, arch="P_AMICIA"):
    """A host slot's textures from its Material_Z (hosts.py): ids, sizes, mask channel (R host style / G belt style) and
    its neutral value (the most common value of the host's own mask), colour multiplier. arch: M16, any Amicia archive."""
    import hosts
    res = hosts.res_dir(arch)
    bm, ms = hosts.labels(arch)
    b = open(os.path.join(res, f"{ms[label]}.Mesh_Z"), "rb").read()
    mi = hosts.material_info(hosts.material_of(b, res), res)
    out = dict(label=label, mult=mi["mult"], arch=arch)
    for role in ("colour", "normal", "mask"):
        t = mi["textures"].get(role)
        if t is None:
            out[role] = None
            continue
        i = texlib.info(os.path.join(res, f"{t}.Bitmap_Z"))
        out[role] = dict(id=t, w=i["w"], h=i["h"], fmt=i["fmt"])
    if out["colour"] is None or out["normal"] is None:
        raise ValueError(f"{label}: the material has no colour or no normal texture")
    if out["mask"]:
        style = "R" if mi["flags"] & 0x00800000 else "G"
        w, h, _, img = texlib.load(res, out["mask"]["id"])
        ch = 0 if style == "R" else 1
        vals = img[ch::4]
        out["mask"].update(style=style, neutral=max(range(256), key=vals.count))
    return out


def build_layouts(colour=None, normal=None, metal=None, rough=None, mask=None, compensate=False, spec=None):
    """Inputs: (w, h, rgba) or None. Returns {'colour', 'normal', 'mask'} images in the host's game layouts and sizes
    (spec: host_spec(); default the M38 host), and notes. A host without a mask texture gets no 'mask' entry."""
    spec = spec or default_spec()
    notes = []
    cw, chh = spec["colour"]["w"], spec["colour"]["h"]
    if colour:
        c = resample(colour[2], colour[0], colour[1], cw, chh)
        if (colour[0], colour[1]) != (cw, chh):
            notes.append(f"colour {colour[0]}x{colour[1]} resampled to {cw}x{chh}")
    else:
        c = constant(cw * chh, (128, 128, 128, 255))
        notes.append("no colour: grey")
    for i in range(cw * chh):
        c[4 * i + 3] = 255
    if compensate and abs(spec["mult"] - 1) > 1e-6 and spec["mult"] > 0:
        lut = [to_srgb(LIN[v] / spec["mult"]) for v in range(256)]
        for i in range(cw * chh):
            for k in range(3):
                c[4 * i + k] = lut[c[4 * i + k]]
        notes.append(f"colour divided by the material multiplier {spec['mult']} (linear light)")
    S, T = spec["normal"]["w"], spec["normal"]["h"]
    n = bytearray(S * T * 4)
    nm = resample(normal[2], normal[0], normal[1], S, T) if normal else None
    mt = grey(resample(metal[2], metal[0], metal[1], S, T)) if metal else None
    rg = grey(resample(rough[2], rough[0], rough[1], S, T)) if rough else None
    for i in range(S * T):
        if nm:
            x = nm[4 * i] / 255 * 2 - 1
            y = -(nm[4 * i + 1] / 255 * 2 - 1)        # OpenGL +Y up -> game +Y down
            l = math.hypot(x, y)
            if l > 1:
                x, y = x / l, y / l
            n[4 * i + 3] = max(0, min(255, round(128 + 127 * x)))
            n[4 * i + 1] = max(0, min(255, round(128 + 127 * y)))
        else:
            n[4 * i + 3] = n[4 * i + 1] = 128
        n[4 * i] = mt[i] if mt else 0
        n[4 * i + 2] = rg[i] if rg else ROUGH_DEFAULT
    if not normal:
        notes.append("no normal map: flat")
    out = dict(colour=c, normal=n)
    ms = spec.get("mask")
    if ms:
        M, N = ms["w"], ms["h"]
        mk = resample(mask[2], mask[0], mask[1], M, N) if mask else None
        k = bytearray(M * N * 4)
        for i in range(M * N):
            v = mk[4 * i] if mk else ms["neutral"]
            k[4 * i:4 * i + 4] = bytes((v, 255, 255, 255)) if ms["style"] == "R" else bytes((0, v, 0, 255))
        if not mask:
            notes.append(f"no mask: neutral {ms['neutral']} in {ms['style']}")
        out["mask"] = k
    elif mask:
        notes.append("the host has no mask texture: the mask image is ignored")
    return out, notes


def reserve(layouts, spec, keep, res=RES, arch="P_AMICIA"):
    """Kept pieces that share the host's textures: paste their original texels (under their UV triangles, plus a
    4-texel border on colour / normal, 1 on the mask) back into the new images, so they keep looking as before.
    Returns notes with the reserved share of each texture."""
    import hosts
    import uvraster
    bm, ms = hosts.labels(arch)
    notes = []
    for lab in keep:
        b = open(os.path.join(res, f"{ms[lab]}.Mesh_Z"), "rb").read()
        mi = hosts.material_info(hosts.material_of(b, res), res)
        uv, tris = uvraster.mesh_uv(b)
        for role, img in layouts.items():
            t = spec[role]["id"]
            if mi["textures"].get(role) != t:
                continue
            w, h = spec[role]["w"], spec[role]["h"]
            cov = uvraster.coverage(uv, tris, w, h, dilate=4 if role != "mask" else 1)
            ow, oh, _, orig = texlib.load(res, t)
            n = 0
            for i in range(w * h):
                if cov[i]:
                    img[4 * i:4 * i + 4] = orig[4 * i:4 * i + 4]
                    n += 1
            notes.append(f"{role}: {100 * n / (w * h):.1f} % reserved for the kept piece {lab} (its original texels)")
    return notes


def overlap_reserved(spec, keep, uv, tris, res=RES, arch="P_AMICIA"):
    """Share (%) of a part's UV coverage that falls on texels reserved for kept pieces (colour texture size)."""
    import hosts
    import uvraster
    bm, ms = hosts.labels(arch)
    w, h = spec["colour"]["w"], spec["colour"]["h"]
    part = uvraster.coverage(uv, tris, w, h)
    hit = 0
    for lab in keep:
        b = open(os.path.join(res, f"{ms[lab]}.Mesh_Z"), "rb").read()
        mi = hosts.material_info(hosts.material_of(b, res), res)
        if mi["textures"].get("colour") != spec["colour"]["id"]:
            continue
        kuv, ktris = uvraster.mesh_uv(b)
        cov = uvraster.coverage(kuv, ktris, w, h, dilate=4)
        hit += sum(1 for i in range(w * h) if cov[i] and part[i])
    return 100 * hit / max(1, sum(part))


def gltf_images(path, obj=None):
    """{'colour', 'normal', 'metal', 'rough'} as (w, h, rgba) from a glTF material, and notes."""
    import gltfread
    import zlib  # noqa: F401  (png decoding lives in texcodec)
    g = gltfread.load(path)
    doc = g.doc
    mats = doc.get("materials", [])
    if not mats:
        raise ValueError("the glTF has no material")
    mi = 0
    if obj is not None:
        node = doc["nodes"][g.find_node(obj)]
        prims = doc["meshes"][node["mesh"]]["primitives"]
        mi = prims[0].get("material", 0)
        if len({p.get("material", 0) for p in prims}) > 1:
            raise ValueError(f"object {obj} has several materials: bake them into one material (one atlas) first")
    m = mats[mi]
    notes = [f"material '{m.get('name', mi)}'"]

    def image(tex_ref):
        if not tex_ref:
            return None
        if tex_ref.get("texCoord", 0) != 0:
            raise ValueError("a texture uses a second UV map (texCoord 1); use the first UV map")
        src = doc["textures"][tex_ref["index"]]["source"]
        im = doc["images"][src]
        if "bufferView" in im:
            bv = doc["bufferViews"][im["bufferView"]]
            data = g.bins[bv.get("buffer", 0)][bv.get("byteOffset", 0):bv.get("byteOffset", 0) + bv["byteLength"]]
        elif im.get("uri", "").startswith("data:"):
            data = base64.b64decode(im["uri"].split(",", 1)[1])
        else:
            data = open(os.path.join(os.path.dirname(os.path.abspath(path)), im["uri"]), "rb").read()
        if data[:8] != b"\x89PNG\r\n\x1a\n":
            raise ValueError(f"image '{im.get('name', src)}' is not a PNG (JPEG is not supported: set the images to PNG "
                             "in Blender before exporting)")
        tmp = os.path.join(OUT, "import_work", "m14", f"gltf_img_{src}.png")
        os.makedirs(os.path.dirname(tmp), exist_ok=True)
        open(tmp, "wb").write(data)
        return read_png(tmp)
    pbr = m.get("pbrMetallicRoughness", {})
    out = {}
    col = image(pbr.get("baseColorTexture"))
    f = pbr.get("baseColorFactor", [1, 1, 1, 1])
    if col and f[:3] != [1, 1, 1]:
        w, h, img = col
        for i in range(w * h):
            for k in range(3):
                img[4 * i + k] = to_srgb(LIN[img[4 * i + k]] * f[k])
        notes.append(f"baseColorFactor {f[:3]} applied")
    if not col:
        notes.append(f"no baseColorTexture: constant colour {f[:3]}")
        col = (1, 1, bytearray(bytes([to_srgb(f[0]), to_srgb(f[1]), to_srgb(f[2]), 255])))
    out["colour"] = col
    nt = image(m.get("normalTexture"))
    if nt:
        out["normal"] = nt
        if m["normalTexture"].get("scale", 1) != 1:
            notes.append("normalTexture scale ignored")
    mr = image(pbr.get("metallicRoughnessTexture"))
    mf, rf = pbr.get("metallicFactor", 1.0), pbr.get("roughnessFactor", 1.0)
    if mr:
        w, h, img = mr
        met, rou = bytearray(w * h * 4), bytearray(w * h * 4)
        for i in range(w * h):
            met[4 * i] = round(img[4 * i + 2] * mf)       # glTF B = metalness
            rou[4 * i] = round(img[4 * i + 1] * rf)       # glTF G = roughness
        out["metal"], out["rough"] = (w, h, met), (w, h, rou)
    else:
        out["metal"] = (1, 1, bytearray([round(255 * mf), 0, 0, 255]))
        out["rough"] = (1, 1, bytearray([round(255 * rf), 0, 0, 255]))
        notes.append(f"no metallicRoughnessTexture: metallic {mf}, roughness {rf}")
    if m.get("occlusionTexture"):
        notes.append("occlusionTexture ignored (the host material has no AO channel)")
    if m.get("alphaMode", "OPAQUE") != "OPAQUE":
        notes.append(f"alphaMode {m['alphaMode']} ignored: the host material is opaque")
    return out, notes


def export(dpc_path, out_dir, work):
    """Your-format PNGs of the textures in a DPC's host slots (round-trip base)."""
    os.makedirs(out_dir, exist_ok=True)
    ims = {}
    for k, bid in HOST.items():
        p = os.path.join(work, f"export_{k}.png")
        r = subprocess.run([sys.executable, "-I", os.path.join(HERE, "texpatch.py"), "export", dpc_path, f"--id={bid}",
                            "--png", p, "--work", work], capture_output=True, text=True)
        if r.returncode:
            raise ValueError(r.stdout + r.stderr)
        w, h, img = read_png(p)
        ims[k] = (w, h, img)
    w, h, c = ims["colour"]
    tc.png_write(os.path.join(out_dir, "colour.png"), w, h, c)
    w, h, n = ims["normal"]
    nrm, met, rou = bytearray(w * h * 4), bytearray(w * h * 4), bytearray(w * h * 4)
    for i in range(w * h):
        x, y = max(-1.0, (n[4 * i + 3] - 128) / 127), max(-1.0, (n[4 * i + 1] - 128) / 127)
        z = math.sqrt(max(0.0, 1 - x * x - y * y))
        nrm[4 * i:4 * i + 4] = bytes((round((x * 0.5 + 0.5) * 255), round((-y * 0.5 + 0.5) * 255), round((z * 0.5 + 0.5) * 255), 255))
        met[4 * i:4 * i + 4] = bytes((n[4 * i],) * 3 + (255,))
        rou[4 * i:4 * i + 4] = bytes((n[4 * i + 2],) * 3 + (255,))
    tc.png_write(os.path.join(out_dir, "normal.png"), w, h, nrm)
    tc.png_write(os.path.join(out_dir, "metal.png"), w, h, met)
    tc.png_write(os.path.join(out_dir, "rough.png"), w, h, rou)
    w, h, k = ims["mask"]
    tc.png_write(os.path.join(out_dir, "mask.png"), w, h, bytearray(b for i in range(w * h) for b in (k[4 * i],) * 3 + (255,)))
    return sorted(os.listdir(out_dir))


def gather(a):
    ims, notes = {}, []
    if a.gltf:
        ims, notes = gltf_images(a.gltf, a.object)
    for k in ("colour", "normal", "metal", "rough", "mask"):
        p = getattr(a, k)
        if p:
            ims[k] = read_png(p)
    return ims, notes


def write_layouts(lay, out_dir, spec=None):
    spec = spec or default_spec()
    os.makedirs(out_dir, exist_ok=True)
    paths = {}
    for k, img in lay.items():
        p = os.path.join(out_dir, f"game_{k}.png")
        tc.png_write(p, spec[k]["w"], spec[k]["h"], img, alpha=k == "normal")
        paths[k] = p
    return paths


def apply_layouts(src, dst, paths, work, report=None, spec=None, opaque_colour=False):
    """texpatch.py apply for each host texture in turn (each step is checked by texpatch; the last writes dst)."""
    ids = {k: v["id"] for k, v in (spec or default_spec()).items() if k in ("colour", "normal", "mask") and v}
    cur, steps = src, []
    order = [k for k in ("colour", "normal", "mask") if k in paths]
    for n, k in enumerate(order):
        nxt = dst if n == len(order) - 1 else os.path.join(work, f"hosttex_{k}.tmp.DPC")
        rp = os.path.join(work, f"texpatch_{k}.json")
        r = subprocess.run([sys.executable, "-I", os.path.join(HERE, "texpatch.py"), "apply", cur, nxt, f"--id={ids[k]}",
                            "--png", paths[k], "--work", work, "--report", rp, "--allow-grow"] + (["--opaque"] if opaque_colour and k == "colour" else []),
                           capture_output=True, text=True)
        if r.returncode:
            raise ValueError(f"texpatch {k}: {(r.stdout + r.stderr).strip()[-600:]}")
        steps.append(dict(texture=k, id=ids[k], log=r.stdout.strip().splitlines()[-3:], report=json.load(open(rp))))
        if cur != src:
            fsx.remove(cur)
        cur = nxt
    if report:
        json.dump(steps, open(report, "w"), indent=1)
    return steps


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["export", "apply", "layouts"])
    ap.add_argument("dpc", nargs="?")
    ap.add_argument("dst", nargs="?")
    for k in ("colour", "normal", "metal", "rough", "mask", "gltf", "object", "out", "report", "host", "keep"):
        ap.add_argument(f"--{k}")
    ap.add_argument("--compensate", action="store_true")
    ap.add_argument("--work", default=os.path.join(OUT, "import_work", "hosttex"))
    a = ap.parse_args()
    os.makedirs(a.work, exist_ok=True)
    try:
        if a.mode == "export":
            print("wrote", export(a.dpc, a.out, a.work), "to", a.out)
            return
        ims, notes = gather(a)
        spec = host_spec(a.host) if a.host else default_spec()
        lay, n2 = build_layouts(ims.get("colour"), ims.get("normal"), ims.get("metal"), ims.get("rough"), ims.get("mask"),
                                a.compensate, spec)
        if a.keep:
            n2 += reserve(lay, spec, [x for x in a.keep.split(",") if x])
        for x in notes + n2:
            print("NOTE:", x)
        paths = write_layouts(lay, a.out or a.work, spec)
        if a.mode == "layouts":
            print("wrote", paths)
            return
        for s in apply_layouts(a.dpc, a.dst, paths, a.work, a.report, spec):
            print(f"{s['texture']}: " + " | ".join(s["log"]))
        print("wrote", a.dst)
    except ValueError as e:
        sys.exit(f"ERROR: {e}")


if __name__ == "__main__":
    main()
