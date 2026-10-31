"""check-model hair mode (M15): check a hair .glb (one object "hair", route A: UVs on the game's B01 atlas) against the
hair host of every Amicia archive before building. Builds nothing.

File / object: one mesh object named "hair" (or --object), one material, one UV map, triangles only.
Counts: vertices against the 65,535 limit; per archive the host (hairedit.pick_host: M02 / C1-20 / C2-20 / E-03 / U-48),
its budget, enlargement and the estimated stored size.
Bones: every weighted bone by kind (safe: head, neck, neck1, spine, spine1, spine2; chain: hair1-5; avoid: the lock
attachment bones 269-282, the named face bones, the unnamed bone_NNN under head, other body bones); per archive the bones
that do not exist there and what they fall back to (CHP1: hair1-5 -> head, no swing).
Chain (tips and roots): chain level = weighted mean of head/neck 0, hair1..hair5 1..5.
  - static tip: vertices on head/neck only next to the swinging end (the bug the M15 mock showed in chapters II / XVII);
  - lag: vertices low on the chain that mostly follow upper bones (stiff, stretched);
  - roots: chain weight on vertices lying on the scalp (they lift off when it swings);
  - tears: vertices at the same position with different weights (they split apart when it moves);
  - length below hair5 (follows hair5 rigidly) and the game braid's length for comparison.
UVs and alpha (B01): UVs inside 0..1, zero-area UV triangles, the share of the surface on transparent / drawn B01 texels,
cards that are almost fully transparent, texel density against the game's own hair.
Clearance (bind pose, the archive's original head): vertices inside the head per archive; vertices close to the outfit
pieces next to the hair (hood, ruffles, fur collar, tops).
Every finding is OK, WARNING or ERROR with one line of advice. Exit code 1 if there is an ERROR.
Usage: python -I haircheck.py <hair.glb> [--object hair]     (or: modtool.py check-model <hair.glb> --hair)
"""
import argparse
import glob
import json
import math
import mmap
import os
import random
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paths  # noqa: E402
import blendimport  # noqa: E402
import dpc  # noqa: E402
import gltfread  # noqa: E402
import growdpc  # noqa: E402
import hairedit  # noqa: E402
import mergemesh  # noqa: E402
import skel  # noqa: E402
import texlib  # noqa: E402
from blender_kit import load_mesh  # noqa: E402
from meshfmt import parse  # noqa: E402

OUT = paths.OUT
ROOT = paths.REPO
ARCH = [("P_AMICIA", "III-XVI"), ("P_AMICIA_CHP1", "I"), ("P_AMICIA_CHP2", "II"), ("P_AMICIA_END", "XVII"),
        ("P_AMICIA_UNIVERSITY", "unused")]
FACE = {"P_AMICIA": 4840249479922744736, "P_AMICIA_CHP1": 6286021572469189072, "P_AMICIA_CHP2": -8233843334373610856,
        "P_AMICIA_END": -7471950747059094763, "P_AMICIA_UNIVERSITY": -4766798222248043362}   # the face mesh M37 / its copies
CLEAR = {"P_AMICIA": [("M32", "hood"), ("M13", "ruffles")], "P_AMICIA_END": [("E-29", "fur collar")],
         "P_AMICIA_CHP1": [("C1-02", "top")], "P_AMICIA_CHP2": [("C2-10", "top")]}          # as in the hair kit
HEAD, NECKS = 129, (127, 128)
CHAIN = {260: 1, 261: 2, 262: 3, 263: 4, 264: 5}                                         # kit hair1-5 -> level
SAFE = {18, 20, 23, 127, 128, 129}                                                       # spine, spine1, spine2, neck, neck1, head
ATTACH = range(269, 283)
LZ4_RATIO = 0.75
TRANSPARENT, DRAWN = 64, 128            # B01 alpha: below = not drawn, at or above = drawn (the cutoff is a guess)
COUNTS = {"OK": 0, "WARNING": 0, "ERROR": 0}


MSGS = []   # M17: every finding with its code, for the app (--json)


