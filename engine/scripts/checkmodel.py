"""Check a Blender export of a body model before building (M14b). Builds nothing.

Per object and for the whole file: counts against the limits, estimated stored size and slot enlargement, materials /
UV maps, UVs outside 0..1 and overlapping islands, textures (size, format, resampling), bones (unknown names, > 4
influences, unweighted vertices, distance from the main bone), back-facing triangles, loose / degenerate geometry, the
cloth-bone lever warning, seams between parts, kept pieces whose texels a part would cover, and host conflicts.
Every finding is OK, WARNING or ERROR with one line of advice. Exit code 1 if there is an ERROR.
Usage: python -I checkmodel.py <model.glb> [--parts object=host,object=host] [--keep M12,M13] [--skin obj,obj] [--base-only]
       (without --parts the hosts are chosen like the full_body edit does: list-hosts' recommendations for cloth)
       M16: afterwards the same file is checked against EVERY archive (bodycheck.py: hosts and packing per archive,
       bones that fall back, cloth bones, what stays visible). --keep takes labels or roles (necklace, hood_buttons, ...);
       --skin names the skin parts (default: objects whose name contains "skin"); --base-only skips the archives;
       --part-archives forearms=chp1 limits a part to some archives (as "archives" on a part in the manifest);
       (M17: no --forearms: the game's forearm skin is never added; chapter I's arm coverage is a warning);
       --shares cloth=0.8,skin=0.2 as "share" on parts (a packed host's split of its texels).
       --replace-head / --replace-hands [--keep-game-eyes]: M17 full body swap (the game's head / hands hidden).
       python -I checkmodel.py <hair.glb> --hair [--object hair]     hair mode (M15): haircheck.py, every archive's hair host
"""
import argparse
import json
import math
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import blendimport  # noqa: E402
import gltfread  # noqa: E402
import growdpc  # noqa: E402
import hosts  # noqa: E402
import mergemesh  # noqa: E402
import skel  # noqa: E402
import uvraster  # noqa: E402

CHAIN = range(306, 332)      # cloth chain bones (UserDefine DYNAMICON; notes\NOTES.md session 21)
LZ4_RATIO = 0.75             # estimate of LZ4 stored / decompressed for a mesh body (M14 merged mesh: 0.67)
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


def image_info(g, tex_ref):
    """(width, height, 'PNG'|'JPEG'|...) of a material texture, or None."""
    if not tex_ref:
        return None
    im = g.doc["images"][g.doc["textures"][tex_ref["index"]]["source"]]
    if "bufferView" in im:
        bv = g.doc["bufferViews"][im["bufferView"]]
        data = g.bins[bv.get("buffer", 0)][bv.get("byteOffset", 0):bv.get("byteOffset", 0) + bv["byteLength"]]
    else:
        return (0, 0, "external file " + im.get("uri", "?"))
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        w, h = struct.unpack(">II", data[16:24])
        return (w, h, "PNG")
    if data[:2] == b"\xff\xd8":
        return (0, 0, "JPEG")
    return (0, 0, "unknown")


