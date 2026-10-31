"""Positions-only patcher for the face meshes (M8): M37 face, M26 mouth, M28 eyes, M36 lashes.

Reads a Blender glTF export made from blender_kit\\face_kit\\face_kit.glb in which vertices were only MOVED, and writes
the new positions into the meshes of a DPC copy. Everything else stays byte for byte: UVs, bones, weights, colors,
indices, submeshes, the lip-sync block (8 shapes of per-vertex OFFSETS, see notes\\mesh_format.md) and its name table.
- Vertex matching: by the ID UV map of the kit (TEXCOORD_1: u = vertex index, v = mesh x 10 + part), so Blender may
  reorder, split or merge vertices. Without it (fallback): objects are matched by the Mxx in their name and vertices by
  UV + nearest original position; then the triangle check must pass completely.
- Positions: object (node) transforms are applied. Vertices that moved less than 1e-6 m keep their original bytes.
- Normals and tangents (safe update): the game's own normal and tangent of a vertex are rotated by the rotation of the
  area-weighted surface normal at that point (computed on the original and the new shape, vertices welded by original
  position so seams stay consistent). Unchanged areas keep their bytes; the handedness byte is kept.
- Bounds (link header): kept if every new position stays inside the stored sphere and box (rows of the 3x3 = box axes;
  geometry only, like the game's); otherwise the sphere radius / box half extents grow by the amount needed (same
  center and rotation).
- M17: exports never block because of splits, welds or order. Seam copies the exporter splits, vertices welded across a
  UV seam (a copy missing but its position present: it takes the position of a copy at the same original place) and
  any vertex order are fine. Only REAL geometry changes are reported, as warnings (report "added_vertices" /
  "removed_vertices", counted as distinct positions): an added vertex (an ID or UV no original vertex has) is ignored;
  a removed position (no vertex of the file at that original place) keeps its original position. Triangles are compared
  by original position (seams do not count); in fallback mode (no ID map) differences not explained by added or removed
  vertices beyond max(20, 2 %) of the triangles stop (the matching itself failed). Errors that still stop: non-finite
  values, nothing matched.
Usage:
  python -I facepatch.py <edit.glb|gltf> --dpc <in.DPC> --out <out.DPC> [--meshes M37,M26,M28,M36] [--work <dir>]
         [--report <json>] [--target M37=<mesh id>]   (the same mesh under another ID in a variant archive)
  python -I facepatch.py <edit.glb> --dry          (report only)
"""
import argparse
import collections
import json
import math
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paths  # noqa: E402
import dpc  # noqa: E402
import gltfread  # noqa: E402
from bmpdump import body  # noqa: E402
from meshfmt import parse  # noqa: E402
from patch_hide import find  # noqa: E402

OUT = paths.OUT
MESHES = {"M37": (4840249479922744736, 37), "M26": (360281120925488824, 26), "M28": (806504510931861565, 28),
          "M36": (4363979579306007351, 36)}
# M17 kits v2: objects are named YOUR_face, YOUR_mouth, ... (without the game label): names that identify a mesh
# when the file has no ID map
NAME_ALIASES = {"M37": ("your_head", "your_face"), "M26": ("your_mouth",), "M36": ("your_lashes",)}   # M17 v4: the head
# kit (YOUR_head; "your_face" from kits v2 still accepted); the eyes are REF_ (never changed)
MOVE_EPS = 1e-6
SPLIT_TOL = 1e-4
ROT_MIN = math.radians(0.25)


def sub(a, b):
    return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]


def cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def unit(v):
    n = math.sqrt(dot(v, v))
    return [x / n for x in v] if n > 1e-20 else [0.0, 0.0, 0.0]


def rotate(v, axis, ang):
    """Rodrigues rotation of v about unit axis."""
    c, s = math.cos(ang), math.sin(ang)
    k = cross(axis, v)
    d = dot(axis, v)
    return [v[i] * c + k[i] * s + axis[i] * d * (1 - c) for i in range(3)]


def snorm(v):
    return [max(-127, min(127, round(x * 127))) for x in v]