def say(level, what, advice="", code=""):
    COUNTS[level] += 1
    MSGS.append(dict(level=level, code=code or level.lower(), text=what, advice=advice))
    print(f"  [{level}] {what}" + (f"\n            -> {advice}" if advice and level != "OK" else ""))


def write_json(path):
    """M17: the findings for the app: {"fatal": [...], "notes": [...]}, each {code, text, advice}."""
    pick = lambda lv: [dict(code=m["code"], text=m["text"], advice=m["advice"]) for m in MSGS if m["level"] == lv]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(dict(fatal=pick("ERROR"), notes=pick("WARNING")), f, indent=1)


def names():
    n = {int(k): v for k, v in json.load(open(os.path.join(paths.TABLES, "analysis", "bone_names.json"))).items()}
    return lambda i: n.get(i, f"bone_{i:03d}"), set(n)


def under(bones, i, top):
    while i >= 0:
        if i == top:
            return True
        i = bones[i]["parent"]
    return False


def kind(bones, named, i):
    if i in SAFE:
        return "safe"
    if i in CHAIN:
        return "chain"
    if i in ATTACH:
        return "lock attachment bone (scaled bind matrix, keyed by animations)"
    if under(bones, i, HEAD):
        return "named face bone (moves with expressions)" if i in named else "unnamed head bone (face rig)"
    return "body bone"


def level(v):
    """Chain level of a vertex (kit bones): head / neck / others 0, hair1..5 1..5, weighted mean."""
    tot = sum(v["infl"].values()) or 1
    return sum(CHAIN.get(b, 0) * w for b, w in v["infl"].items()) / tot


def chainw(v):
    tot = sum(v["infl"].values()) or 1
    return sum(w for b, w in v["infl"].items() if b in CHAIN) / tot


class Grid:
    def __init__(self, P, cell):
        self.P, self.c, self.g = P, cell, {}
        for k, p in enumerate(P):
            self.g.setdefault(self.key(p), []).append(k)

    def key(self, p):
        return tuple(int(math.floor(x / self.c)) for x in p)

    def near(self, p, r=1):
        c = self.key(p)
        for dx in range(-r, r + 1):
            for dy in range(-r, r + 1):
                for dz in range(-r, r + 1):
                    yield from self.g.get((c[0] + dx, c[1] + dy, c[2] + dz), ())

    def nearest(self, p, r=1):
        best = (9.0, None)
        for k in self.near(p, r):
            d = math.dist(p, self.P[k])
            if d < best[0]:
                best = (d, k)
        return best


def surface(res, mid):
    """(positions, normals) of a game mesh in bind pose."""
    _, prims = load_mesh(os.path.join(res, f"{mid}.Mesh_Z"))
    P, N = [], []
    for vs, _, _, _ in prims:
        P += [v["p"] for v in vs]
        N += [v["n"] for v in vs]
    return P, N


def tri_area(a, b, c):
    e1 = [b[k] - a[k] for k in range(3)]
    e2 = [c[k] - a[k] for k in range(3)]
    cr = [e1[1] * e2[2] - e1[2] * e2[1], e1[2] * e2[0] - e1[0] * e2[2], e1[0] * e2[1] - e1[1] * e2[0]]
    return 0.5 * math.sqrt(sum(x * x for x in cr))


def uv_area(a, b, c):
    return 0.5 * abs((b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1]))


def islands(tris, n):
    par = list(range(n))

    def f(a):
        while par[a] != a:
            par[a] = par[par[a]]
            a = par[a]
        return a
    for a, b, c in tris:
        par[f(b)] = f(a)
        par[f(c)] = f(a)
    out = {}
    for k, t in enumerate(tris):
        out.setdefault(f(t[0]), []).append(k)
    return list(out.values())


