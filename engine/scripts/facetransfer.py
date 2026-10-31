"""Carry the face edit (M37 positions) to a variant archive's own face mesh with a different vertex layout (M10).

Displacement of an M37 vertex = edited M37 - original M37 (both Mesh_Z files). For each vertex of the target mesh:
- copy: an M37 vertex lies at its position (< 0.1 mm): it takes that vertex's move (same UV preferred, then nearest);
- interpolate: otherwise the inverse-distance mean of the moves of the 4 nearest M37 vertices.
The new positions then go through facepatch.patch_mesh (normals/tangents rotated with the surface, bounds grown, lip-sync
data and everything else untouched). Checks on the interpolated vertices: deviation from a smooth surface (distance to
the mean of their edge neighbours, before -> after) and triangles whose normal turns more than 90 degrees (flips), with
the same measures for the copied vertices for comparison. Coincident target vertices may separate only as much as
M37's own copies at that position do (the closed lips, which the edit parts by up to 0.9 mm).
Usage: python -I facetransfer.py --dpc <in.DPC> --out <out.DPC> --target <mesh id> --orig <M37 original.Mesh_Z>
         --edited <M37 edited.Mesh_Z> [--label M37] [--work <dir>] [--report <json>] [--moves-out <posdelta json>]
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
import dpc  # noqa: E402
from bmpdump import body  # noqa: E402
from facepatch import GameMesh, patch_mesh  # noqa: E402
from patch_hide import find  # noqa: E402

COPY_TOL = 1e-4


def cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dpc", required=True)
    ap.add_argument("--out")
    ap.add_argument("--target", type=int, required=True)
    ap.add_argument("--orig", required=True)
    ap.add_argument("--edited", required=True)
    ap.add_argument("--label", default="M37")
    ap.add_argument("--work", default=".")
    ap.add_argument("--report")
    ap.add_argument("--moves-out", help="write the moves as a posdelta.py file (mod package)")
    a = ap.parse_args()
    os.makedirs(a.work, exist_ok=True)
    A0, A1 = GameMesh(open(a.orig, "rb").read()), GameMesh(open(a.edited, "rb").read())
    keysA = sorted(A0.P)
    D = {k: [A1.P[k][i] - A0.P[k][i] for i in range(3)] for k in keysA}
    buf = bytearray(open(a.dpc, "rb").read())
    info = dpc.parse(bytes(buf))
    d, e = find(info, {a.target})[a.target]
    lh = d["body_pos"] - d["link_header_pos"]
    data = bytearray(64) + bytes(buf[d["link_header_pos"]:d["body_pos"]]) + body(buf, d, a.work)
    struct.pack_into("<I", data, 0x2C, lh)
    gm = GameMesh(bytes(data))
    grid = collections.defaultdict(list)
    for k in keysA:
        grid[tuple(int(c // 0.004) for c in A0.P[k])].append(k)

    def nearest(p, n):
        c = tuple(int(x // 0.004) for x in p)
        for r in range(1, 10):
            cand = [k for dx in range(-r, r + 1) for dy in range(-r, r + 1) for dz in range(-r, r + 1)
                    for k in grid.get((c[0] + dx, c[1] + dy, c[2] + dz), ())]
            if len(cand) >= n:
                return sorted(cand, key=lambda k: math.dist(A0.P[k], p))[:n]
        return sorted(keysA, key=lambda k: math.dist(A0.P[k], p))[:n]
    new, how, src_d, uvmatch, mv_of = {}, {}, {}, {}, {}
    for k, p in gm.P.items():
        ks = nearest(p, 4)
        d0 = math.dist(A0.P[ks[0]], p)
        if d0 < COPY_TOL:
            at = [j for j in ks if math.dist(A0.P[j], p) < COPY_TOL]
            same_uv = [j for j in at if A0.UV[j] == gm.UV[k]]
            j = (same_uv or at)[0]
            mv = D[j]
            how[k] = "copy"
            uvmatch[k] = bool(same_uv)
        else:
            w = [1.0 / math.dist(A0.P[j], p) for j in ks]
            mv = [sum(w[n] * D[j][i] for n, j in enumerate(ks)) / sum(w) for i in range(3)]
            how[k] = "interpolate"
            src_d[k] = d0
        new[k] = [p[i] + mv[i] for i in range(3)]
        mv_of[k] = mv
    # coincident target vertices must move together
    co = collections.defaultdict(list)
    for k, p in gm.P.items():
        co[tuple(p)].append(k)
    spread, allowed = 0.0, 0.0  # coincident target vertices may only separate as much as M37's own copies there do
    for p, ks in co.items():
        if len(ks) < 2:
            continue
        s = max(math.dist(new[x], new[y]) for x in ks for y in ks)
        src = [j for j in nearest(p, 6) if math.dist(A0.P[j], p) < COPY_TOL]
        s0 = max((math.dist(A1.P[x], A1.P[y]) for x in src for y in src), default=0.0)
        if s > s0 + 1e-6:
            spread = max(spread, s - s0)
        allowed = max(allowed, s0)
    # smoothness + flips
    nb = collections.defaultdict(set)
    for t in gm.tris:
        for x in t:
            for y in t:
                if x != y:
                    nb[x].add(y)

    def lap(P, k):
        m = [sum(P[j][i] for j in nb[k]) / len(nb[k]) for i in range(3)]
        return math.dist(P[k], m)

    def tri_n(P, t):
        a_, b_, c_ = (P[x] for x in t)
        n = cross([b_[i] - a_[i] for i in range(3)], [c_[i] - a_[i] for i in range(3)])
        L = math.sqrt(sum(x * x for x in n))
        return [x / L for x in n] if L > 1e-14 else None
    stats = {}
    for kind in ("interpolate", "copy"):
        ks = [k for k in gm.P if how[k] == kind and nb[k]]
        if not ks:
            continue
        dl = sorted(((lap(new, k) - lap(gm.P, k)), k) for k in ks)
        tris = [t for t in gm.tris if any(how[x] == kind for x in t)]
        turn, flips = [], []
        for t in tris:
            n0, n1 = tri_n(gm.P, t), tri_n(new, t)
            if n0 and n1:
                ang = math.degrees(math.acos(max(-1.0, min(1.0, sum(x * y for x, y in zip(n0, n1))))))
                turn.append(ang)
                if ang > 90:
                    flips.append((t, round(ang, 1)))
        worst = dl[-1][1]
        stats[kind] = dict(vertices=len(ks), smooth_dev_before_max_mm=round(max(lap(gm.P, k) for k in ks) * 1000, 3),
                           smooth_dev_after_max_mm=round(max(lap(new, k) for k in ks) * 1000, 3),
                           smooth_dev_increase_max_mm=round(dl[-1][0] * 1000, 3),
                           worst_vertex=[worst, [round(x, 4) for x in gm.P[worst]]],
                           triangles=len(tris), triangle_turn_max_deg=round(max(turn), 1) if turn else 0,
                           flipped_triangles=flips[:20], flipped_count=len(flips))
    interp = [k for k in gm.P if how[k] == "interpolate"]
    moves = [math.dist(gm.P[k], new[k]) for k in gm.P]
    rep0 = dict(target=a.target, vertices=len(gm.P), copied=sum(v == "copy" for v in how.values()), interpolated=len(interp),
                interpolated_distance_to_m37_max_mm=round(max(src_d.values()) * 1000, 2) if src_d else 0,
                interpolated_move_max_mm=round(max((math.dist(gm.P[k], new[k]) for k in interp), default=0) * 1000, 2),
                max_move_mm=round(max(moves) * 1000, 3), coincident_extra_spread_mm=round(spread * 1000, 4),
                coincident_spread_like_m37_max_mm=round(allowed * 1000, 3),
                copied_with_same_uv=sum(uvmatch.values()), copied_by_position_only=sum(not v for v in uvmatch.values()), checks=stats)
    verts = [(k, new[k], gm.UV[k], a.label) for k in sorted(gm.P)]
    idx = {k: n for n, (k, *_) in enumerate(verts)}
    rep, nb_ = patch_mesh(gm, verts, [tuple(idx[k] for k in t) for t in gm.tris], True, a.label)
    rep.update(rep0)
    print(json.dumps(rep))
    if spread > 1e-6:
        print("ERROR: coincident vertices moved apart more than in M37 (would open a seam)")
        sys.exit(1)
    if any(s["flipped_count"] for s in stats.values()):
        print("ERROR: triangles flip")
        sys.exit(1)
    if nb_:
        open(os.path.join(a.work, f"{a.label}.Mesh_Z"), "wb").write(nb_)
        if a.out:
            from put_mesh import put
            print(f"put {a.target}:", put(buf, info, a.target, nb_, a.work, allow_bounds=True))
            open(a.out, "wb").write(buf)
            print("wrote", a.out)
    if a.report:
        json.dump([rep], open(a.report, "w"), indent=1)
    if a.moves_out:  # the exact moves added above, so posdelta.py apply reproduces this result byte for byte
        mv = {f"{k[0]}:{k[1]}": [float(x) for x in m] for k, m in sorted(mv_of.items()) if any(x != 0 for x in m)}
        json.dump(dict(format="position deltas 1", mesh_id=str(a.target), label=a.label,
                       vertex_counts=[v["count"] for v in gm.geo], moved=len(mv), deltas=mv),
                  open(a.moves_out, "w"), separators=(",", ":"))


if __name__ == "__main__":
    main()
