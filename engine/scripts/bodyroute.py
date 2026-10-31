"""M16: the full-body route in every Amicia archive. One body.glb (cloth / skin parts on the kit armature) -> per archive:
which outfit pieces exist (roles), which of them host each part, which parts must share a host (packing), what is hidden.

Pieces (notes\\part_map.md, notes\\variants.md; boxes checked in session 45). Every mesh of an archive is in exactly one of:
  outfit  hidden by default, by role (keep a role or a label with "keep"); hosts are chosen only among these
  body    never touched: face, hands (and END's knee skin E-05), eyes, lashes, mouth
  other   never touched: never drawn or unknown (M03-like B03 pieces, M05 / E-13 belt studs)
  hair    never touched by full_body (hairedit.hair_set; only a "hair" edit replaces it)
Host choice (per archive, part by part in the manifest's order): hosts.recommend(kind) among the outfit pieces that are
not kept, one part per texture set, colour texture at least MIN_HOST (1024); skin parts take a skin-like host, else a
cloth host (shaded like cloth). When no host is left, the part is PACKED with the host group that has the fewest
vertices: each part's used UV area (its UV bounding box) gets its own rectangle (cell) of the host's textures, scaled
the same along u and v (M16c: no stretch; the M16b vertical strips squeezed u only), cells sized by each part's share of
the texels (default: equal texel density per surface area, skin parts SKIN_SHARE of that), 4-texel gutters. Overrides per archive: {"hosts": {object: label}, "keep": [...],
"hide": [...]} (hide adds pieces, e.g. a body or other piece on purpose).
M17 full body swap (replace={"head", "hands", "keep_eyes"}): "head" also hides the game's head, mouth and teeth, eyes
and lashes (REPLACE_HEAD; "keep_eyes" keeps eyes and lashes), "hands" the game's hands (REPLACE_HANDS); the skin part
supplies them. The freed slots are not used as hosts.
Usage: python -I bodyroute.py [<glb> [--keep necklace,...]]    (prints the pieces and, with a glb, the plan per archive)
"""
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fsx  # noqa: E402  (renames with a retry: Windows may hold a new file)
import hosts  # noqa: E402

ARCHS = ["P_AMICIA", "P_AMICIA_CHP1", "P_AMICIA_CHP2", "P_AMICIA_END", "P_AMICIA_UNIVERSITY"]
SHORT = {"P_AMICIA": "base", "P_AMICIA_CHP1": "chp1", "P_AMICIA_CHP2": "chp2", "P_AMICIA_END": "end",
         "P_AMICIA_UNIVERSITY": "university"}
CHAPTER = {"P_AMICIA": "III-XVI", "P_AMICIA_CHP1": "I", "P_AMICIA_CHP2": "II", "P_AMICIA_END": "XVII",
           "P_AMICIA_UNIVERSITY": "none found (unused)"}