class GameMesh:
    """A Mesh_Z body (bff extract layout: 64-byte header + link + body) and its decoded geometry."""

    def __init__(self, data):
        self.b = bytearray(data)
        self.m = parse(bytes(data))
        self.geo = [v for v in self.m["vbs"] if v["flags"] == 2]
        self.morph = [v for v in self.m["vbs"] if v["flags"] == 258]
        self.P, self.N, self.T, self.UV = {}, {}, {}, {}
        for part, vb in enumerate(self.geo):
            if vb["stride"] != 48:
                raise ValueError("only stride-48 face meshes are supported")
            for i in range(vb["count"]):
                o = vb["pos"] + 48 * i
                self.P[(part, i)] = list(struct.unpack_from("<3f", data, o))
                self.T[(part, i)] = list(struct.unpack_from("<4b", data, o + 12))
                self.N[(part, i)] = list(struct.unpack_from("<4b", data, o + 16))
                self.UV[(part, i)] = list(struct.unpack_from("<2e", data, o + 20))
        idx = [struct.unpack_from(f"<{ib['count']}H", data, ib["pos"]) for ib in self.m["ibs"]]
        self.tris = []
        for k, s in enumerate(self.m["subs"]):
            part = k if len(self.m["ibs"]) > 1 else 0
            seg = idx[part if len(self.m["ibs"]) > 1 else 0][s["start"]:s["start"] + 3 * s["ntri"]]
            self.tris += [tuple((part, seg[t + j]) for j in range(3)) for t in range(0, len(seg), 3)]

    def morph_offsets(self):
        """{(0, i): [offsets of the 8 shapes]} (the lip-sync block belongs to the first geometry block)."""
        if not self.morph:
            return {}
        mb, n = self.morph[0], self.geo[0]["count"]
        k = mb["count"] // n
        return {(0, i): [struct.unpack_from("<3f", self.b, mb["pos"] + 20 * (s * n + i)) for s in range(k)] for i in range(n)}


def gltf_vertices(g, code_of, label, fallback_names):
    """[(key or None, position world, uv0)] and triangles as lists of vertex numbers, for the objects of one mesh."""
    verts, tris, used_ids = [], [], False
    for ni, name in g.mesh_nodes():
        node = g.doc["nodes"][ni]
        W = g.world(ni)
        for p in g.doc["meshes"][node["mesh"]]["primitives"]:
            at = p["attributes"]
            if p.get("mode", 4) != 4:
                continue
            ids = g.accessor(at["TEXCOORD_1"]) if "TEXCOORD_1" in at else None
            mine = []
            if ids:
                codes = {round(v[1]) for v in ids}
                if not codes & set(code_of):
                    continue
                used_ids = True
            elif label not in name and not any(a in name.lower() for a in NAME_ALIASES.get(label, ())):
                continue
            pos = g.accessor(at["POSITION"])
            uv = g.accessor(at["TEXCOORD_0"]) if "TEXCOORD_0" in at else [[0.0, 0.0]] * len(pos)
            base = len(verts)
            for k in range(len(pos)):
                key = None
                if ids:
                    u, v = ids[k]
                    if abs(u - round(u)) > 1e-3 or abs(v - round(v)) > 1e-3:
                        key = "added"   # a new vertex (subdivided / extruded): its ID UV was interpolated
                    else:
                        key = (code_of[round(v)], int(round(u))) if round(v) in code_of else "other"  # another mesh joined in
                verts.append((key, gltfread.apply(W, pos[k]), uv[k], name))
            idx = [x[0] for x in g.accessor(p["indices"])] if "indices" in p else list(range(len(pos)))
            tris += [tuple(base + idx[i + j] for j in range(3)) for i in range(0, len(idx) - 2, 3)]
    return verts, tris, used_ids


def match_fallback(verts, gm):
    """Keys for vertices without IDs: same UV (f16 grid) and nearest original position."""
    by_uv = collections.defaultdict(list)
    for key, uv in gm.UV.items():
        by_uv[(round(uv[0] * 2048), round(uv[1] * 2048))].append(key)
    groups = collections.defaultdict(list)
    for n, (key, p, uv, name) in enumerate(verts):
        u = (round(uv[0] * 2048), round(uv[1] * 2048))
        if u not in by_uv:
            continue   # a UV no original vertex has: an added vertex
        groups[u].append(n)
    keys = ["added"] * len(verts)
    for u, ns in groups.items():
        cand = by_uv[u]
        # one-to-one first (closest pairs first), so vertices sharing a UV are not all sent to one original;
        # copies left over (split vertices) go to their nearest original
        pairs = sorted((math.dist(gm.P[k], verts[n][1]), n, k) for n in ns for k in cand)
        free = set(cand)
        for d, n, k in pairs:
            if keys[n] == "added" and k in free:
                keys[n] = k
                free.discard(k)
        for n in ns:
            if keys[n] == "added":
                keys[n] = min(cand, key=lambda k: math.dist(gm.P[k], verts[n][1]))
    return [(keys[n],) + tuple(v[1:]) for n, v in enumerate(verts)]


