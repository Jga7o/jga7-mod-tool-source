"""Merge several Mesh_Z pieces that use identical materials into ONE Mesh_Z (M13 merge test).

The first piece is the target (its link header, MeshData and Material_Z IDs are kept). The vertices of every piece are
copied byte for byte, in order (target first), the indices are offset by the vertices before them, and one submesh covers
everything. Only the link-header bounds (sphere + box, recomputed over all vertices) and the counts change.
Refused: morph data, more than one vertex block / index buffer / submesh, different strides or vertex formats, Material_Z
contents that differ (shader flags or textures), more than 65,535 vertices (u16 indices), --res missing a Material_Z.
Usage: python -I mergemesh.py --res <unpacked\\resources> --out <merged.Mesh_Z> <target id> <id> [<id> ...] [--report r.json]
"""
import argparse
import hashlib
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from meshfmt import parse, check  # noqa: E402
from meshio import write_bounds  # noqa: E402

MAX_VERTS = 65535  # u16 indices; submesh vmin/vmax are u16 too (last vertex index 65534)


def material_ids(b, res):
    """Material_Z IDs referenced by a Mesh_Z (link header)."""
    out = []
    for k in range(64, 64 + struct.unpack_from("<I", b, 0x2C)[0] - 7):
        v = struct.unpack_from("<q", b, k)[0]
        if os.path.isfile(os.path.join(res, f"{v}.Material_Z")) and v not in out:
            out.append(v)
    return out


def assemble(tb, tm, verts, idx):
    """A Mesh_Z (bff extract form) with the template's header, link header, leading zeros, submesh entry fields and
    rest, but one vertex block of these vertex bytes and one index buffer of these indices (one submesh over all)."""
    stride = tm["vbs"][0]["stride"]
    nv = len(verts) // stride
    if nv > MAX_VERTS:
        raise ValueError(f"{nv} vertices: more than {MAX_VERTS} (16-bit indices)")
    s = tm["subs"][0]
    sub = bytearray(tb[s["pos"]:s["pos"] + 32])
    struct.pack_into("<HHHHIIIII", sub, 0, s["prim"], s["x"], 0, nv - 1, nv, 0, len(idx) // 3, s["z"], 0)
    lh = tm["link"]
    body = (tb[64 + lh:64 + lh + 20] + struct.pack("<I", 1) + struct.pack("<III", nv, stride, 2) + verts
            + struct.pack("<I", 1) + struct.pack("<II", len(idx), 2) + struct.pack(f"<{len(idx)}H", *idx)
            + struct.pack("<I", 1) + bytes(sub) + tb[tm["rest_pos"]:])
    head = bytearray(tb[:64 + lh])
    struct.pack_into("<II", head, 0x28, lh + len(body), lh)   # bff extract header: link + body, link size, body size
    struct.pack_into("<I", head, 0x30, len(body))
    return bytes(head) + body


def merge(res, ids):
    pieces = []
    for i in ids:
        b = open(os.path.join(res, f"{i}.Mesh_Z"), "rb").read()
        m = parse(b)
        if len(m["vbs"]) != 1 or m["vbs"][0]["flags"] != 2 or len(m["ibs"]) != 1 or len(m["subs"]) != 1:
            raise ValueError(f"{i}: morph data or several blocks/submeshes; not mergeable")
        if m["ibs"][0]["size"] != 2:
            raise ValueError(f"{i}: index size {m['ibs'][0]['size']}")
        if check(b, m):
            raise ValueError(f"{i}: {check(b, m)}")
        mats = material_ids(b, res)
        if len(mats) != 1:
            raise ValueError(f"{i}: {len(mats)} materials")
        mb = open(os.path.join(res, f"{mats[0]}.Material_Z"), "rb").read()
        pieces.append(dict(id=i, b=b, m=m, mat=mats[0], mat_sha=hashlib.sha256(mb[64:]).hexdigest()))
    t = pieces[0]
    if len({p["mat_sha"] for p in pieces}) != 1:
        raise ValueError("the Material_Z contents differ: " + ", ".join(f"{p['id']}: {p['mat_sha'][:12]}" for p in pieces))
    fmt = {(p["m"]["vbs"][0]["stride"], p["m"]["subs"][0]["x"], p["m"]["subs"][0]["prim"]) for p in pieces}
    if len(fmt) != 1:
        raise ValueError(f"different vertex formats {fmt}")
    stride = t["m"]["vbs"][0]["stride"]
    nv = sum(p["m"]["vbs"][0]["count"] for p in pieces)
    if nv > MAX_VERTS:
        raise ValueError(f"{nv} vertices: more than {MAX_VERTS} (16-bit indices)")
    verts, idx, base, ranges = bytearray(), [], 0, []
    for p in pieces:
        vb, ib = p["m"]["vbs"][0], p["m"]["ibs"][0]
        verts += p["b"][vb["pos"]:vb["pos"] + vb["count"] * stride]
        idx += [x + base for x in struct.unpack_from(f"<{ib['count']}H", p["b"], ib["pos"])]
        ranges.append(dict(id=p["id"], first_vertex=base, vertices=vb["count"], first_index=len(idx) - ib["count"],
                           indices=ib["count"]))
        base += vb["count"]
    out = bytearray(assemble(t["b"], t["m"], bytes(verts), idx))
    write_bounds(out, [struct.unpack_from("<3f", verts, k * stride) for k in range(nv)])
    msgs = check(bytes(out), parse(bytes(out)))
    if msgs:
        raise ValueError(msgs)
    return bytes(out), dict(target=t["id"], pieces=ranges, vertices=nv, triangles=len(idx) // 3, material=t["mat"],
                            material_sha=t["mat_sha"], size=len(out))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ids", nargs="+", type=int)
    ap.add_argument("--res", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--report")
    a = ap.parse_args()
    try:
        b, rep = merge(a.res, a.ids)
    except ValueError as e:
        sys.exit(f"ERROR: {e}")
    open(a.out, "wb").write(b)
    if a.report:
        json.dump(rep, open(a.report, "w"), indent=1)
    print(f"merged {len(a.ids)} pieces into {rep['target']}: {rep['vertices']} vertices, {rep['triangles']} triangles, "
          f"{rep['size']} bytes; material {rep['material']} (identical contents)")


if __name__ == "__main__":
    main()
