"""check-model, M16 part: a body.glb against EVERY Amicia archive (called by checkmodel.py after the file checks).

Per archive (bodyroute.plan, the same choice as the full_body edit with "archives"):
  hosts and packing per part (M16c: a packed part's UV box gets its own cell, same scale along u and v), texel density
  (texels per metre) of every part, the host's texture sizes and storage,
  vertices per host against 65,535, the estimated stored size; skin parts without a skin host (shaded like cloth);
  bones the parts use that this archive's skeleton lacks: they fall back to their nearest existing parent (which one,
  how many vertices); cloth-chain bones (306-331) that fall back (that cloth will not swing here);
  what stays visible of the archive's own outfit (kept pieces) and which body pieces stay (face, hands, ...).
"""
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import blendimport  # noqa: E402
import bodyroute  # noqa: E402
import growdpc  # noqa: E402
import hosts  # noqa: E402

CHAIN = range(306, 332)
LZ4_RATIO = 0.75


def hands_start(arch):
    """Smallest |x| of the archive's hands mesh on the arms (bind pose: arms along x): where its skin begins."""
    import struct
    from meshfmt import parse
    o, b, x, _ = bodyroute.pieces(arch)
    _, ms = hosts.labels(arch)
    best = 9.0
    for lab, role in b.items():
        if role != "hands":
            continue
        raw = open(os.path.join(hosts.res_dir(arch), f"{ms[lab]}.Mesh_Z"), "rb").read()
        m = parse(raw)
        for vb in m["vbs"]:
            if vb["flags"] == 2:
                for i in range(vb["count"]):
                    ax = abs(struct.unpack_from("<f", raw, vb["pos"] + i * vb["stride"])[0])
                    if ax > 0.2:
                        best = min(best, ax)
    return best


def arm_points():
    """Points on the base hands M00's surface from the elbow region outwards (|x| >= ARM0, PER_CM2 per cm²): skin that
    must be covered."""
    import random
    import struct
    from meshfmt import parse
    _, ms = hosts.labels("P_AMICIA")
    raw = open(os.path.join(hosts.res_dir("P_AMICIA"), f"{ms['M00']}.Mesh_Z"), "rb").read()
    m = parse(raw)
    vb, ib = m["vbs"][0], m["ibs"][0]
    idx = struct.unpack_from(f"<{ib['count']}H", raw, ib["pos"])
    P = [struct.unpack_from("<3f", raw, vb["pos"] + i * 48) for i in range(vb["count"])]
    rnd = random.Random(16)
    out = []
    for t in range(sum(s["ntri"] for s in m["subs"])):
        a, b, c = (P[i] for i in idx[3 * t:3 * t + 3])
        if min(abs(a[0]), abs(b[0]), abs(c[0])) < ARM0:
            continue
        e1 = [b[i] - a[i] for i in range(3)]
        e2 = [c[i] - a[i] for i in range(3)]
        ar = 0.5 * ((e1[1] * e2[2] - e1[2] * e2[1]) ** 2 + (e1[2] * e2[0] - e1[0] * e2[2]) ** 2 + (e1[0] * e2[1] - e1[1] * e2[0]) ** 2) ** 0.5
        n = ar * 1e4 * PER_CM2
        for _ in range(int(n) + (rnd.random() < n - int(n))):
            r1, r2 = rnd.random(), rnd.random()
            if r1 + r2 > 1:
                r1, r2 = 1 - r1, 1 - r2
            out.append([a[i] + r1 * e1[i] + r2 * e2[i] for i in range(3)])
    return out


def own_hands_tris(arch):
    import struct
    from meshfmt import parse
    _, ms = hosts.labels(arch)
    _, b, _, _ = bodyroute.pieces(arch)
    out = []
    for lab, role in b.items():
        if role != "hands":
            continue
        raw = open(os.path.join(hosts.res_dir(arch), f"{ms[lab]}.Mesh_Z"), "rb").read()
        m = parse(raw)
        vb, ib = [v for v in m["vbs"] if v["flags"] == 2][0], m["ibs"][0]
        idx = struct.unpack_from(f"<{ib['count']}H", raw, ib["pos"])
        P = [struct.unpack_from("<3f", raw, vb["pos"] + i * vb["stride"]) for i in range(vb["count"])]
        out += [[P[i] for i in idx[3 * t:3 * t + 3]] for t in range(sum(s["ntri"] for s in m["subs"]))]
    return out