def alpha_stats(P, UV, tris, b01, w, h, n_samples=40000, seed=15):
    """Area-weighted random samples of B01's alpha over the surface: per triangle (samples, drawn), totals."""
    areas = [tri_area(*(P[i] for i in t)) for t in tris]
    tot = sum(areas) or 1
    rnd = random.Random(seed)
    per = [[0, 0, 0] for _ in tris]            # samples, transparent, drawn
    for k, t in enumerate(tris):
        n = max(1, round(n_samples * areas[k] / tot))
        a, b, c = (UV[i] for i in t)
        for _ in range(n):
            r1, r2 = rnd.random(), rnd.random()
            if r1 + r2 > 1:
                r1, r2 = 1 - r1, 1 - r2
            u = a[0] + r1 * (b[0] - a[0]) + r2 * (c[0] - a[0])
            v = a[1] + r1 * (b[1] - a[1]) + r2 * (c[1] - a[1])
            al = texlib.texel(b01, w, h, u, v)[3]
            per[k][0] += 1
            per[k][1] += al < TRANSPARENT
            per[k][2] += al >= DRAWN
    wsum = lambda j: sum(areas[k] * per[k][j] / per[k][0] for k in range(len(tris))) / tot
    return per, areas, wsum(1), wsum(2)


def texel_density(P, UV, tris, size=2048):
    """Median texels per cm over the triangles (sqrt of UV area in texels / 3D area in cm^2)."""
    d = []
    for t in tris:
        a3 = tri_area(*(P[i] for i in t)) * 1e4
        if a3 > 1e-4:
            d.append(math.sqrt(uv_area(*(UV[i] for i in t)) * size * size / a3))
    d.sort()
    return d[len(d) // 2] if d else 0.0


_GAME = {}


def game_reference():
    """The game's own base hair (all 16 pieces, kit bones): braid lowest point and joint, drawn share, texel density."""
    if _GAME:
        return _GAME
    res = os.path.join(OUT, "P_AMICIA_unpacked", "resources")
    w, h, _, b01 = texlib.load(res, hairedit.B01)
    low, P, UV, T = 9.0, [], [], []
    for mid, lab, n, imp, col, _ in hairedit.hair_set("P_AMICIA", res):
        m, prims = load_mesh(os.path.join(res, f"{mid}.Mesh_Z"))
        for vs, tris, _, _ in prims:
            base = len(P)
            P += [v["p"] for v in vs]
            UV += [v["uv"] for v in vs]
            T += [(a + base, b + base, c + base) for a, b, c in tris]
            if m["vbs"][0]["stride"] == 48:
                low = min([low] + [v["p"][1] for v in vs if any(j in CHAIN and x > 0 for j, x in zip(v["j"], v["w"]))])
    _, _, tr, dr = alpha_stats(P, UV, T, b01, w, h, 20000)
    _GAME.update(low=low, transparent=tr, drawn=dr, density=texel_density(P, UV, T))
    return _GAME


def host_info(arch, res):
    hs = hairedit.hair_set(arch, res)
    mid, lab, mode = hairedit.pick_host(hs)
    m = parse(open(os.path.join(res, f"{mid}.Mesh_Z"), "rb").read())
    nv = sum(v["count"] for v in m["vbs"] if v["flags"] == 2)
    nt = sum(s["ntri"] for s in m["subs"])
    with open(os.path.join(paths.ORIG, f"{arch}.DPC"), "rb") as f, \
            mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as b:
        info = dpc.parse(b)
        e = next(e for e in info["blocks"][0]["resources"]["resources"] if dpc.s64(e["name"]) == mid)
        lz4 = bool(e["data"]["compression_type"])
    return dict(mid=mid, label=lab, mode=mode, vertices=nv, triangles=nt, lz4=lz4, pieces=len(hs))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("glb")
    ap.add_argument("--object", default="hair")
    ap.add_argument("--hair", action="store_true", help="(accepted for modtool check-model)")
    ap.add_argument("--json", default="", help="M17: also write the findings as JSON for the app")
    a = ap.parse_args()
    if a.json:
        __import__("atexit").register(write_json, a.json)
    nm, named = names()
    print(f"Checking hair {a.glb} (route A: UVs on the game's hair atlas B01)")
    try:
        g = gltfread.load(a.glb)
    except Exception as e:
        say("ERROR", f"the file cannot be read: {e}", "export again as glTF Binary (.glb) from Blender", code="read_failed")
        sys.exit(1)
    objs = g.mesh_nodes()
    print(f"\nFile: {len(objs)} mesh object(s): {', '.join(n for _, n in objs)}")
    if not g.doc.get("skins"):
        say("ERROR", "no armature / skin in the file", "select the armature too and export with Data > Skinning on", code="no_skin")
    hit = [ni for ni, n in objs if n == a.object]
    if not hit:
        say("ERROR", f"no mesh object named '{a.object}'",
            "name the hair object 'hair' (or give --object and the same \"object\" in the manifest's hair edit)", code="no_object")
        sys.exit(1)
    others = [n for _, n in objs if n != a.object]
    if others:
        say("WARNING", f"other objects in the file are ignored: {', '.join(others)}",
            "fine; the hair edit imports only the hair object (export Selected Objects to keep the file small)", code="other_objects")
    ni = hit[0]
    mesh = g.doc["meshes"][g.doc["nodes"][ni]["mesh"]]
    prims = mesh["primitives"]
    print(f"\n== object '{a.object}'")
    mats = {p.get("material") for p in prims}
    if len(mats) != 1:
        say("ERROR", f"{len(mats)} materials", "one material for the whole hair (the host's hair shader is used anyway)", code="many_materials")
    else:
        say("OK", "one material (its settings and textures are not used: the host keeps the game's hair shader and B01 / B02)")
    uvsets = {k for p in prims for k in p["attributes"] if k.startswith("TEXCOORD_")}
    if "TEXCOORD_0" not in uvsets:
        say("ERROR", "no UV map", "unwrap the hair onto B01_hair.png (hair kit)", code="no_uv")
    elif len(uvsets) > 1:
        say("WARNING", f"{len(uvsets)} UV maps: only the first is used", "delete the extra UV maps", code="many_uv_maps")
    else:
        say("OK", "one UV map")
    if any(p.get("mode", 4) != 4 for p in prims):
        say("ERROR", "not only triangles", "add a Triangulate modifier or Ctrl+T before export", code="not_triangles")
    look, kb = blendimport.bone_lookup()
    obj, errors, warns = blendimport.read_object(g, ni, look)
    if errors:
        for e in errors:
            say("ERROR", e, ("these vertices have no weights, so Blender added 'neutral_bone': weight them (Select > Select All by "
                             "Trait > Ungrouped Vertices) and export again" if "neutral_bone" in str(e) else "use the kit armature's bone names (hair kit); see notes\\body_model_guide.md, hair guide"), code="neutral_bone" if "neutral_bone" in str(e) else "unknown_bone")
        sys.exit(1)
    errs2 = []
    vs = blendimport.to_game(obj, kb, 48, [], errs2)
    if errs2:
        for e in sorted(set(errs2)):
            say("ERROR", e, "weight-paint the missing vertices (every vertex in at least one bone group)", code="unweighted_vertices")
        sys.exit(1)
    V, tris = obj["V"], obj["tris"]
    nv, nt = len(vs), len(tris)
    P = [v["p"] for v in vs]
    UV = [v["uv"] for v in vs]
    if nv > mergemesh.MAX_VERTS:
        say("ERROR", f"{nv} vertices (limit {mergemesh.MAX_VERTS})", "reduce the hair (the game's whole hair has about 26,000)", code="too_many_vertices")
    else:
        say("OK", f"{nv} vertices of {mergemesh.MAX_VERTS}, {nt} triangles (the game's whole hair: about 26,000 vertices)")
    many = sum(1 for v in V if len(v["infl"]) > 4)
    say("WARNING" if many else "OK", f"{many} vertices with more than 4 bone influences (the 4 largest are kept)" if many else
        "at most 4 bone influences per vertex", "Weights > Limit Total (limit 4) before export", code="many_influences")

    against = 0
    for t in tris:
        p0, p1, p2 = (P[i] for i in t)
        e1 = [p1[k] - p0[k] for k in range(3)]
        e2 = [p2[k] - p0[k] for k in range(3)]
        cr = [e1[1] * e2[2] - e1[2] * e2[1], e1[2] * e2[0] - e1[0] * e2[2], e1[0] * e2[1] - e1[1] * e2[0]]
        nsum = [sum(vs[i]["n"][k] for i in t) for k in range(3)]
        against += sum(cr[k] * nsum[k] for k in range(3)) < 0
    say("WARNING" if against > 0.01 * nt else "OK", f"{against} triangles face against their normals" + (
        " (flipped cards: lit from the wrong side, and maybe not drawn if the shader culls back faces)" if against > 0.01 * nt else ""),
        "Mesh > Normals > Recalculate Outside on those cards (Face Orientation overlay: blue = front)", code="flipped_faces")

    # ---- bones
    print("\n== bones")
    used = {}
    for v in V:
        tot = sum(v["infl"].values()) or 1
        for b, w in v["infl"].items():
            c = used.setdefault(b, [0, 0.0])
            c[0] += 1
            c[1] += w / tot
    for b in sorted(used, key=lambda b: -used[b][1]):
        k = kind(kb, named, b)
        line = f"{nm(b)} ({b}): {used[b][0]} vertices, {100 * used[b][1] / nv:.1f} % of the weight, {k}"
        if k in ("safe", "chain"):
            say("OK", line)
        else:
            say("WARNING", line, {"body bone": "the hair would follow this body part; use head / neck / spine or hair1-5",
                                  }.get(k, "avoid: weight these vertices to head (or hair1-5 to swing)"), code="hair_body_bone")
    far = 0
    dmax = 0.0
    for v in vs:
        main_ = v["bones"][max(range(4), key=lambda i: v["weights"][i])]
        d = math.dist(v["p"], skel.world_pos(kb[main_]))
        dmax = max(dmax, d)
        far += d > 0.45
    say("WARNING" if far else "OK", f"{far} vertices more than 0.45 m from their main bone (largest {dmax:.2f} m)" if far else
        f"every vertex near its main bone (largest distance {dmax:.2f} m)", "check those weights", code="far_from_bone")

    # ---- chain: tips and roots
    print("\n== chain (hair1-hair5): tips and roots")
    L = [level(v) for v in V]
    C = [k for k, v in enumerate(V) if chainw(v) > 0]
    ref = game_reference()
    jy = {CHAIN[b]: skel.world_pos(kb[b])[1] for b in CHAIN}
    head_res = os.path.join(OUT, "P_AMICIA_unpacked", "resources")
    HP, HN = surface(head_res, FACE["P_AMICIA"])
    hg = Grid(HP, 0.02)
    if not C:
        say("OK", "no vertex on hair1-5: nothing swings (fine for short hair; the whole hair is rigid on the head)")
    else:
        say("OK", f"{len(C)} vertices swing (on hair1-5); chain levels used: "
            + ", ".join(f"hair{i} {sum(1 for k in C if round(L[k]) == i)}" for i in range(1, 6)))
        # static tip: head/neck-only vertices next to the swinging end
        end = [k for k in C if L[k] >= 3.5]
        eg = Grid([P[k] for k in end], 0.03)
        stat = [k for k, v in enumerate(V) if chainw(v) == 0 and P[k][1] < jy[4] and end
                and eg.nearest(P[k])[0] < 0.03]
        if stat:
            say("WARNING", f"{len(stat)} vertices next to the swinging end (within 3 cm, below hair4) are on head / neck only: "
                "a static tip (it stays behind when the end swings)",
                "weight the tip to hair5 (or hair4 + hair5) like the rest of the end", code="hair_end_static")
        else:
            say("OK", "no static tip: everything next to the swinging end swings with it")
        # lag: low vertices that mostly follow upper bones
        def expected(y):
            e = 0
            for i in range(1, 6):
                if y <= jy[i]:
                    e = i
            return e
        lag = [k for k in C if expected(P[k][1]) - L[k] > 1.5]
        if len(lag) > 0.05 * len(C):
            say("WARNING", f"{len(lag)} swinging vertices ({100 * len(lag) / len(C):.0f} %) lie below a joint but mostly follow the "
                "bones above it: that part swings stiffly and stretches", "move their weight down the chain (see the hair guide)", code="hair_lag")
        else:
            say("OK", f"weights follow the chain down ({len(lag)} vertices lag by more than 1.5 levels)")
        # roots on the scalp carrying chain weight
        scalp = [k for k in C if chainw(V[k]) > 0.25 and hg.nearest(P[k])[0] < 0.01]
        if len(scalp) > 0.02 * len(C):
            say("WARNING", f"{len(scalp)} vertices within 1 cm of the scalp carry more than 25 % chain weight: the roots lift off "
                "the head when it swings", "keep the roots on head; let hair1 take over only where the hair leaves the head", code="hair_scalp_lift")
        else:
            say("OK", f"roots stay on the head ({len(scalp)} scalp vertices carry more than 25 % chain weight)")
        # tears: coincident vertices with different weights
        pg = Grid(P, 0.001)
        tear = set()
        for k in C:
            for o in pg.near(P[k]):
                if o > k and math.dist(P[k], P[o]) < 1e-5 and abs(L[k] - L[o]) > 0.05:
                    tear.add(k)
        if tear:
            say("WARNING", f"{len(tear)} vertices sit on another vertex with different chain weights: the hair splits open there "
                "when it moves", "give split vertices (UV seams, joined pieces) the same weights (Merge by Distance first)", code="hair_tear")
        else:
            say("OK", "no split vertices with different weights (no tears)")
        low = min(P[k][1] for k in C)
        below = jy[5] - low
        line = (f"the swinging part reaches down to y {low:.2f} m, {100 * below:.0f} cm below hair5's joint (that part follows "
                f"hair5 rigidly); the game braid reaches y {ref['low']:.2f} m")
        if below > 0.25:
            say("WARNING", line, "a long end stays straight like a stick: shorter, or accept it (no bones can be added)", code="hair_stick")
        else:
            say("OK", line)

    # ---- UVs and alpha on B01
    print("\n== UVs on the hair atlas B01 (route A)")
    out = sum(1 for u in UV if not (-1e-3 <= u[0] <= 1.001 and -1e-3 <= u[1] <= 1.001))
    say("OK", f"{out} vertices have UVs outside 0..1: B01 repeats there (the game's own hair does this on about 40 % of its "
        "vertices, along strips that tile; check in Blender with the image set to Repeat that the right strip shows)")
    zero = sum(1 for t in tris if uv_area(*(UV[i] for i in t)) < 1e-9 and tri_area(*(P[i] for i in t)) > 1e-6)
    say("WARNING" if zero > 0.001 * nt else "OK", f"{zero} triangles with zero UV area (one texel stretched over the triangle; "
        "the game's hair has 3)", "unwrap those triangles onto a strand strip", code="zero_uv_area")
    w, h, _, b01 = texlib.load(head_res, hairedit.B01)
    per, areas, tr, dr = alpha_stats(P, UV, tris, b01, w, h)
    line = (f"surface on B01: {100 * dr:.0f} % drawn (alpha >= {DRAWN}), {100 * tr:.0f} % transparent (alpha < {TRANSPARENT}); "
            f"the game's hair: {100 * ref['drawn']:.0f} % / {100 * ref['transparent']:.0f} %")
    if dr < 0.5 * ref["drawn"]:
        say("WARNING", line, "much of your cards falls on empty atlas area: move the UVs onto strand strips (B01_hair_uv.png)", code="hair_atlas_empty")
    elif tr < 0.25 * ref["transparent"]:
        say("WARNING", line, "almost no see-through edge: the cards look like solid sheets; let the strand tips (alpha fade) "
            "reach the card edges", code="hair_no_alpha_edge")
    else:
        say("OK", line)
    empty = 0
    for isl in islands(tris, nv):
        s = sum(per[k][0] for k in isl)
        if s >= 8 and sum(per[k][2] for k in isl) < 0.02 * s:
            empty += 1
    say("WARNING" if empty else "OK", f"{empty} cards (connected pieces) are almost fully transparent: invisible in game" if empty
        else "every card draws something", "move those cards' UVs onto a strand strip, or delete them", code="hair_cards_empty")
    dens = texel_density(P, UV, tris)
    line = f"texel density {dens:.0f} texels per cm (median); the game's hair {ref['density']:.0f}"
    say("WARNING" if dens < 0.3 * ref["density"] else "OK", line, "the strands look blurry: scale the UV islands up on B01", code="hair_blurry")

    # ---- per archive: host, budget, bones, clearance
    print("\n== per archive (the same hair goes into every archive)")
    for arch, chap in ARCH:
        res = os.path.join(OUT, f"{arch}_unpacked", "resources")
        print(f" -- {arch} (chapter {chap})")
        try:
            hi = host_info(arch, res)
        except ValueError as e:
            say("ERROR", str(e), "report this: the archive's hair set changed", code="hair_set_changed")
            continue
        body = 20 + 4 + 12 + 48 * nv + 4 + 8 + 6 * 3 * nt + 4 + 32 + 28
        stored = 48 + 222 + (int(body * LZ4_RATIO) if hi["lz4"] else body)
        grow = nv > hi["vertices"] or nt > hi["triangles"]
        say("ERROR" if stored > growdpc.LARGEST_SEEN else "OK",
            f"host {hi['label']} ({hi['vertices']} vertices now, {'LZ4' if hi['lz4'] else 'raw'}); estimated stored size {stored} "
            f"bytes; {'the slot will be enlarged (proven in game)' if grow else 'fits without enlarging'}; "
            f"{hi['pieces'] - 1} other hair pieces hidden"
            + ("; texture: B01 / B02 copied into 1-00 / 1-10 (chapter I)" if hi["mode"] == "own" else
               "; texture: B01 (follows the outfit option's hair color)"), "use fewer vertices", code="too_large")
        look_nf, _ = blendimport.bone_lookup(res, False)
        look_fb, ab = blendimport.bone_lookup(res, True)
        kit_of = {b["name"]: b["index"] for b in kb}
        miss = []
        for n in sorted(obj["weighted"]):
            if look_nf.get(n) is None:
                to = look_fb.get(n)
                miss.append(f"{n} -> {nm(kit_of[ab[to]['name']]) if to is not None else 'NONE'}")
        chain_missing = [m for m in miss if m.startswith("hair")]
        if miss and len(chain_missing) == len(miss) and C:
            say("OK", f"no hair1-5 here: {', '.join(miss)} (rigid on the head, no swing; expected)")
        elif miss:
            say("WARNING", f"bones missing here, they fall back to their parent: {', '.join(miss)}",
                "use bones that exist in every archive (hair guide)", code="bones_missing")
        else:
            say("OK", "every weighted bone exists here")
        # clearance: inside the head
        fp, fn = surface(res, FACE[arch])
        fg = Grid(fp, 0.02)
        inside, gap = 0, 9.0
        for p in P:
            d, k = fg.nearest(p)
            if k is None:
                continue
            gap = min(gap, d)
            if d < 0.04 and sum((p[i] - fp[k][i]) * fn[k][i] for i in range(3)) < -0.002:
                inside += 1
        line = (f"{inside} hair vertices ({100 * inside / nv:.1f} %) lie more than 2 mm under this archive's skin (the game's own "
                "hair: 2.1 %, roots tucked into the scalp)")
        if inside > 0.05 * nv:
            say("WARNING", line, "skin may show through: move the hair out a little (check against head_game_M37 in the kit)", code="hair_skin_through")
        else:
            say("OK", line)
        for lab, what in CLEAR.get(arch, []):
            mid = next(i for i, l in hairedit.labels(arch).items() if l == lab)
            cp, _ = surface(res, mid)
            cg = Grid(cp, 0.02)
            close = sum(1 for p in P if cg.nearest(p)[0] < 0.01)
            line = f"{close} vertices within 1 cm of the {what} ({lab}): they may clip into it when she moves"
            if close > 0.05 * nv:
                say("WARNING", line, "keep more room (the hair's collision radius is only 1-3 cm), or accept some clipping", code="hair_collision")
            else:
                say("OK", line)
    print(f"\nSummary: {COUNTS['ERROR']} error(s), {COUNTS['WARNING']} warning(s), {COUNTS['OK']} OK"
          + ("" if COUNTS["ERROR"] else ". Ready to build (modtool.py build <test name> with output\\mod\\hair.json)."))
    print("Certain: counts, hosts, bones per archive, UVs. Likely: the alpha shares (the game's cutoff is unknown), the clearance "
          "(bind pose only, no animation).")
    sys.exit(1 if COUNTS["ERROR"] else 0)


if __name__ == "__main__":
    main()