MIN_HOST = 1024
GUTTER = 4
MIN_CELL = 32       # smallest inner cell side (texels)
MAX_STRETCH = 2.0   # a packed cell may grow into free texels up to this u:v scale ratio (M16b strips: up to 13)
SKIN_SHARE = 0.25   # default share of a packed skin part against cloth of the same surface (half the texel density)
DEFAULT_KEEP = ["necklace"]
REPLACE_HEAD = ["face", "mouth", "eyes", "lashes"]   # body roles hidden by "replace the game's head"
REPLACE_HANDS = ["hands"]                            # END's knee skin E-05 is not a hand: it stays
B40 = ["04", "10", "11", "17", "21", "23", "31", "35", "39", "43", "44"]
PIECES = {
    "P_AMICIA": dict(
        outfit={"pants": ["M18"], "corset": ["M25"], "boots": ["M38"], "shirt": ["M33"], "shield": ["M22"],
                "hood": ["M32"], "hood_buttons": ["M12"], "ruffles": ["M13"], "inner_clothes": ["M30"],
                "belt": [f"M{x}" for x in B40], "hand_wrap": ["M29", "M42"]},
        body={"face": ["M37"], "hands": ["M00"], "eyes": ["M28"], "lashes": ["M36"], "mouth": ["M26"]},
        other={"unknown (never seen)": ["M03"], "belt studs (never drawn)": ["M05"]}),
    "P_AMICIA_CHP1": dict(
        outfit={"pants_boots": ["C1-05", "C1-10"], "shirt": ["C1-02", "C1-23"], "pouch": ["C1-18"], "necklace": ["C1-14"]},
        body={"face": ["C1-19"], "hands": ["C1-22"], "eyes": ["C1-09", "C1-07"], "lashes": ["C1-12"], "mouth": ["C1-15"]},
        other={"unknown (never seen)": ["C1-21"]}),
    "P_AMICIA_CHP2": dict(
        outfit={"pants_boots": ["C2-05", "C2-26"], "shirt": ["C2-08", "C2-10"], "pouch": ["C2-27"], "necklace": ["C2-04"]},
        body={"face": ["C2-00"], "hands": ["C2-16"], "eyes": ["C2-01"], "lashes": ["C2-11"], "mouth": ["C2-09"]},
        other={"unknown (never seen)": ["C2-22"]}),
    "P_AMICIA_END": dict(
        outfit={"belt": ["E-08", "E-10", "E-15", "E-18", "E-22", "E-28", "E-35", "E-37", "E-39", "E-41", "E-43"],
                "corset_boots_pants": ["E-31", "E-36", "E-38"], "fur": ["E-14", "E-29"], "arm_wear": ["E-02", "E-21"],
                "sleeve": ["E-09", "E-44"], "shirt": ["E-27"], "shield": ["E-19"], "hand_wrap": ["E-32"]},
        body={"face": ["E-04"], "hands": ["E-25"], "knee_skin": ["E-05"], "eyes": ["E-16"], "lashes": ["E-11"],
              "mouth": ["E-30"]},
        other={"unknown (never seen)": ["E-24"], "belt studs (never drawn)": ["E-13"]}),
    "P_AMICIA_UNIVERSITY": dict(
        outfit={"pants": ["U-26"], "corset": ["U-17"], "boots": ["U-46"], "shirt": ["U-11"], "shield": ["U-21"],
                "hood": ["U-32"], "hood_buttons": ["U-34"], "ruffles": ["U-07"], "inner_clothes": ["U-30"],
                "belt": ["U-00", "U-01", "U-06", "U-13", "U-14", "U-15", "U-20", "U-23", "U-25", "U-27", "U-28", "U-36",
                         "U-37", "U-43", "U-45"],
                "hand_wrap": ["U-40", "U-35"], "extras": ["U-08"]},
        body={"face": ["U-09"], "hands": ["U-05"], "eyes": ["U-18"], "lashes": ["U-02", "U-39"], "mouth": ["U-16"]},
        other={"unknown (never seen)": ["U-04"]}),
}


class PlanError(Exception):
    pass


def pieces(arch):
    """(outfit {label: role}, body {label: role}, other {label: role}, hair [labels]); every mesh in exactly one."""
    import hairedit
    _, ms = hosts.labels(arch)
    p = PIECES[arch]
    flat = lambda d: {l: r for r, ls in d.items() for l in ls}
    o, b, x = flat(p["outfit"]), flat(p["body"]), flat(p["other"])
    hair = [h[1] for h in hairedit.hair_set(arch)]
    seen = list(o) + list(b) + list(x) + hair
    if sorted(seen) != sorted(ms) or len(set(seen)) != len(seen):
        raise PlanError(f"{arch}: piece table does not cover every mesh exactly once: missing {sorted(set(ms) - set(seen))}, "
                        f"unknown {sorted(set(seen) - set(ms))}")
    return o, b, x, hair


def resolve_keep(arch, keep):
    """Roles or labels -> labels of this archive (roles that do not exist here are ignored)."""
    o, b, x, _ = pieces(arch)
    out = set()
    for k in keep:
        out |= {l for l, r in o.items() if r == k} | ({k} if k in o else set())
    return sorted(out)


def sphere_radius(arch, label):
    import struct
    _, ms = hosts.labels(arch)
    b = open(os.path.join(hosts.res_dir(arch), f"{ms[label]}.Mesh_Z"), "rb").read()
    return struct.unpack_from("<f", b, 64 + 28)[0]


def host_score(arch, row, kind, role):
    """(score, reason) of an outfit piece as host for a part kind (lower is better), or None. M16 rules (after the first
    in-game rehearsal): colour + normal of its own, colour not in CUSTOMIZATION, colour >= MIN_HOST; cloth: opaque, or an
    alpha-cutout material with a BC3 colour (alpha written 255, e.g. END's corset/pants/boots) that is not hair or eye;
    skin: skin-like. Never the small belt fittings (radius < 0.1 m: their drawing seems to depend on game state; END's
    E-28 = M10 hid the whole leather part in chapter XVII). Better: larger colour, an R-style mask, SEEN drawn in this
    archive's chapter (colour test), PROVEN (one of our meshes seen in it in game)."""
    if not row["importable"]:
        return None
    roles = {t["role"]: t for t in row["textures"]}
    if "colour" not in roles or "normal" not in roles or roles["colour"]["in_customization"]:
        return None
    size = int(roles["colour"]["size"].split("x")[0])
    if size < MIN_HOST:
        return None
    mi = row["material"]
    if kind == "skin":
        if mi["cls"] != "skin-like":
            return None
    elif not (mi["cls"] == "opaque" or (mi["cls"] == "alpha cutout" and roles["colour"]["fmt"] == "BC3"
                                       and mi["model"] not in (63, 64))):
        return None
    if role == "belt" and sphere_radius(arch, row["label"]) < 0.1:
        return None
    lab = row["label"]
    seen = lab in PROVEN.get(arch, ()) or lab in SEEN.get(arch, ())
    rstyle = "mask" in roles and mask_style(arch, roles["mask"]["id"]) == "R"
    why = ("proven in game" if lab in PROVEN.get(arch, ()) else "seen drawn in its chapter" if seen else "not yet seen in game")
    return (0 if seen else 1, -size, -rstyle, -row["vertices"], lab), why