ARM0 = 0.30      # |x| from which the forearm skin shows with rolled-up sleeves (bind pose)
PER_CM2 = 4      # sample points per cm² of the base forearm skin
TOL = 0.004      # a part triangle (or the archive's own hands) closer than this covers a point (M16c: was 25 mm to the
                 # nearest vertex, which missed chapter I's bare ring before the wrist)


def pt_tri(p, a, b, c):
    def dot(u, v):
        return u[0] * v[0] + u[1] * v[1] + u[2] * v[2]
    ab = [b[i] - a[i] for i in range(3)]
    ac = [c[i] - a[i] for i in range(3)]
    ap = [p[i] - a[i] for i in range(3)]
    d1, d2 = dot(ab, ap), dot(ac, ap)
    if d1 <= 0 and d2 <= 0:
        return math.dist(p, a)
    bp = [p[i] - b[i] for i in range(3)]
    d3, d4 = dot(ab, bp), dot(ac, bp)
    if d3 >= 0 and d4 <= d3:
        return math.dist(p, b)
    vc = d1 * d4 - d3 * d2
    if vc <= 0 and d1 >= 0 and d3 <= 0:
        v = d1 / (d1 - d3)
        return math.dist(p, [a[i] + v * ab[i] for i in range(3)])
    cp = [p[i] - c[i] for i in range(3)]
    d5, d6 = dot(ab, cp), dot(ac, cp)
    if d6 >= 0 and d5 <= d6:
        return math.dist(p, c)
    vb = d5 * d2 - d1 * d6
    if vb <= 0 and d2 >= 0 and d6 <= 0:
        w = d2 / (d2 - d6)
        return math.dist(p, [a[i] + w * ac[i] for i in range(3)])
    va = d3 * d6 - d5 * d4
    if va <= 0 and (d4 - d3) >= 0 and (d5 - d6) >= 0:
        w = (d4 - d3) / ((d4 - d3) + (d5 - d6))
        return math.dist(p, [b[i] + w * (c[i] - b[i]) for i in range(3)])
    den = 1 / (va + vb + vc)
    v, w = vb * den, vc * den
    return math.dist(p, [a[i] + ab[i] * v + ac[i] * w for i in range(3)])