def surface_normals(P, tris, weld):
    """Area-weighted normal per weld group (game winding: front faces have N . ((b-a) x (c-a)) < 0)."""
    acc = collections.defaultdict(lambda: [0.0, 0.0, 0.0])
    for t in tris:
        a, b, c = (P[k] for k in t)
        n = cross(sub(b, a), sub(c, a))
        for k in t:
            g = acc[weld[k]]
            g[0] -= n[0]
            g[1] -= n[1]
            g[2] -= n[2]
    return {g: unit(v) for g, v in acc.items()}


def bounds_update(b, pts_orig, pts_new):
    """Grow the link-header sphere/box if the new points reach further than the original ones. Returns a message."""
    o = 64 + 16
    v = list(struct.unpack_from("<20f", b, o))
    c, r = v[:3], v[3]
    M = [v[4:8], v[8:12], v[12:16]]
    half = v[16:19]

    def ext(pts):
        d = max(math.dist(p, c) for p in pts)
        loc = [[sum(M[i][j] * (p[j] - M[j][3]) for j in range(3)) for i in range(3)] for p in pts]  # rows = box axes
        return d, [max(abs(q[i]) for q in loc) for i in range(3)]
    d0, h0 = ext(pts_orig)
    d1, h1 = ext(pts_new)
    msg = []
    if d1 > max(r, d0) + 1e-7:
        v[3] = r + (d1 - max(r, d0)) + 1e-5
        msg.append(f"sphere radius {r:.4f} -> {v[3]:.4f}")
    grown = False
    for i in range(3):
        need = h1[i] - max(half[i], h0[i])
        if need > 1e-7:
            v[16 + i] = half[i] + need + 1e-5
            grown = True
    if grown:
        v[19] = math.sqrt(sum(x * x for x in v[16:19]))
        msg.append(f"box half extents {[round(x, 4) for x in half]} -> {[round(x, 4) for x in v[16:19]]}")
    struct.pack_into("<20f", b, o, *v)
    return "; ".join(msg) or "unchanged (inside the stored sphere and box)"