_STYLE = {}


def mask_style(arch, bid):
    """'R' (host style: R varies, G = B = 255) or 'G' (belt style: R = B = 0, G varies), read from the texture itself
    (session 46: END's E-38 uses B14 = R style without the material flag 0x00800000)."""
    if (arch, bid) not in _STYLE:
        import texlib
        w, h, _, img = texlib.load(hosts.res_dir(arch), bid)
        n = w * h
        g_const = all(img[4 * i + 1] == 255 and img[4 * i + 2] == 255 for i in range(0, n, 61))
        r_zero = all(img[4 * i] == 0 and img[4 * i + 2] == 0 for i in range(0, n, 61))
        _STYLE[(arch, bid)] = "R" if g_const else "G" if r_zero else "?"
    return _STYLE[(arch, bid)]


# in-game evidence per archive: PROVEN = one of our meshes was seen in it; SEEN = its texture was seen drawn in the
# chapter's colour test (notes\texture_map.md, variant tests) on that kind of piece
PROVEN = {"P_AMICIA": ("M38", "M32", "M42"), "P_AMICIA_CHP1": ("C1-10",), "P_AMICIA_CHP2": ("C2-26",),
          "P_AMICIA_END": ("E-02",)}
SEEN = {"P_AMICIA": ("M18", "M25", "M30", "M21", "M23", "M43"), "P_AMICIA_CHP1": ("C1-05",), "P_AMICIA_CHP2": ("C2-05",),
        "P_AMICIA_END": ("E-31", "E-36", "E-38", "E-21", "E-09", "E-44", "E-32")}


def candidates(arch, kind, rows, allowed, o):
    """Best host per texture set, best first: [(label, why)]."""
    best = {}
    for r in rows:
        if r["label"] not in allowed:
            continue
        sc = host_score(arch, r, kind, o.get(r["label"]))
        if sc is None:
            continue
        key = next(t["id"] for t in r["textures"] if t["role"] == "colour")
        if key not in best or sc[0] < best[key][0]:
            best[key] = (sc[0], r["label"], sc[1])
    return [(lab, why) for _, lab, why in sorted(best.values())]


def replaced_body(arch, replace):
    """Body labels of this archive hidden by replace={"head": bool, "hands": bool, "keep_eyes": bool} (M17)."""
    replace = replace or {}
    roles = (REPLACE_HEAD if replace.get("head") else []) + (REPLACE_HANDS if replace.get("hands") else [])
    if replace.get("keep_eyes"):
        roles = [r for r in roles if r not in ("eyes", "lashes")]
    return sorted(l for l, r in pieces(arch)[1].items() if r in roles)


