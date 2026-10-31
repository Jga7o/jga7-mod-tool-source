"""Host slots for new meshes (M14b): every importable P_AMICIA slot with its material class, textures, storage, budget,
CUSTOMIZATION overrides and the pieces that share its textures. Used by modtool.py list-hosts and the "full_body" edit.

Importable = one skinned vertex block (stride 48), one index buffer, one submesh (the rule of blendimport.py).
Material class from the Material_Z words (notes\\host_material.md):
  alpha cutout  shader flags (+292) with low bits set (hair, lashes, ruffles, eyes)          likely
  skin-like     +300 == 15 (hands, hand wraps, face, mouth: the skin shading model)            likely
  hair / eye    +300 == 64 / 63                                                                likely
  opaque        everything else; "host-style mask" when flag 0x00800000 is set (mask in R)     likely
Texture roles by Material_Z slot: +316 colour, +452 normal, +524 mask, +380 / +388 / +468 / +508 other (certain:
the presence bits at +308 match).
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paths  # noqa: E402
import texlib  # noqa: E402
from meshfmt import parse  # noqa: E402

OUT = paths.OUT
ROOT = paths.REPO
RES = os.path.join(OUT, "P_AMICIA_unpacked", "resources")
CUST = os.path.join(OUT, "CUSTOMIZATION_unpacked", "resources")
ROLES = {316: "colour", 452: "normal", 524: "mask", 380: "environment", 388: "extra", 468: "cube", 508: "extra2"}
# what each P_AMICIA piece is (notes\part_map.md); "outfit" pieces are hidden by default by the full_body edit
PIECE = {"M04": "belt (hip)", "M05": "belt studs (never drawn)", "M10": "belt (hip)", "M11": "belt (hip)", "M12": "hood buttons (2)",
         "M13": "ruffles", "M17": "belt (hip)", "M18": "pants", "M21": "belt", "M23": "belt", "M25": "corset", "M29": "right-hand wrap",
         "M30": "inner clothes, sleeves (linen)", "M31": "belt (hip)", "M32": "shoulder hood", "M33": "shirt", "M35": "belt (hip)",
         "M38": "boots", "M39": "belt (hip)", "M42": "right-hand wrap", "M43": "belt", "M44": "belt (hip)", "M36": "eyelashes",
         "M02": "hair", "M07": "hair", "M08": "hair", "M14": "hair", "M34": "hair", "M03": "unknown (never seen)"}
PROVEN = {"M38": "enlarged with a merged mesh and its own atlas in game (M13, M14)"}
OUTFIT = ["M18", "M25", "M33", "M32", "M12", "M13", "M30", "M04", "M10", "M11", "M17", "M21", "M23", "M31", "M35", "M39",
          "M43", "M44", "M38"]


def res_dir(arch="P_AMICIA"):
    return os.path.join(OUT, f"{arch}_unpacked", "resources")


def labels(arch="P_AMICIA"):
    """({bitmap id: label}, {mesh label: mesh id}). Bitmap labels: P_AMICIA's own table (B..); variants: the previews
    label tables (a shared id keeps its first label)."""
    if arch == "P_AMICIA":
        bm = {int(l.split("\t")[1]): l.split("\t")[0] for l in open(os.path.join(paths.TABLES, "previews", "P_AMICIA_bitmaps.tsv")) if not l.startswith("label")}
    else:
        bm = {int(l.split("\t")[0]): l.split("\t")[1].strip() for l in open(os.path.join(paths.TABLES, "previews_variants", f"{arch}_labels.tsv")) if l.strip()}
    ms = {r.split("\t")[0]: int(r.split("\t")[1]) for r in open(os.path.join(paths.TABLES, "analysis", f"{arch}_parts.tsv")).read().split("\n")[1:] if r}
    return bm, ms


def material_of(b, res=RES):
    lh = struct.unpack_from("<I", b, 0x2C)[0]
    for k in range(64, 64 + lh - 7):
        v = struct.unpack_from("<q", b, k)[0]
        if os.path.isfile(os.path.join(res, f"{v}.Material_Z")):
            return v
    return None


def material_info(mid, res=RES):
    b = open(os.path.join(res, f"{mid}.Material_Z"), "rb").read()
    lh = struct.unpack_from("<I", b, 0x2C)[0]
    body = b[64 + lh:]
    flags = struct.unpack_from("<I", body, 292)[0]
    model = struct.unpack_from("<I", body, 300)[0]
    mult = struct.unpack_from("<f", body, 0)[0]
    tex = {}
    for off, role in ROLES.items():
        v = struct.unpack_from("<q", body, off)[0]
        if os.path.isfile(os.path.join(res, f"{v}.Bitmap_Z")):
            tex[role] = v
    if flags & 0xFFFF:
        cls = "alpha cutout"
    elif model == 15:
        cls = "skin-like"
    elif model == 64:
        cls = "hair"
    elif model == 63:
        cls = "eye"
    else:
        cls = "opaque"
    return dict(id=mid, flags=flags, model=model, mult=round(mult, 4), textures=tex, cls=cls,
                mask_style="R (host style)" if flags & 0x00800000 else ("G (belt style)" if "mask" in tex else "none"))


def storage(dpc_path=None, arch="P_AMICIA"):
    """{mesh id: (raw|lz4, stored bytes)} from the original archive."""
    import mmap
    import dpc
    p = dpc_path or os.path.join(paths.ORIG, f"{arch}.DPC")
    out = {}
    with open(p, "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as buf:
        for e in dpc.parse(buf)["blocks"][0]["resources"]["resources"]:
            d = e["data"]
            out[dpc.s64(e["name"])] = ("lz4" if d["compression_type"] else "raw", d["end"] - d["pos"])
    return out


def table(arch="P_AMICIA"):
    """Every mesh of an archive (default P_AMICIA) as a host row."""
    res = res_dir(arch)
    bm, ms = labels(arch)
    st = storage(arch=arch)
    cust = {int(f.split(".")[0]) for f in os.listdir(CUST)} if os.path.isdir(CUST) else set()
    rows, users = [], {}
    infos = {}
    for lab, mid in sorted(ms.items()):
        b = open(os.path.join(res, f"{mid}.Mesh_Z"), "rb").read()
        m = parse(b)
        mat = material_of(b, res)
        mi = material_info(mat, res) if mat else None
        infos[lab] = (b, m, mi)
        for role, t in (mi["textures"].items() if mi else []):
            users.setdefault(t, []).append(lab)
    for lab, (b, m, mi) in infos.items():
        geo = [v for v in m["vbs"] if v["flags"] == 2]
        importable = len(m["vbs"]) == 1 and len(geo) == 1 and len(m["ibs"]) == 1 and len(m["subs"]) == 1 and geo[0]["stride"] == 48
        tex = []
        for role, t in mi["textures"].items():
            i = texlib.info(os.path.join(res, f"{t}.Bitmap_Z"))
            tex.append(dict(role=role, id=t, label=bm.get(t, str(t)), size=f"{i['w']}x{i['h']}", fmt=i["fmt"],
                            storage=st.get(t, ("group", 0))[0], in_customization=t in cust,
                            shared_with=sorted(set(users.get(t, [])) - {lab})))
        rows.append(dict(label=lab, id=ms[lab], piece=PIECE.get(lab, "?"), importable=importable,
                         stride=m["vbs"][0]["stride"], vertices=geo[0]["count"] if geo else 0, triangles=m["ibs"][0]["count"] // 3,
                         storage=st.get(ms[lab], ("?", 0))[0], stored=st.get(ms[lab], ("?", 0))[1],
                         material=mi, textures=tex, customization=any(t["in_customization"] for t in tex),
                         replaces=sorted({lab} | {u for t in tex for u in t["shared_with"]})))
    return rows


def suitable(row, kind):
    """Score a host for a part kind (cloth | skin | hair). Lower is better; None = not suitable."""
    if not row["importable"]:
        return None
    mi = row["material"]
    roles = {t["role"]: t for t in row["textures"]}
    if "colour" not in roles or "normal" not in roles or row["customization"]:
        return None
    big = int(roles["colour"]["size"].split("x")[0])
    want = {"cloth": "opaque", "skin": "skin-like", "hair": "alpha cutout"}[kind]
    if mi["cls"] != want:
        return None
    if row["label"] in ("M05", "M03"):   # never seen in game
        return None
    score = -big
    if mi["mask_style"].startswith("R"):
        score -= 1   # host-style mask: the decoded convention
    if row["label"] in PROVEN:
        score -= 2   # enlarged and textured in game already
    return score


def recommend(kind, exclude=(), arch="P_AMICIA", rows=None, allowed=None):
    """Best host per texture set (slots sharing a colour texture share their textures: one part per set), best first.
    allowed (M16): only these labels, e.g. the archive's outfit pieces, so a host never replaces a kept body piece."""
    if kind == "hair":
        return []   # M15: the hair slots and their CUSTOMIZATION copies are decided there
    rows = [r for r in (rows or table(arch)) if r["label"] not in exclude and (allowed is None or r["label"] in allowed)]
    best = {}
    for r in rows:
        sc = suitable(r, kind)
        if sc is None:
            continue
        key = next(t["id"] for t in r["textures"] if t["role"] == "colour")
        cand = (sc, -r["vertices"], r["label"])
        if key not in best or cand < best[key]:
            best[key] = cand
    return [lab for _, _, lab in sorted(best.values())]