def check(g, objs, kinds, keep, say, part_archives=None, shares=None, replace=None, per_archive=None):
    """objs: [(node index, name)]; kinds: {name: cloth|skin}; keep: roles or labels; part_archives: {name: [archives]};
    shares: {name: weight} as an edit's per-archive "shares"; per_archive: the edit's "per_archive" (M17: the app's
    hosts per archive, plan §18)."""
    look, kb = blendimport.bone_lookup()
    parts, infl, pos, tris = [], {}, {}, {}
    for ni, name in objs:
        obj, errors, _ = blendimport.read_object(g, ni, look)
        if errors:
            continue
        vs = obj["V"]
        parts.append(dict(object=name, kind=kinds.get(name, "cloth"), vertices=len(vs), triangles=len(obj["tris"]),
                          archives=(part_archives or {}).get(name)))
        pos[name] = [v["p"] for v in vs]
        tris[name] = [[vs[i]["p"] for i in t] for t in obj["tris"]]
        ar = uva = 0.0   # surface area / UV area used: the build's default packing share
        for t in obj["tris"]:
            a0, a1, a2 = (vs[i]["p"] for i in t)
            e1 = [a1[q] - a0[q] for q in range(3)]
            e2 = [a2[q] - a0[q] for q in range(3)]
            ar += 0.5 * ((e1[1] * e2[2] - e1[2] * e2[1]) ** 2 + (e1[2] * e2[0] - e1[0] * e2[2]) ** 2 + (e1[0] * e2[1] - e1[1] * e2[0]) ** 2) ** 0.5
            u0, u1, u2 = (vs[i]["uv"] for i in t)
            uva += 0.5 * abs((u1[0] - u0[0]) * (u2[1] - u0[1]) - (u2[0] - u0[0]) * (u1[1] - u0[1]))
        parts[-1]["area"] = ar / max(0.02, min(1.0, uva))
        ts = bodyroute.part_texsize(g, ni)
        uvs = [v["uv"] for v in vs]
        parts[-1].update(texsize=list(ts), uvbox=bodyroute.uv_box(uvs, ts[0], ts[1]),
                         uv_outside=sum(1 for q in uvs if not (-1e-3 <= q[0] <= 1.001 and -1e-3 <= q[1] <= 1.001)))
        cnt = {}
        for v in vs:
            for b, w in v["infl"].items():
                if w > 0:
                    cnt[b] = cnt.get(b, 0) + 1
        infl[name] = cnt
    if not parts:
        return
    name_of = {i: n for n, i in look.items() if not n.startswith("bone_")}
    arm = arm_points()
    for arch in bodyroute.ARCHS:
        print(f"\n== {arch} (chapter {bodyroute.CHAPTER[arch]})")
        rows = hosts.table(arch)
        table = {r["label"]: r for r in rows}
        try:
            ov = dict(bodyroute.override_for(dict(per_archive=per_archive or {}), arch))
            if shares:
                ov["shares"] = shares
            pl = bodyroute.plan(arch, parts, keep, ov or None, rows, replace=replace)
        except bodyroute.PlanError as e:
            say("ERROR", str(e), "set a host for this archive in the edit's \"per_archive\" or split the parts differently", code="no_host")
            continue
        for gr in pl["groups"]:
            r = table[gr["host"]]
            nv = sum(p["vertices"] for p in parts if p["object"] in gr["objects"])
            nt = sum(p["triangles"] for p in parts if p["object"] in gr["objects"])
            tex = ", ".join(f"{t['role']} {t['size']} {t['storage']}" for t in r["textures"] if t["role"] in ("colour", "normal", "mask"))
            body = 20 + 4 + 12 + 48 * nv + 4 + 8 + 6 * 3 * nt + 4 + 32 + 28
            stored = 48 + 222 + (int(body * LZ4_RATIO) if r["storage"] == "lz4" else body)
            lvl = "ERROR" if nv > 65535 or stored > growdpc.LARGEST_SEEN else "OK"
            say(lvl, f"host {gr['host']} ({gr['class']}; {tex}): {' + '.join(gr['objects'])}, {nv} vertices of 65535, "
                f"about {stored} bytes stored{'; slot enlarged' if nv > r['vertices'] or nt > r['triangles'] else ''}",
                "too much in one host: fewer vertices, or give a part another host (\"per_archive\": {\"hosts\": ...})", code="host_fill")
            byp = {p["object"]: p for p in parts}
            W, H = (int(q) for q in gr["sizes"]["colour"].split("x"))
            if len(gr["objects"]) > 1:
                say("WARNING", f"{len(gr['objects'])} parts share {gr['host']} ({W}x{H}): " + ", ".join(
                    f"'{o}' {100 * sh:.0f} % = {c['px'][0]} x {c['px'][1]} texels, {bodyroute.cell_density(c, byp[o])[0]:.0f} texels/m"
                    for o, sh, c in zip(gr["objects"], gr["shares"], gr["cells"]))
                    + " (each part's UV box in its own cell, u and v scaled alike, at most 2:1 where a cell grew into free texels; shares: equal texel density per surface, "
                    "skin 1/4, or each part's \"share\")",
                    f"{bodyroute.CHAPTER[arch]}: this archive has too few hosts with their own textures. ONE object (one atlas "
                    "for the whole body) gets the whole host texture and the best sharpness here; with two parts give the cloth "
                    "most of the texels (\"share\": e.g. 0.8 / 0.2) and lay out the small part's UVs compactly", code="packed_parts")
                for o in gr["objects"]:
                    if byp[o].get("uv_outside"):
                        say("WARNING", f"'{o}': {byp[o]['uv_outside']} UVs outside 0..1: clamped in the packed host (no tiling)",
                            "keep a packed part's UVs inside 0..1", code="packed_uv_outside")
            else:
                o = gr["objects"][0]
                say("OK", f"'{o}' has the whole {W}x{H} of {gr['host']}: {bodyroute.texels_per_m(W, H, byp[o]):.0f} texels/m")
            for k, o in zip(gr["kinds"], gr["objects"]):
                if k == "skin" and gr["class"] != "skin-like":
                    say("WARNING", f"'{o}' is skin but {gr['host']} has a cloth material: it is shaded like cloth here",
                        "expected in archives without a free skin host", code="skin_as_cloth")
            if gr["texture_sharing_kept"]:
                say("OK", f"{gr['host']} shares its textures with kept {', '.join(gr['texture_sharing_kept'])}: their texels are kept"
                    + (" (check that your UVs avoid that area: the base check above)" if arch == "P_AMICIA" else ""))
        # bones
        if arch != "P_AMICIA":
            res = hosts.res_dir(arch)
            exact, _ = blendimport.bone_lookup(res, False)
            fall, ab = blendimport.bone_lookup(res, True)
            ab_name = {}
            for n, i in exact.items():
                if i is not None and (i not in ab_name or ab_name[i].startswith("bone_")):
                    ab_name[i] = n
            for p in parts:
                miss = {b: c for b, c in infl[p["object"]].items() if exact.get(f"bone_{b:03d}") is None}
                if not miss:
                    say("OK", f"'{p['object']}': every weighted bone exists here")
                    continue
                desc = ", ".join(f"{name_of.get(b, f'bone_{b:03d}')} ({c} v) -> {ab_name.get(fall.get(f'bone_{b:03d}'), '?')}"
                                 for b, c in sorted(miss.items(), key=lambda x: -x[1])[:8])
                chain = sum(c for b, c in miss.items() if b in CHAIN)
                say("WARNING", f"'{p['object']}': {len(miss)} bones missing in {arch}, they follow their parent: {desc}"
                    + (" ..." if len(miss) > 8 else ""),
                    "fine for small pieces; vertices on a missing bone move with its parent", code="bones_missing")
                if chain:
                    say("WARNING", f"'{p['object']}': {chain} vertices on cloth-chain bones missing here: that cloth does not swing in {arch}",
                        "expected where the archive's outfit has no such cloth", code="chain_missing")
        for x in pl["notes"]:
            say("OK", f"note: {x}")
        if pl["skipped"]:
            say("OK", f"not used in {arch} (their \"archives\"): {', '.join(pl['skipped'])}")
        # skin the archive's own body does not have: forearms (chapter I's hands start at the wrist). M17: the game's
        # forearm skin is never added; the model must cover the arm (a non-blocking warning)
        hs = hands_start(arch)
        gap = arm if hs > ARM0 else []
        if gap:
            grid, cell = {}, 0.02
            tri_sets = [t for p in parts if p["object"] not in pl["skipped"] for t in tris[p["object"]]] + own_hands_tris(arch)
            for t in tri_sets:
                lo = [min(v[i] for v in t) - TOL for i in range(3)]
                hi = [max(v[i] for v in t) + TOL for i in range(3)]
                if max(abs(lo[0]), abs(hi[0])) < ARM0 - 0.05:
                    continue
                for gx in range(int(lo[0] // cell), int(hi[0] // cell) + 1):
                    for gy in range(int(lo[1] // cell), int(hi[1] // cell) + 1):
                        for gz in range(int(lo[2] // cell), int(hi[2] // cell) + 1):
                            grid.setdefault((gx, gy, gz), []).append(t)

            def covered(pt):
                return any(pt_tri(pt, *t) < TOL for t in grid.get(tuple(int(x // cell) for x in pt), ()))
            bare = [pt for pt in gap if not covered(pt)]
            pct = 100 * len(bare) / len(gap)
            say("WARNING" if pct > 0.5 else "OK",
                f"forearms: {arch}'s own hands start at |x| {hs:.3f} (at the wrist; the base's at 0.241 include the forearms); "
                f"{pct:.1f} % of the base forearm skin from |x| {ARM0} on is covered by none of your parts nor its hands (bare: "
                f"no mesh at all)" + (f", at |x| {min(abs(q[0]) for q in bare):.3f}-{max(abs(q[0]) for q in bare):.3f}" if bare else ""),
                "in chapter I your model should include the forearms: model them (a skin part, or with \"archives\": [\""
                + bodyroute.SHORT[arch] + "\"] for this chapter only) or use long sleeves; the game's forearm skin is never added", code="forearms_bare")
        # what stays
        vis = pl["visible_outfit"]
        if vis:
            say("OK", "stays from the original outfit: " + ", ".join(f"{l} ({r})" for l, r in sorted(vis.items())),
                )
        else:
            say("OK", "nothing of the original outfit stays")
        if pl["replaced"]:   # M17 full body swap: the game's head / hands hidden, the skin part supplies them
            say("WARNING", "replaced by your skin part (the game's pieces hidden): " + ", ".join(
                f"{l} ({r})" for l, r in sorted(pl["replaced"].items())),
                "your skin must bring these: a head with eyes and teeth if the game's eyes / mouth are hidden, the hands; "
                "the game's lip-sync shapes are lost with its head", code="pieces_replaced")
        say("OK", f"hidden: {len(pl['hide']) - len(pl['replaced'])} outfit pieces; untouched: " + ", ".join(sorted(set(pl["body"].values()))
            + [f"hair ({len(pl['hair'])} pieces)"]))