def plan(arch, parts, keep=None, override=None, rows=None, replace=None):
    """parts: [{object, kind, vertices, area?, share?, archives?}] in manifest order. Returns dict(groups=[{host, objects,
    kinds, why, strips?}], hide, kept, notes, skipped, replaced). Raises PlanError. replace: see replaced_body."""
    override = override or {}
    keep = list(DEFAULT_KEEP if keep is None else keep) + list(override.get("keep", []))
    o, b, x, hair = pieces(arch)
    rows = rows or hosts.table(arch)
    table = {r["label"]: r for r in rows}
    kept = resolve_keep(arch, keep)
    allowed = [l for l in o if l not in kept]
    groups, notes, used_sets, skipped = [], [], [], []
    for k in keep:
        if not resolve_keep(arch, [k]):
            notes.append(f"keep '{k}': no such piece in {arch} (ignored; if your model needs it, model it into your body)")

    def free(lab):
        return not any(set(table[lab]["replaces"]) & s for s in used_sets)
    for pt in parts:
        obj, kind = pt["object"], pt.get("kind", "cloth")
        if kind not in ("cloth", "skin"):
            raise PlanError(f"part '{obj}': kind {kind!r} (cloth or skin; hair is a separate \"hair\" edit)")
        if pt.get("archives") and arch not in [arch_name(a) for a in pt["archives"]]:
            skipped.append(obj)
            continue
        host, why = override.get("hosts", {}).get(obj), "given"
        if host is not None:
            if host not in table or not table[host]["importable"]:
                raise PlanError(f"{arch}: host {host} for '{obj}' is not an importable slot")
            if host not in o:
                notes.append(f"'{obj}': host {host} is not an outfit piece ({(b | x).get(host, 'hair')})")
        else:
            for k, w in ((kind, f"best free {kind} host"), ("cloth", "no free skin host: a cloth host (shaded like cloth)")):
                if k == kind or kind == "skin":
                    c = next(((h, ev) for h, ev in candidates(arch, k, rows, allowed, o) if free(h)), None)
                    if c:
                        host, why = c[0], f"{w} ({c[1]})"
                        break
        if host is None:
            if not groups:
                raise PlanError(f"{arch}: no host at all for '{obj}' (no importable outfit piece with its own textures)")
            g = min(groups, key=lambda g: g["vertices"])
            g["objects"].append(obj)
            g["kinds"].append(kind)
            g["vertices"] += pt.get("vertices", 0)
            g["why"].append(f"'{obj}': no free {'skin ' if kind == 'skin' else ''}host left: packed with "
                            f"{', '.join(repr(q) for q in g['objects'][:-1])} in {g['host']}"
                            + (" (shaded like cloth)" if kind == "skin" and table[g["host"]]["material"]["cls"] != "skin-like" else ""))
            continue
        if not free(host):
            raise PlanError(f"{arch}: host {host} for '{obj}' shares textures with another part's host")
        used_sets.append(set(table[host]["replaces"]))
        groups.append(dict(host=host, id=table[host]["id"], objects=[obj], kinds=[kind], vertices=pt.get("vertices", 0),
                           why=[f"'{obj}' -> {host}: {why}"]))
    byobj = {p["object"]: p for p in parts}
    shares_ov = override.get("shares", {})
    hosts_used = [g["host"] for g in groups]
    replaced = {u for g in groups for u in table[g["host"]]["replaces"]}
    swap = replaced_body(arch, replace)
    hide = sorted((set(allowed) | (replaced & set(o)) | set(override.get("hide", [])) | set(swap)) - set(hosts_used) - set(kept))
    shared_kept = sorted(set(kept) & replaced)
    bad = sorted((set(hide) | set(hosts_used)) & set(hair))
    if bad:
        raise PlanError(f"{arch}: full_body would touch hair pieces {bad}")
    for g in groups:
        g["texture_sharing_kept"] = sorted(set(table[g["host"]]["replaces"]) & set(kept))
        g["sizes"] = {t["role"]: t["size"] for t in table[g["host"]]["textures"]}
        g["class"] = table[g["host"]]["material"]["cls"]
        if len(g["objects"]) > 1:
            w = [float(shares_ov.get(ob) or byobj[ob].get("share") or default_share(byobj[ob])) for ob in g["objects"]]
            g["shares"] = [round(v / sum(w), 4) for v in w]
            W, H = (int(q) for q in g["sizes"]["colour"].split("x"))
            g["cells"] = cells(W, H, [dict(box=byobj[ob].get("uvbox") or [0.0, 1.0, 0.0, 1.0],
                                           tex=byobj[ob].get("texsize") or [1024, 1024], weight=v)
                                      for ob, v in zip(g["objects"], w)])
    visible = sorted(set(kept) | {l for l in o if l not in hide and l not in hosts_used})
    return dict(arch=arch, groups=groups, hide=hide, kept=kept, kept_sharing_host_textures=shared_kept,
                visible_outfit={l: o[l] for l in visible}, body={l: r for l, r in b.items() if l not in swap}, other=x,
                hair=hair, notes=notes, outfit=o, skipped=skipped, replaced={l: b[l] for l in swap})


def default_share(pt):
    """A packed part's default share of the host's texels: the same texel density per surface area for every part
    (surface area / UV area used x UV box area), skin parts SKIN_SHARE of that (skin has little detail)."""
    bx = pt.get("uvbox") or [0.0, 1.0, 0.0, 1.0]
    v = float(pt.get("area") or 1.0) * max(1e-4, (bx[1] - bx[0]) * (bx[3] - bx[2]))
    return v * (SKIN_SHARE if pt.get("kind") == "skin" else 1.0)