def patch_mesh(gm, verts, tris_g, used_ids, label):
    rep = dict(mesh=label, warnings=[])
    errors = []
    if not used_ids:
        rep["warnings"].append("no ID UV map (TEXCOORD_1) in the file: vertices matched by UV + nearest position")
        verts = match_fallback(verts, gm)
    keep = [i for i, v in enumerate(verts) if v[0] != "other"]
    if len(keep) < len(verts):
        re = {o: n for n, o in enumerate(keep)}
        tris_g = [tuple(re[i] for i in t) for t in tris_g if all(i in re for i in t)]
        verts = [verts[i] for i in keep]
    # original positions (weld groups): seams and splits do not count as geometry
    wpos = {k: tuple(round(x, 5) for x in gm.P[k]) for k in gm.P}
    gid = {}
    for k in sorted(gm.P):
        gid.setdefault(wpos[k], len(gid))
    G = {k: gid[wpos[k]] for k in gm.P}
    added = [v for v in verts if v[0] not in gm.P]
    new = collections.defaultdict(list)
    for key, p, uv, name in verts:
        if key not in gm.P:
            continue
        if not all(math.isfinite(x) for x in p):
            errors.append(f"non-finite position at {key}")
        new[key].append(p)
    if not new:
        raise ValueError(f"no vertex of the file matches {label}: export from the face kit")
    if added:
        n_add = len({tuple(round(x, 4) for x in v[1]) for v in added})
        rep["added_vertices"] = n_add
        rep["warnings"].append(f"added {n_add} vertices (new geometry): ignored, only moved vertices are used")
    # exact duplicates (same position and UV) cannot be told apart without IDs: a missing one takes its twin's position
    twin = {}
    for k in sorted(gm.P):
        twin.setdefault((tuple(gm.P[k]), tuple(gm.UV[k])), k)
    canon = {k: twin[(tuple(gm.P[k]), tuple(gm.UV[k]))] for k in gm.P}
    by_canon = collections.defaultdict(list)
    for k in new:
        by_canon[canon[k]].append(k)
    dup = 0
    for k in gm.P:
        if k not in new and by_canon.get(canon[k]):
            new[k] = list(new[by_canon[canon[k]][0]])
            dup += 1
    if dup:
        rep["duplicates_filled"] = dup
    # seam copies welded away (a copy missing, its position present): the position of a copy at the same original place
    by_g = collections.defaultdict(list)
    for k in new:
        by_g[G[k]].append(k)
    seam = 0
    for k in gm.P:
        if k not in new and by_g.get(G[k]):
            new[k] = list(new[by_g[G[k]][0]])
            seam += 1
    if seam:
        rep["seam_copies_filled"] = seam
    removed_g = {G[k] for k in gm.P if k not in new}
    if removed_g:
        rep["removed_vertices"] = len(removed_g)
        rep["warnings"].append(f"removed {len(removed_g)} vertices (missing geometry): they keep their original position")
        for k in gm.P:
            if k not in new:
                new[k] = [list(gm.P[k])]
    spread = [(k, max(math.dist(q, ps[0]) for q in ps)) for k, ps in new.items() if len(ps) > 1]
    bad = [(k, d) for k, d in spread if d > SPLIT_TOL]
    if bad:
        rep["warnings"].append(f"{len(bad)} vertices were split and the copies moved apart (max "
                               f"{max(d for _, d in bad) * 1000:.2f} mm): their average position is used")
    if errors:
        raise ValueError("; ".join(errors))
    P1 = {k: [sum(p[i] for p in ps) / len(ps) for i in range(3)] for k, ps in new.items()}
    # triangle check by original position (seams and splits do not count)
    keyed = [G[v[0]] if v[0] in gm.P else None for v in verts]
    tg = collections.Counter(tuple(sorted(keyed[i] for i in t)) for t in tris_g if all(keyed[i] is not None for i in t))
    to = collections.Counter(tuple(sorted(G[k] for k in t)) for t in gm.tris)
    tg = collections.Counter({t: n for t, n in tg.items() if len(set(t)) == 3})
    to = collections.Counter({t: n for t, n in to.items() if len(set(t)) == 3})
    extra, lost = set(tg) - set(to), set(to) - set(tg)
    rep["triangles"] = dict(file=sum(tg.values()), original=sum(to.values()), not_in_original=len(extra),
                            original_not_in_file=len(lost))
    if extra or lost:
        near_add = {keyed[i] for t in tris_g if any(keyed[i] is None for i in t) for i in t if keyed[i] is not None}
        nb_rem = set(removed_g)
        for t in to:
            if set(t) & removed_g:
                nb_rem |= set(t)
        unexplained = [t for t in lost if not (set(t) & nb_rem) and not set(t) <= near_add] + \
                      [t for t in extra if not (set(t) & nb_rem) and not (set(t) & near_add)]
        msg = (f"faces differ from the original: {len(extra)} new, {len(lost)} missing, {len(unexplained)} not explained by "
               "added or removed vertices (positions are taken; faces are not)")
        if not used_ids and len(unexplained) > max(20, 0.02 * len(to)):
            raise ValueError("the vertices could not be matched to the face kit (no ID map): " + msg)
        rep["warnings"].append(msg)
    moved = {k for k in gm.P if math.dist(gm.P[k], P1[k]) > MOVE_EPS}
    rep["vertices"] = len(gm.P)
    rep["moved"] = len(moved)
    if not moved:
        rep["note"] = "no vertex moved: mesh unchanged"
        return rep, None
    disp = {k: math.dist(gm.P[k], P1[k]) for k in moved}
    rep["max_move_mm"] = round(max(disp.values()) * 1000, 3)
    rep["moved_box"] = [[round(min(P1[k][i] for k in moved), 4), round(max(P1[k][i] for k in moved), 4)] for i in range(3)]
    # normals / tangents
    weld, groups = {}, {}
    for k, p in gm.P.items():
        weld[k] = groups.setdefault(tuple(p), len(groups))
    P0 = {k: gm.P[k] for k in gm.P}
    Pn = {k: (P1[k] if k in moved else gm.P[k]) for k in gm.P}
    g0 = surface_normals(P0, gm.tris, weld)
    g1 = surface_normals(Pn, gm.tris, weld)
    rotated, flipped = 0, 0
    b = gm.b
    for part, vb in enumerate(gm.geo):
        for i in range(vb["count"]):
            k = (part, i)
            o = vb["pos"] + 48 * i
            if k in moved:
                struct.pack_into("<3f", b, o, *P1[k])
            a, c = g0.get(weld[k]), g1.get(weld[k])
            if not a or not c or dot(a, a) == 0 or dot(c, c) == 0:
                continue
            cosang = max(-1.0, min(1.0, dot(a, c)))
            ang = math.acos(cosang)
            if ang < ROT_MIN:
                continue
            if ang > math.radians(90):
                flipped += 1
            axis = unit(cross(a, c))
            if dot(axis, axis) == 0:
                continue
            n0 = unit([x / 127 for x in gm.N[k][:3]])
            t0 = unit([x / 127 for x in gm.T[k][:3]])
            n1 = unit(rotate(n0, axis, ang))
            t1 = rotate(t0, axis, ang)
            t1 = unit(sub(t1, [n1[j] * dot(t1, n1) for j in range(3)]))
            struct.pack_into("<4b", b, o + 12, *(snorm(t1) + [gm.T[k][3]]))
            struct.pack_into("<4b", b, o + 16, *(snorm(n1) + [gm.N[k][3]]))
            rotated += 1
    rep["normals_rotated"] = rotated
    if flipped:
        rep["warnings"].append(f"{flipped} vertices: surface turned more than 90 degrees (folded faces?)")
    # lip-sync overlap
    off = gm.morph_offsets()
    if off:
        lip = {k for k, ds in off.items() if max(math.sqrt(dot(d, d)) for d in ds) > 0.0006}
        rep["moved_with_lipsync_offsets"] = len(moved & lip)
        rep["lipsync_vertices"] = len(lip)
    # bounds
    # the game's bounds cover the geometry only, not the lip-sync offsets (M26: offsets reach outside; M8 decode)
    rep["bounds"] = bounds_update(b, [gm.P[k] for k in gm.P], [Pn[k] for k in Pn])
    return rep, bytes(b)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("glb")
    ap.add_argument("--dpc", default=os.path.join(OUT, "P_AMICIA.orig.DPC"))
    ap.add_argument("--out")
    ap.add_argument("--meshes", default="M37,M26,M28,M36")
    ap.add_argument("--work", default=os.path.join(OUT, "import_work", "facepatch"))
    ap.add_argument("--report")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--target", action="append", default=[],
                    help="LABEL=mesh id: patch this mesh instead (the same mesh in a variant archive, e.g. END's copy of M37)")
    a = ap.parse_args()
    os.makedirs(a.work, exist_ok=True)
    g = gltfread.load(a.glb)
    names = [n for _, n in g.mesh_nodes()]
    buf = bytearray(open(a.dpc, "rb").read())
    info = dpc.parse(bytes(buf))
    reports, built = [], {}
    for label in a.meshes.split(","):
        mid, num = MESHES[label]
        mid = {t.split("=")[0]: int(t.split("=")[1]) for t in a.target}.get(label, mid)
        d, e = find(info, {mid})[mid]
        lh = d["body_pos"] - d["link_header_pos"]
        data = bytearray(64) + bytes(buf[d["link_header_pos"]:d["body_pos"]]) + body(buf, d, a.work)
        struct.pack_into("<I", data, 0x2C, lh)
        gm = GameMesh(bytes(data))
        code_of = {num * 10 + p: p for p in range(len(gm.geo))}
        verts, tris, used_ids = gltf_vertices(g, code_of, label, names)
        if not verts:
            reports.append(dict(mesh=label, note="not in the file: unchanged"))
            continue
        try:
            rep, nb = patch_mesh(gm, verts, tris, used_ids, label)
        except ValueError as ex:
            print(f"ERROR {label}: {ex}")
            sys.exit(1)
        reports.append(rep)
        if nb:
            built[mid] = nb
            open(os.path.join(a.work, f"{label}.Mesh_Z"), "wb").write(nb)
    for r in reports:
        print(json.dumps(r))
    if a.out and not a.dry:
        from put_mesh import put
        for mid, nb in built.items():
            print(f"put {mid}:", put(buf, info, mid, nb, a.work, allow_bounds=True))
        open(a.out, "wb").write(buf)
        print("wrote", a.out)
    if a.report:
        json.dump(reports, open(a.report, "w"), indent=1)


if __name__ == "__main__":
    main()