def main():
    if "--hair" in sys.argv[1:]:
        import haircheck
        return haircheck.main()
    ap = argparse.ArgumentParser()
    ap.add_argument("glb")
    ap.add_argument("--parts", default="")
    ap.add_argument("--keep", default="")
    ap.add_argument("--skin", default=None)
    ap.add_argument("--base-only", action="store_true")
    ap.add_argument("--part-archives", default="", help="object=chp1+chp2,...: parts used only in those archives")
    ap.add_argument("--shares", default="", help="object=weight,...: a packed host's split of its texels")
    ap.add_argument("--replace-head", action="store_true", help="M17: the game's head, mouth, eyes and lashes are hidden")
    ap.add_argument("--replace-hands", action="store_true", help="M17: the game's hands are hidden")
    ap.add_argument("--keep-game-eyes", action="store_true", help="M17: with --replace-head, keep the game's eyes and lashes")
    ap.add_argument("--json", default="", help="M17: also write the findings as JSON for the app")
    ap.add_argument("--per-archive", default="", help="M17: a JSON file with the full_body edit's \"per_archive\" (hosts)")
    a = ap.parse_args()
    if a.json:
        __import__("atexit").register(write_json, a.json)
    import bodyroute
    keep_roles = [x for x in a.keep.split(",") if x]
    keep = sorted({x for x in keep_roles if x.startswith("M")} | set(bodyroute.resolve_keep("P_AMICIA", keep_roles)))
    want = dict(x.split("=") for x in a.parts.split(",") if x)
    print(f"Checking {a.glb}")
    try:
        g = gltfread.load(a.glb)
    except Exception as e:
        say("ERROR", f"the file cannot be read: {e}", "export again as glTF Binary (.glb) from Blender", code="read_failed")
        sys.exit(1)
    objs = g.mesh_nodes()
    look, bones = blendimport.bone_lookup()
    table = {r["label"]: r for r in hosts.table()}
    rec = hosts.recommend("cloth")
    taken, parts = {}, []
    print(f"\nFile: {len(objs)} mesh object(s): {', '.join(n for _, n in objs)}")
    if not g.doc.get("skins"):
        say("ERROR", "no armature / skin in the file", "select the armature too and export with Data > Skinning on", code="no_skin")
    for ni, name in objs:
        print(f"\n== object '{name}'")
        node = g.doc["nodes"][ni]
        mesh = g.doc["meshes"][node["mesh"]]
        prims = mesh["primitives"]
        mats = {p.get("material") for p in prims}
        uvsets = {k for p in prims for k in p["attributes"] if k.startswith("TEXCOORD_")}
        # host
        host = want.get(name)
        if host is None:
            host = next((h for h in rec if h not in taken.values() and not any(
                set(table[h]["replaces"]) & set(table[t]["replaces"]) for t in taken.values())), None)
            how = "chosen automatically (list-hosts: best free cloth host)"
        else:
            how = "given"
        if host not in table or not table[host]["importable"]:
            say("ERROR", f"host {host}: not an importable slot", "pick a host from: modtool.py list-hosts", code="host_not_importable")
            continue
        clash = [o for o, t in taken.items() if set(table[t]["replaces"]) & set(table[host]["replaces"])]
        if clash:
            say("ERROR", f"host {host} shares its textures with the host of '{clash[0]}' ({taken[clash[0]]})",
                "give each part a host with its own textures (modtool.py list-hosts)", code="host_shared_textures")
        taken[name] = host
        row = table[host]
        say("OK", f"host {host} ({row['piece']}, {row['material']['cls']} material, {row['storage']}) {how}")
        # materials and UV maps
        if len(mats) != 1:
            say("ERROR", f"{len(mats)} materials on this object", "one material per object: split it, or bake into one texture set", code="many_materials")
        else:
            say("OK", "one material")
        if "TEXCOORD_0" not in uvsets:
            say("ERROR", "no UV map", "unwrap the object (one UV map)", code="no_uv")
        elif len(uvsets) > 1:
            say("WARNING", f"{len(uvsets)} UV maps: only the first is used", "delete the extra UV maps (Object Data > UV Maps)", code="many_uv_maps")
        else:
            say("OK", "one UV map")
        if any(p.get("mode", 4) != 4 for p in prims):
            say("ERROR", "not only triangles", "add a Triangulate modifier or Ctrl+T before export", code="not_triangles")
        # geometry and bones (same reader as blendimport)
        obj, errors, warns = blendimport.read_object(g, ni, look)
        if errors:
            for e in errors:
                say("ERROR", e, ("these vertices have no weights, so Blender added 'neutral_bone': weight them (Select > Select All by "
                                 "Trait > Ungrouped Vertices) and export again" if "neutral_bone" in str(e) else "use the kit armature's bone names (output\\blender_kit); see notes\\body_model_guide.md"), code="neutral_bone" if "neutral_bone" in str(e) else "unknown_bone")
            continue
        errs2 = []
        vs = blendimport.to_game(obj, bones, 48, [], errs2)
        if errs2:
            for e in sorted(set(errs2)):
                say("ERROR", e, "weight-paint the missing vertices (every vertex in at least one bone group)", code="unweighted_vertices")
            continue
        nv, tris = len(vs), obj["tris"]
        nt = len(tris)
        if nv > mergemesh.MAX_VERTS:
            say("ERROR", f"{nv} vertices (limit {mergemesh.MAX_VERTS} per mesh)", "reduce the mesh or split it into two parts", code="too_many_vertices")
        else:
            say("OK", f"{nv} vertices of {mergemesh.MAX_VERTS}, {nt} triangles (after export: UV seams and hard edges split vertices)")
        body = 20 + 4 + 12 + 48 * nv + 4 + 8 + 6 * 3 * nt + 4 + 32 + 28
        stored = 48 + 222 + (int(body * LZ4_RATIO) if row["storage"] == "lz4" else body)
        if stored > growdpc.LARGEST_SEEN:
            say("ERROR", f"estimated stored size {stored} bytes > limit {growdpc.LARGEST_SEEN}", "use fewer triangles / vertices", code="too_large")
        else:
            grow = nv > row["vertices"] or nt > row["triangles"]
            say("OK", f"estimated stored size {stored} bytes ({row['storage']}; limit {growdpc.LARGEST_SEEN})"
                + ("; the slot will be enlarged (proven in game)" if grow else "; fits the slot without enlarging"))
        many = sum(1 for v in obj["V"] if len(v["infl"]) > 4)
        if many:
            say("WARNING", f"{many} vertices with more than 4 bone influences (the 4 largest are kept)",
                "Weights > Limit Total (limit 4) before export", code="many_influences")
        else:
            say("OK", "at most 4 bone influences per vertex")
        dmax, far = 0.0, 0
        for v in vs:
            main = v["bones"][max(range(4), key=lambda i: v["weights"][i])]
            d = math.dist(v["p"], skel.world_pos(bones[main]))
            dmax = max(dmax, d)
            far += d > 0.45
        if far:
            say("WARNING", f"{far} vertices more than 0.45 m from their main bone (largest {dmax:.2f} m)",
                "check those weights: a vertex should mostly follow a nearby bone", code="far_from_bone")
        else:
            say("OK", f"every vertex near its main bone (largest distance {dmax:.2f} m)")
        # cloth chains: vertices above the chain root that carry chain weight move like levers
        root_of = {}
        for b in CHAIN:
            r = b
            while bones[r]["parent"] in CHAIN:
                r = bones[r]["parent"]
            root_of[b] = r
        lev, worst = 0, 0.0
        for v in vs:
            for b, w in zip(v["bones"], v["weights"]):
                if w and b in root_of:
                    rp = skel.world_pos(bones[root_of[b]])
                    if v["p"][1] > rp[1] + 0.02:
                        lev += 1
                        worst = max(worst, w / 65535 * math.dist(v["p"], rp))
        if lev and worst > 0.03:
            say("WARNING", f"{lev} vertices above a cloth-chain root carry chain weight (swing up to {100 * worst:.1f} cm)",
                "give vertices above the hips little or no weight on the cloth bones (306-331)", code="chain_lever")
        else:
            say("OK", "cloth bones: no vertex far above a chain root carries chain weight" if not lev else
                f"cloth bones: {lev} small levers above a chain root (swing <= {100 * worst:.1f} cm)")
        # UVs
        uv = [v["uv"] for v in vs]
        out = sum(1 for u in uv if not (-1e-3 <= u[0] <= 1.001 and -1e-3 <= u[1] <= 1.001))
        if out:
            say("WARNING", f"{out} vertices have UVs outside 0..1 (the texture repeats there)", "keep the whole UV map inside the square", code="uv_outside")
        else:
            say("OK", "UVs inside 0..1")
        G = 512
        owner = {}
        over = 0
        for isl, (ts, _) in enumerate(uvraster.islands(uv, [t for t in tris])):
            cells = set()

            def emit(x, y, *_):
                cells.add((x % G, y % G))
            for k in ts:
                uvraster.raster([(uv[i][0] * G, uv[i][1] * G) for i in tris[k]], G, G, emit)
            for c in cells:
                if c in owner and owner[c] != isl:
                    over += 1
                owner[c] = isl
        if over:
            say("WARNING", f"UV islands overlap ({100 * over / max(1, len(owner)):.1f} % of the used area)",
                "fine for mirrored copies that should look the same; otherwise separate the islands", code="uv_overlap")
        else:
            say("OK", "no overlapping UV islands")
        if keep:
            import hosttex
            spec = hosttex.host_spec(host)
            pct = hosttex.overlap_reserved(spec, keep, uv, [tuple(t) for t in tris])
            if pct > 1.0:
                say("ERROR", f"{pct:.1f} % of this part's UVs fall on the texture area kept for {', '.join(keep)} in {host}",
                    "move those UV islands away, or do not keep that piece, or use another host", code="kept_area_conflict")
            else:
                say("OK", f"no conflict with the kept pieces' texture area ({pct:.1f} %)")
        # winding / normals, degenerate and loose geometry
        against = degen = 0
        seen, dup = set(), 0
        for t in tris:
            p0, p1, p2 = (vs[i]["p"] for i in t)
            e1 = [p1[k] - p0[k] for k in range(3)]
            e2 = [p2[k] - p0[k] for k in range(3)]
            cr = [e1[1] * e2[2] - e1[2] * e2[1], e1[2] * e2[0] - e1[0] * e2[2], e1[0] * e2[1] - e1[1] * e2[0]]
            if math.sqrt(sum(c * c for c in cr)) < 1e-10:
                degen += 1
            nsum = [sum(vs[i]["n"][k] for i in t) for k in range(3)]
            against += sum(cr[k] * nsum[k] for k in range(3)) < 0
            key = tuple(sorted(t))
            dup += key in seen
            seen.add(key)
        if against > 0.01 * max(1, nt):
            say("WARNING", f"{against} triangles face against their normals (flipped faces?)",
                "Mesh > Normals > Recalculate Outside, and check Face Orientation (blue = outside)", code="flipped_faces")
        else:
            say("OK", f"normals and winding agree ({against} exceptions)")
        used = {i for t in tris for i in t}
        loose = nv - len(used)
        if loose or degen or dup:
            say("WARNING", f"{loose} loose vertices, {degen} zero-area triangles, {dup} duplicate triangles",
                "Mesh > Clean Up > Delete Loose and Degenerate Dissolve; Merge by Distance", code="loose_geometry")
        else:
            say("OK", "no loose vertices, zero-area or duplicate triangles")
        # textures
        mat = g.doc["materials"][list(mats)[0]] if len(mats) == 1 and None not in mats else {}
        pbr = mat.get("pbrMetallicRoughness", {})
        spec = __import__("hosttex").host_spec(host)
        for what, ref, role in (("base color", pbr.get("baseColorTexture"), "colour"), ("normal map", mat.get("normalTexture"), "normal"),
                                ("metallic-roughness", pbr.get("metallicRoughnessTexture"), "normal")):
            info = image_info(g, ref)
            tw, th = spec[role]["w"], spec[role]["h"]
            if info is None:
                say("WARNING", f"no {what} texture: a default is used", "connect an Image Texture to that input of the Principled BSDF", code="no_texture")
            elif info[2] != "PNG":
                say("ERROR", f"{what} is {info[2]}", "set the image to PNG and export with Images: Automatic or PNG, embedded", code="texture_not_png")
            else:
                note = "same size" if (info[0], info[1]) == (tw, th) else (
                    f"resampled to {tw}x{th} (" + ("softer: smaller than the slot" if info[0] < tw else "reduced") + ")")
                say("OK" if info[0] >= tw or info[0] == tw else "WARNING", f"{what}: {info[0]}x{info[1]} PNG -> {note}",
                    f"use {tw}x{th} for full sharpness", code="texture_size")
        if mat.get("alphaMode", "OPAQUE") != "OPAQUE":
            say("WARNING", f"alpha mode {mat['alphaMode']}: ignored (the host is opaque)", "set Blend Mode to Opaque", code="alpha_ignored")
        parts.append(dict(name=name, host=host, P=[v["p"] for v in vs], tris=tris))
    # seams between parts
    if len(parts) > 1:
        print("\n== seams between parts")
        for i in range(len(parts)):
            for j in range(i + 1, len(parts)):
                A, B = parts[i], parts[j]

                def boundary(p):
                    cnt = {}
                    for t in p["tris"]:
                        for e in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0])):
                            k = tuple(sorted(e))
                            cnt[k] = cnt.get(k, 0) + 1
                    return {v for e, c in cnt.items() if c == 1 for v in e}
                bb = boundary(B)
                grid = {}
                for k in range(len(B["P"])):
                    grid.setdefault(tuple(int(c // 0.01) for c in B["P"][k]), []).append(k)

                def nearest(p):
                    best = (9.0, None)
                    c = tuple(int(x // 0.01) for x in p)
                    for dx in (-1, 0, 1):
                        for dy in (-1, 0, 1):
                            for dz in (-1, 0, 1):
                                for k in grid.get((c[0] + dx, c[1] + dy, c[2] + dz), []):
                                    d = math.dist(p, B["P"][k])
                                    if d < best[0]:
                                        best = (d, k)
                    return best
                joined = gap = overlap = 0
                gmax = 0.0
                for v in boundary(A):
                    d, k = nearest(A["P"][v])
                    if k is None:
                        continue
                    if d < 0.0005:
                        joined += 1
                    elif d < 0.01 and k in bb:
                        gap += 1
                        gmax = max(gmax, d)
                    elif d < 0.002:
                        overlap += 1
                label = f"'{A['name']}' / '{B['name']}'"
                if gap:
                    say("WARNING", f"{label}: {gap} edge vertices 0.5-10 mm from the other part's edge (largest {1000 * gmax:.1f} mm): small gaps",
                        "snap the seam vertices together (Snap to Vertex) or let one part overlap the other a little", code="seam_gap")
                if overlap:
                    say("WARNING", f"{label}: {overlap} edge vertices lie on the other part's surface (overlap)",
                        "fine if one part is meant to cover the other; otherwise trim one of them", code="seam_overlap")
                if not gap and not overlap:
                    say("OK", f"{label}: {joined} seam vertices meet exactly, no gaps")
    if not a.base_only:
        import bodycheck
        skin = a.skin.split(",") if a.skin is not None else [n for _, n in objs if "skin" in n.lower()]
        print("\n== every archive (M16: the full_body edit with \"archives\": \"all\")")
        bodycheck.check(g, objs, {n: "skin" if n in skin else "cloth" for _, n in objs},
                        keep_roles if keep_roles else bodyroute.DEFAULT_KEEP, say,
                        {x.split("=")[0]: x.split("=")[1].split("+") for x in a.part_archives.split(",") if x},
                        {x.split("=")[0]: float(x.split("=")[1]) for x in a.shares.split(",") if x},
                        dict(head=a.replace_head, hands=a.replace_hands, keep_eyes=a.keep_game_eyes),
                        json.load(open(a.per_archive)) if a.per_archive else None)
    print(f"\nSummary: {COUNTS['ERROR']} error(s), {COUNTS['WARNING']} warning(s), {COUNTS['OK']} OK"
          + ("" if COUNTS["ERROR"] else ". Ready to build (modtool.py build <test name>)."))
    sys.exit(1 if COUNTS["ERROR"] else 0)


if __name__ == "__main__":
    main()