def uv_box(uvs, tw, th, pad=2):
    """[u0, u1, v0, v1]: the UVs' bounding box widened by pad texels of the part's own texture (tw x th), on texel
    boundaries, clamped to 0..1 (UVs outside 0..1 are clamped when packed: no tiling in a packed part)."""
    us, vs = [min(1.0, max(0.0, q[0])) for q in uvs], [min(1.0, max(0.0, q[1])) for q in uvs]
    return [max(0.0, (math.floor(min(us) * tw) - pad) / tw), min(1.0, (math.ceil(max(us) * tw) + pad) / tw),
            max(0.0, (math.floor(min(vs) * th) - pad) / th), min(1.0, (math.ceil(max(vs) * th) + pad) / th)]


def _shelves(sizes, W, H):
    """Shelf packing of (w, h) outer sizes, tallest first: [(x, y)] in input order, or None if they do not fit."""
    order = sorted(range(len(sizes)), key=lambda k: (-sizes[k][1], -sizes[k][0]))
    pos, x, y, shelf = [None] * len(sizes), 0, 0, 0
    for k in order:
        w, h = sizes[k]
        if w > W:
            return None
        if x + w > W:
            x, y, shelf = 0, y + shelf, 0
        if y + h > H:
            return None
        pos[k] = (x, y)
        x += w
        shelf = max(shelf, h)
    return pos


def _fill(rect, W, H, g):
    """Grow the packed cells ([x0, y0, x1, y1] outer, texels) into the free texels, 4 at a time, round robin, each in the
    direction that keeps it closest to its packed aspect; a cell stops at MAX_STRETCH (u scale / v scale)."""
    base = [(r[2] - r[0] - 2 * g, r[3] - r[1] - 2 * g) for r in rect]

    def free(k, c):
        if c[0] < 0 or c[1] < 0 or c[2] > W or c[3] > H:
            return False
        return all(j == k or c[2] <= o[0] or o[2] <= c[0] or c[3] <= o[1] or o[3] <= c[1] for j, o in enumerate(rect))
    stuck = set()
    while len(stuck) < len(rect):   # the cell that has grown least (texel area against its packed area) grows first
        k = min((j for j in range(len(rect)) if j not in stuck),
                key=lambda j: (rect[j][2] - rect[j][0] - 2 * g) * (rect[j][3] - rect[j][1] - 2 * g) / (base[j][0] * base[j][1]))
        r = rect[k]
        fw, fh = (r[2] - r[0] - 2 * g) / base[k][0], (r[3] - r[1] - 2 * g) / base[k][1]
        done = False
        for d, _ in sorted([("w", fw), ("h", fh)], key=lambda d: d[1]):
            nfw, nfh = (fw + 4 / base[k][0], fh) if d == "w" else (fw, fh + 4 / base[k][1])
            if max(nfw, nfh) / min(nfw, nfh) > MAX_STRETCH:
                continue
            tries = ([r[0], r[1], r[2] + 4, r[3]], [r[0] - 4, r[1], r[2], r[3]]) if d == "w" else \
                    ([r[0], r[1], r[2], r[3] + 4], [r[0], r[1] - 4, r[2], r[3]])
            c = next((c for c in tries if free(k, c)), None)
            if c:
                rect[k] = c
                done = True
                break
        if not done:
            stuck.add(k)
    return rect


