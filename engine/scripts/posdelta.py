"""Vertex position deltas for a mesh (M12 mod package): the face edit stored as "how far each vertex moved", without
the game's own positions.

make:  python -I posdelta.py make <original.Mesh_Z> <edited.Mesh_Z> <out.json> --mesh-id <id> --label <name>
       Only position bytes may differ besides normals/tangents/bounds; delta = edited - original per moved vertex
       (exact: original + delta gives the edited float back).
apply: python -I posdelta.py apply --dpc <in.DPC> --dst <out.DPC> --deltas <json> [--work <dir>] [--report <json>]
       New positions = the archive's own positions + deltas; then facepatch.patch_mesh (normals and tangents rotated
       with the surface, bounds grown if needed, everything else untouched) and put_mesh (same as facepatch.py), so the
       result is byte-identical to the original build. The file records the vertex count per block: a mesh with other
       counts is refused.
"""
import argparse
import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import dpc  # noqa: E402
from facepatch import GameMesh, patch_mesh  # noqa: E402
from necklacepatch import mesh_from_dpc  # noqa: E402


def make(a):
    o = GameMesh(open(a.original, "rb").read())
    e = GameMesh(open(a.edited, "rb").read())
    if [v["count"] for v in o.geo] != [v["count"] for v in e.geo]:
        sys.exit("vertex counts differ")
    deltas = {}
    for k in sorted(o.P):
        if o.P[k] != e.P[k]:
            d = [e.P[k][i] - o.P[k][i] for i in range(3)]
            assert all(struct.pack("<f", o.P[k][i] + d[i]) == struct.pack("<f", e.P[k][i]) for i in range(3))
            deltas[f"{k[0]}:{k[1]}"] = d
    doc = dict(format="position deltas 1", mesh_id=str(a.mesh_id), label=a.label,
               vertex_counts=[v["count"] for v in o.geo], moved=len(deltas), deltas=deltas)
    json.dump(doc, open(a.out, "w"), separators=(",", ":"))
    print(f"{a.label}: {len(deltas)} of {len(o.P)} vertices moved -> {a.out}")


def apply(a):
    doc = json.load(open(a.deltas))
    mid = int(doc["mesh_id"])
    os.makedirs(a.work, exist_ok=True)
    buf = bytearray(open(a.dpc, "rb").read())
    info = dpc.parse(bytes(buf))
    gm = mesh_from_dpc(buf, info, mid, a.work)
    if [v["count"] for v in gm.geo] != doc["vertex_counts"]:
        sys.exit(f"ERROR {doc['label']}: the archive's mesh has {[v['count'] for v in gm.geo]} vertices, the deltas were made "
                 f"for {doc['vertex_counts']} (another game version?)")
    D = {tuple(int(x) for x in k.split(":")): v for k, v in doc["deltas"].items()}
    P1 = {k: ([gm.P[k][i] + D[k][i] for i in range(3)] if k in D else gm.P[k]) for k in gm.P}
    full = [(k, P1[k], gm.UV[k], doc["label"]) for k in sorted(gm.P)]
    idx = {k: n for n, (k, *_) in enumerate(full)}
    rep, nb = patch_mesh(gm, full, [tuple(idx[k] for k in t) for t in gm.tris], True, doc["label"])
    print(json.dumps(rep))
    if a.report:
        json.dump([rep], open(a.report, "w"), indent=1)
    if not nb:
        sys.exit(f"ERROR {doc['label']}: no vertex moved")
    open(os.path.join(a.work, f"{doc['label']}.Mesh_Z"), "wb").write(nb)
    from put_mesh import put
    print(f"put {mid}:", put(buf, info, mid, nb, a.work, allow_bounds=True))
    open(a.dst, "wb").write(buf)
    print("wrote", a.dst)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["make", "apply"])
    ap.add_argument("original", nargs="?")
    ap.add_argument("edited", nargs="?")
    ap.add_argument("out", nargs="?")
    ap.add_argument("--mesh-id")
    ap.add_argument("--label", default="mesh")
    ap.add_argument("--dpc")
    ap.add_argument("--dst")
    ap.add_argument("--deltas")
    ap.add_argument("--work", default=".")
    ap.add_argument("--report")
    a = ap.parse_args()
    if a.mode == "make":
        make(a)
    else:
        apply(a)


if __name__ == "__main__":
    main()