def cells(W, H, items):
    """items: [dict(box=[u0, u1, v0, v1] (the part's UV box), tex=[w, h] (its texture size), weight)] -> per item a cell
    dict(x0, x1, y0, y1: the inner rectangle in host UV; ox0, ox1, oy0, oy1: with its gutter; src: the UV box; px: inner
    w x h texels). Each cell keeps its UV box's aspect in texels (the same scale along u and v), its texel area follows
    the weights: shelf packing in rows or in columns at the largest common scale that fits and at smaller ones, each
    grown into the free texels up to MAX_STRETCH (_fill); kept: the layout whose worst-off part (texels per share) has
    the most, then the most texels in all (4-texel boundaries)."""
    tot = sum(float(it["weight"]) for it in items)
    shape = []
    for it in items:
        b, (tw, th) = it["box"], it["tex"]
        asp = max(1e-3, (b[1] - b[0]) * tw) / max(1e-3, (b[3] - b[2]) * th)
        f = float(it["weight"]) / tot
        shape.append((math.sqrt(f * asp), math.sqrt(f / asp)))
    g = GUTTER

    def outer(S):
        return [(4 * math.ceil((max(MIN_CELL, S * a) + 2 * g) / 4), 4 * math.ceil((max(MIN_CELL, S * c) + 2 * g) / 4))
                for a, c in shape]

    def place(S, cols):
        sz = outer(S)
        if not cols:
            p = _shelves(sz, W, H)
            return None if p is None else (sz, p)
        q = _shelves([(h, w) for w, h in sz], H, W)   # in columns
        return None if q is None else (sz, [(y, x) for x, y in q])
    wts = [float(it["weight"]) / tot for it in items]
    best = None
    for cols in (False, True):
        if place(0.0, cols) is None:
            continue
        lo, hi = 0.0, 4.0 * max(W, H)
        for _ in range(40):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if place(mid, cols) is not None else (lo, mid)
        for f in (1.0, 0.95, 0.9, 0.85, 0.8, 0.75, 0.7, 0.65, 0.6, 0.55, 0.5):   # a smaller start can pack better
            sz, pos = place(lo * f, cols)
            rect = _fill([[x, y, x + w, y + h] for (w, h), (x, y) in zip(sz, pos)], W, H, g)
            area = [(r[2] - r[0] - 2 * g) * (r[3] - r[1] - 2 * g) for r in rect]
            score = (min(ar / wt for ar, wt in zip(area, wts)), sum(area))   # the worst-off part first, then the total
            if best is None or score > best[0]:
                best = (score, rect)
    if best is None:
        raise PlanError(f"{len(items)} parts do not fit in one {W}x{H} host even at the smallest cell size")
    rect = best[1]
    out = []
    for it, (x, y, x2, y2) in zip(items, rect):
        w, h = x2 - x, y2 - y
        out.append(dict(x0=(x + g) / W, x1=(x + w - g) / W, y0=(y + g) / H, y1=(y + h - g) / H,
                        ox0=x / W, ox1=(x + w) / W, oy0=y / H, oy1=(y + h) / H, src=list(it["box"]), px=[w - 2 * g, h - 2 * g]))
    return out


def cell_uv(u, v, c):
    """A part's UV -> its cell (clamped to its UV box)."""
    b = c["src"]
    u = min(b[1], max(b[0], u))
    v = min(b[3], max(b[2], v))
    return (c["x0"] + (u - b[0]) / (b[1] - b[0]) * (c["x1"] - c["x0"]), c["y0"] + (v - b[2]) / (b[3] - b[2]) * (c["y1"] - c["y0"]))


def texels_per_m(texels_u, texels_v, pt):
    """Texels per metre of a part whose whole UV square maps to texels_u x texels_v texels: sqrt(texels per unit UV area
    / surface per unit UV area); pt["area"] = surface area / UV area used."""
    return math.sqrt(texels_u * texels_v / max(1e-9, float(pt.get("area") or 1.0)))


def cell_density(c, pt):
    """(texels per metre, stretch u/v) of a packed part in its cell."""
    b = c["src"]
    tu, tv = c["px"][0] / (b[1] - b[0]), c["px"][1] / (b[3] - b[2])
    return texels_per_m(tu, tv, pt), tu / tv


def arch_name(x):
    """'chp1' / 'P_AMICIA_CHP1' / 'P_AMICIA_CHP1.DPC' -> 'P_AMICIA_CHP1'."""
    x = x[:-4] if x.upper().endswith(".DPC") else x
    for a, s in SHORT.items():
        if x.upper() in (a, s.upper()):
            return a
    raise PlanError(f"unknown archive {x!r} (one of {', '.join(SHORT.values())})")


def body_archives(ed):
    a = ed.get("archives")
    return ARCHS if a == "all" else [arch_name(x) for x in a]


def test_name_for(base_name, arch):
    stem = base_name[:-5] if base_name.endswith("_base") else base_name
    return base_name if arch == "P_AMICIA" else f"{stem}_{SHORT[arch]}"


def expand(man):
    """The manifest's archives as [dict(archive, test_name, edits, checks, base)]. A full_body edit with "archives" in the
    base archive's edits is applied to every listed archive: a variant that is not in the manifest gets its own entry
    (test name <base test name without _base>_<short>), an existing variant gets the edit at the same position."""
    out = [dict(archive=man["archive"], test_name=man["test_name"], edits=list(man["edits"]), checks=man.get("checks", []), base=True)]
    out += [dict(v, edits=list(v.get("edits", [])), checks=v.get("checks", []), base=False) for v in man.get("variants", [])]
    for pos, ed in enumerate(man["edits"]):
        if ed.get("type") != "full_body" or "archives" not in ed:
            continue
        want = body_archives(ed)
        if "P_AMICIA" not in want:
            out[0]["edits"] = [e for e in out[0]["edits"] if e is not ed]
        for a in want:
            if a == "P_AMICIA":
                continue
            s = next((s for s in out if s["archive"][:-4] == a), None)
            if s is None:
                out.append(dict(archive=a + ".DPC", test_name=test_name_for(man["test_name"], a), edits=[ed], checks=[], base=False))
            elif ed not in s["edits"]:
                s["edits"].insert(min(pos, len(s["edits"])), ed)
    return out


def override_for(ed, arch):
    po = ed.get("per_archive", {})
    for k, v in po.items():
        if arch_name(k) == arch:
            return v
    return {}


def part_texsize(g, node_index):
    """(w, h) of an object's base colour image in a loaded glTF (the PNG header), or (1024, 1024) without one."""
    import struct
    doc = g.doc
    prims = doc["meshes"][doc["nodes"][node_index]["mesh"]]["primitives"]
    mat = doc.get("materials", [{}])[prims[0].get("material", 0)] if doc.get("materials") else {}
    ref = mat.get("pbrMetallicRoughness", {}).get("baseColorTexture")
    if not ref:
        return (1024, 1024)
    im = doc["images"][doc["textures"][ref["index"]]["source"]]
    if "bufferView" not in im:
        return (1024, 1024)
    bv = doc["bufferViews"][im["bufferView"]]
    head = g.bins[bv.get("buffer", 0)][bv.get("byteOffset", 0):bv.get("byteOffset", 0) + 24]
    return struct.unpack(">II", head[16:24]) if head[:8] == b"\x89PNG\r\n\x1a\n" else (1024, 1024)


def pack_glb(src, objects, cell_list, dst, name):
    """Copy of src with one more object `name`: the given objects' primitives with TEXCOORD_0 moved into their cells
    (new accessors; positions, normals, joints, weights, indices shared). All objects must use the same skin."""
    import struct
    raw = open(src, "rb").read()
    jl = struct.unpack_from("<I", raw, 12)[0]
    doc = json.loads(raw[20:20 + jl])
    bl = struct.unpack_from("<I", raw, 20 + jl)[0]
    blob = bytearray(raw[28 + jl:28 + jl + bl])
    import gltfread
    g = gltfread.load(src)
    prims, skin = [], None
    for obj, s in zip(objects, cell_list):
        node = doc["nodes"][g.find_node(obj)]
        if skin is not None and node.get("skin") != skin:
            raise PlanError(f"packed objects use different armatures ('{obj}')")
        skin = node.get("skin")
        for p in doc["meshes"][node["mesh"]]["primitives"]:
            uv = g.accessor(p["attributes"]["TEXCOORD_0"])
            data = b"".join(struct.pack("<2f", *cell_uv(u[0], u[1], s)) for u in uv)
            while len(blob) % 4:
                blob.append(0)
            doc["bufferViews"].append(dict(buffer=0, byteOffset=len(blob), byteLength=len(data), target=34962))
            blob += data
            doc["accessors"].append(dict(bufferView=len(doc["bufferViews"]) - 1, componentType=5126, count=len(uv), type="VEC2"))
            q = dict(p, attributes=dict(p["attributes"], TEXCOORD_0=len(doc["accessors"]) - 1))
            for k in [k for k in q["attributes"] if k.startswith("TEXCOORD_") and k != "TEXCOORD_0"]:
                del q["attributes"][k]
            prims.append(q)
    doc["meshes"].append(dict(name=name, primitives=prims))
    doc["nodes"].append(dict(name=name, mesh=len(doc["meshes"]) - 1, skin=skin))
    doc["scenes"][doc.get("scene", 0)]["nodes"].append(len(doc["nodes"]) - 1)
    while len(blob) % 4:
        blob.append(0)
    doc["buffers"][0]["byteLength"] = len(blob)
    js = json.dumps(doc).encode()
    js += b" " * (-len(js) % 4)
    out = struct.pack("<III", 0x46546C67, 2, 28 + len(js) + len(blob)) + struct.pack("<II", len(js), 0x4E4F534A) + js \
        + struct.pack("<II", len(blob), 0x004E4942) + blob
    open(dst + ".tmp", "wb").write(out)
    fsx.replace(dst + ".tmp", dst)


def _axis_map(n, m):
    """For each of m output texels along one axis: ("a", i0, i1) average of input texels i0..i1-1 (shrinking) or
    ("l", i0, i1, t) linear between i0 and i1 (growing; edge clamp)."""
    if m <= n:
        f = n / m
        return [("a", int(x * f), max(int(x * f) + 1, int((x + 1) * f))) for x in range(m)]
    out = []
    for x in range(m):
        p = (x + 0.5) * n / m - 0.5
        i0 = int(math.floor(p))
        out.append(("l", min(n - 1, max(0, i0)), min(n - 1, max(0, i0 + 1)), p - i0))
    return out


def resample_xy(img, w, h, W, H):
    """RGBA w x h -> W x H, each axis on its own: area average where that axis shrinks, linear (edge clamp) where it
    grows (hosttex.resample is bilinear as soon as one axis grows: a shrinking axis then aliases)."""
    mx, my = _axis_map(w, W), _axis_map(h, H)
    tmp = bytearray(W * h * 4)
    for y in range(h):
        row = img[4 * y * w:4 * (y + 1) * w]
        o = 4 * y * W
        for x, e in enumerate(mx):
            for c in range(4):
                if e[0] == "a":
                    tmp[o + 4 * x + c] = round(sum(row[4 * k + c] for k in range(e[1], e[2])) / (e[2] - e[1]))
                else:
                    tmp[o + 4 * x + c] = round(row[4 * e[1] + c] * (1 - e[3]) + row[4 * e[2] + c] * e[3])
    out = bytearray(W * H * 4)
    for y, e in enumerate(my):
        o = 4 * y * W
        if e[0] == "a":
            n = e[2] - e[1]
            rows = [tmp[4 * k * W:4 * (k + 1) * W] for k in range(e[1], e[2])]
            out[o:o + 4 * W] = bytes(round(sum(r[i] for r in rows) / n) for i in range(4 * W))
        else:
            a, b, t = tmp[4 * e[1] * W:4 * (e[1] + 1) * W], tmp[4 * e[2] * W:4 * (e[2] + 1) * W], e[3]
            out[o:o + 4 * W] = bytes(round(a[i] * (1 - t) + b[i] * t) for i in range(4 * W))
    return out


def pack_images(per_part, cell_list, sizes, defaults):
    """per_part: [{map: (w, h, rgba)}]; sizes: {map: (W, H)}; defaults: {map: rgba of a part without that map, also for
    the unused texels}. Returns {map: (W, H, rgba)}: each part's UV box resampled into its cell (resample_xy); the gutters
    repeat the cell's edge texels."""
    out = {}
    for mp, (W, H) in sizes.items():
        img = bytearray(bytes(defaults[mp]) * (W * H))
        for ims, c in zip(per_part, cell_list):
            sw, sh, src = ims.get(mp) or (1, 1, bytearray(defaults[mp]))
            b = c["src"]
            cx0, cy0 = min(sw - 1, int(round(b[0] * sw))), min(sh - 1, int(round(b[2] * sh)))
            cx1, cy1 = max(cx0 + 1, int(round(b[1] * sw))), max(cy0 + 1, int(round(b[3] * sh)))
            crop = bytearray()
            for y in range(cy0, cy1):
                crop += src[4 * (y * sw + cx0):4 * (y * sw + cx1)]
            x0, x1, y0, y1 = round(c["x0"] * W), round(c["x1"] * W), round(c["y0"] * H), round(c["y1"] * H)
            ox0, ox1, oy0, oy1 = round(c["ox0"] * W), round(c["ox1"] * W), round(c["oy0"] * H), round(c["oy1"] * H)
            iw, ih = max(1, x1 - x0), max(1, y1 - y0)
            r = resample_xy(crop, cx1 - cx0, cy1 - cy0, iw, ih)
            for y in range(oy0, oy1):
                sy = min(ih - 1, max(0, y - y0))
                row = r[4 * sy * iw:4 * (sy + 1) * iw]
                o = 4 * y * W
                img[o + 4 * x0:o + 4 * (x0 + iw)] = row
                img[o + 4 * ox0:o + 4 * x0] = row[:4] * (x0 - ox0)
                img[o + 4 * (x0 + iw):o + 4 * ox1] = row[-4:] * (ox1 - x0 - iw)
        out[mp] = (W, H, img)
    return out


def main():
    if len(sys.argv) < 2:
        for a in ARCHS:
            o, b, x, hair = pieces(a)
            print(f"{a} (chapter {CHAPTER[a]}): outfit {len(o)}, body {len(b)}, other {len(x)}, hair {len(hair)}")
        return
    import gltfread
    g = gltfread.load(sys.argv[1])
    keep = sys.argv[sys.argv.index("--keep") + 1].split(",") if "--keep" in sys.argv else None
    parts = []
    for ni, name in g.mesh_nodes():
        n = sum(g.doc["accessors"][p["attributes"]["POSITION"]]["count"] for p in g.doc["meshes"][g.doc["nodes"][ni]["mesh"]]["primitives"])
        parts.append(dict(object=name, kind="skin" if "skin" in name.lower() else "cloth", vertices=n))
    for a in ARCHS:
        p = plan(a, parts, keep)
        print(json.dumps(dict(arch=a, groups=[(x["host"], x["objects"], x["why"]) for x in p["groups"]], hide=p["hide"],
                              kept=p["kept"], visible=p["visible_outfit"]), indent=None))


if __name__ == "__main__":
    main()
