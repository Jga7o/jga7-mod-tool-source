"""Export a Mesh_Z (bff extract file) to glTF 2.0 + OBJ, and import the glTF back into a Mesh_Z.

Export writes <out>.gltf (+ <out>.bin) and <out>.obj:
- glTF attributes (one primitive per submesh, shared vertex buffer):
  POSITION f32 vec3 (exact), NORMAL f32 vec3 and TANGENT f32 vec4 (decoded from snorm8 and normalized, for viewing
  and editing), TEXCOORD_0 f32 vec2 (from f16, exact; not flipped), and the raw game data as app-specific attributes
  _NORMAL_RAW / _TANGENT_RAW (4 x i8), _BONES (4 x u16), _WEIGHTS (4 x u16, or 1 x f32 for stride-44 rigid vertices),
  COLOR_0 (4 x u8 normalized: the vertex color in the last 4 bytes of each vertex).
  Bone indices are the game's skin-bone numbers; there is no glTF skin (the skeleton is not decoded yet).
  Vertex bytes 12-15 = tangent (along dP/du, w = handedness), bytes 16-19 = normal (w = 127); corrected in M5,
  M4 had them swapped. Axes need no conversion (y up, model faces +z, her left = +x); triangle winding is flipped.
- OBJ: positions, normals, UVs and faces (one group per submesh), for looking at the mesh in a viewer only.
Import rebuilds the Mesh_Z from the original file (template for the header, counts and submesh entries) with every
vertex and index byte taken from the glTF; the template's vertex and index regions are zeroed first. Counts must match.
Normals/tangents: if the glTF NORMAL/TANGENT still equals the value decoded from _*_RAW, the raw bytes are written;
otherwise the new vector is encoded as snorm8 (round(v * 127)) with the raw w byte kept.
Meshes with morph blocks (flags 258: the face M37 and mouth M26) are refused.

Usage: python -I meshio.py export <mesh.Mesh_Z> <out_prefix>
       python -I meshio.py import <template.Mesh_Z> <in.gltf> <out.Mesh_Z> [--pad] [--bounds]
--pad: fewer vertices/indices than the template are allowed (single-submesh meshes); the rest is padded.
--bounds: recompute the link-header bounding sphere and box from the new vertices.
"""
import json
import math
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from meshfmt import parse, check  # noqa: E402

F32 = 5126
U16 = 5123
I8 = 5120
U8 = 5121


def flip(idx):
    """Swap the 2nd and 3rd index of every triangle. The game's front faces satisfy N . ((b-a) x (c-a)) < 0 (clockwise);
    glTF and OBJ want counter-clockwise, so export flips and import flips back (its own inverse)."""
    out = list(idx)
    for t in range(0, len(out) - 2, 3):
        out[t + 1], out[t + 2] = out[t + 2], out[t + 1]
    return out


def _unit(v):
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v] if n else list(v)


def f32(x):
    return struct.unpack("<f", struct.pack("<f", x))[0]


def decode_vertices(b, m):
    if len(m["vbs"]) != 1 or m["vbs"][0]["flags"] != 2 or len(m["ibs"]) != 1:
        raise ValueError("only meshes with one geometry block and one index buffer are supported")
    vb = m["vbs"][0]
    st = vb["stride"]
    out = []
    for k in range(vb["count"]):
        o = vb["pos"] + k * st
        v = dict(pos=list(struct.unpack_from("<3f", b, o)),
                 traw=list(struct.unpack_from("<4b", b, o + 12)),   # tangent = dP/du, w = handedness
                 nraw=list(struct.unpack_from("<4b", b, o + 16)),   # normal, w = 127
                 uv=list(struct.unpack_from("<2e", b, o + 20)))
        if b[o + 24:o + 28] != b"\0\0\0\0":
            raise ValueError(f"vertex {k}: bytes 24-27 not zero")
        v["color"] = list(b[o + st - 4:o + st])  # vertex color RGBA8, white on almost every mesh
        v["bones"] = list(struct.unpack_from("<4H", b, o + 28))
        if st == 48:
            v["weights"] = list(struct.unpack_from("<4H", b, o + 36))
        elif st == 44:
            v["weights"] = [struct.unpack_from("<f", b, o + 36)[0]]
        else:
            raise ValueError(f"stride {st}")
        v["normal"] = [f32(x) for x in _unit([x / 127 for x in v["nraw"][:3]])]
        t = _unit([x / 127 for x in v["traw"][:3]])
        v["tangent"] = [f32(x) for x in t] + [1.0 if v["traw"][3] >= 0 else -1.0]
        out.append(v)
    return out


def export(src, prefix):
    b = open(src, "rb").read()
    m = parse(b)
    msgs = check(b, m)
    if msgs:
        raise ValueError(msgs)
    vs = decode_vertices(b, m)
    st = m["vbs"][0]["stride"]
    ib = m["ibs"][0]
    idx = list(struct.unpack_from(f"<{ib['count']}H", b, ib["pos"]))
    blob = bytearray()
    views, accs = [], []

    def add(data, ctype, typ, count, normalized=False, minmax=None, target=34962):
        while len(blob) % 4:
            blob.append(0)
        views.append(dict(buffer=0, byteOffset=len(blob), byteLength=len(data), target=target))
        blob.extend(data)
        a = dict(bufferView=len(views) - 1, componentType=ctype, count=count, type=typ)
        if normalized:
            a["normalized"] = True
        if minmax:
            a["min"], a["max"] = minmax
        accs.append(a)
        return len(accs) - 1

    n = len(vs)
    pos = [v["pos"] for v in vs]
    attr = dict(
        POSITION=add(struct.pack(f"<{3 * n}f", *[x for p in pos for x in p]), F32, "VEC3", n,
                     minmax=([min(p[i] for p in pos) for i in range(3)], [max(p[i] for p in pos) for i in range(3)])),
        NORMAL=add(struct.pack(f"<{3 * n}f", *[x for v in vs for x in v["normal"]]), F32, "VEC3", n),
        TANGENT=add(struct.pack(f"<{4 * n}f", *[x for v in vs for x in v["tangent"]]), F32, "VEC4", n),
        TEXCOORD_0=add(struct.pack(f"<{2 * n}f", *[x for v in vs for x in v["uv"]]), F32, "VEC2", n),
        _NORMAL_RAW=add(struct.pack(f"<{4 * n}b", *[x for v in vs for x in v["nraw"]]), I8, "VEC4", n),
        _TANGENT_RAW=add(struct.pack(f"<{4 * n}b", *[x for v in vs for x in v["traw"]]), I8, "VEC4", n),
        _BONES=add(struct.pack(f"<{4 * n}H", *[x for v in vs for x in v["bones"]]), U16, "VEC4", n),
        COLOR_0=add(bytes(x for v in vs for x in v["color"]), U8, "VEC4", n, normalized=True),
    )
    if st == 48:
        attr["_WEIGHTS"] = add(struct.pack(f"<{4 * n}H", *[x for v in vs for x in v["weights"]]), U16, "VEC4", n)
    else:
        attr["_WEIGHTS"] = add(struct.pack(f"<{n}f", *[v["weights"][0] for v in vs]), F32, "SCALAR", n)
    prims = []
    for s in m["subs"]:
        seg = flip(idx[s["start"]:s["start"] + 3 * s["ntri"]])
        ia = add(struct.pack(f"<{len(seg)}H", *seg), U16, "SCALAR", len(seg), target=34963)
        prims.append(dict(attributes=attr, indices=ia, mode=4))
    name = os.path.basename(src)
    binname = os.path.basename(prefix) + ".bin"
    doc = dict(asset=dict(version="2.0", generator="modlab meshio.py"),
               scene=0, scenes=[dict(nodes=[0])], nodes=[dict(mesh=0, name=name)],
               meshes=[dict(name=name, primitives=prims)],
               buffers=[dict(byteLength=len(blob), uri=binname)], bufferViews=views, accessors=accs,
               extras=dict(source=name, stride=st, vertices=n, indices=len(idx), submeshes=len(m["subs"])))
    open(prefix + ".bin", "wb").write(blob)
    open(prefix + ".gltf", "w").write(json.dumps(doc, indent=1))
    with open(prefix + ".obj", "w") as o:
        o.write(f"# {name}: {n} vertices, {len(idx) // 3} triangles (meters, y up; view only)\n")
        for p in pos:
            o.write("v %r %r %r\n" % tuple(p))
        for v in vs:
            o.write("vn %r %r %r\n" % tuple(v["normal"]))
        for v in vs:
            o.write("vt %r %r\n" % tuple(v["uv"]))
        for k, s in enumerate(m["subs"]):
            o.write(f"g submesh{k}\n")
            seg = flip(idx[s["start"]:s["start"] + 3 * s["ntri"]])
            for t in range(0, len(seg), 3):
                a, c, d = (x + 1 for x in seg[t:t + 3])
                o.write(f"f {a}/{a}/{a} {c}/{c}/{c} {d}/{d}/{d}\n")
    return n, len(idx)


def read_gltf(path):
    doc = json.load(open(path))
    blob = open(os.path.join(os.path.dirname(os.path.abspath(path)), doc["buffers"][0]["uri"]), "rb").read()
    fmt = {F32: "f", U16: "H", I8: "b", U8: "B"}
    width = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}

    def acc(i):
        a = doc["accessors"][i]
        v = doc["bufferViews"][a["bufferView"]]
        w = width[a["type"]]
        vals = struct.unpack_from(f"<{a['count'] * w}{fmt[a['componentType']]}", blob, v["byteOffset"] + a.get("byteOffset", 0))
        return [list(vals[k * w:(k + 1) * w]) for k in range(a["count"])]
    prims = doc["meshes"][0]["primitives"]
    at = {k: acc(i) for k, i in prims[0]["attributes"].items()}
    idx = flip([x[0] for p in prims for x in acc(p["indices"])])
    return at, idx


def encode_dir(vec, raw):
    """Raw bytes if vec still equals the decoded raw value, else snorm8 of vec with the raw w byte."""
    dec = [f32(x) for x in _unit([x / 127 for x in raw[:3]])]
    if [f32(x) for x in vec[:3]] == dec:
        return raw
    u = _unit(vec[:3])
    return [max(-127, min(127, round(x * 127))) for x in u] + [raw[3]]


def write_bounds(b, pts):
    """Recompute the link-header bounds (link +16): sphere = box center + farthest-point radius, then the oriented box
    as a 3x4 matrix (identity rotation, center in column 4), half extents and their length. Same layout as the game's."""
    lo = [min(p[i] for p in pts) for i in range(3)]
    hi = [max(p[i] for p in pts) for i in range(3)]
    c = [f32((lo[i] + hi[i]) / 2) for i in range(3)]
    half = [f32((hi[i] - lo[i]) / 2) for i in range(3)]
    r = max(math.dist(p, c) for p in pts)
    vals = c + [r, 1, 0, 0, c[0], 0, 1, 0, c[1], 0, 0, 1, c[2]] + half + [math.sqrt(sum(h * h for h in half))]
    struct.pack_into("<20f", b, 64 + 16, *vals)


def build(template, gltf, pad=False, bounds=False):
    """pad: allow fewer vertices/indices than the template; the remaining vertex slots get copies of vertex 0 and the
    remaining triangles become degenerate (0,0,0), so every count and size stays. bounds: recompute the link-header
    bounding sphere/box from the glTF's vertices."""
    b = bytearray(open(template, "rb").read())
    m = parse(bytes(b))
    vb, ib = m["vbs"][0], m["ibs"][0]
    if len(m["vbs"]) != 1 or vb["flags"] != 2 or len(m["ibs"]) != 1:
        raise ValueError("unsupported template")
    st = vb["stride"]
    at, idx = read_gltf(gltf)
    n = len(at["POSITION"])
    fits = n <= vb["count"] and len(idx) <= ib["count"] and len(m["subs"]) == 1
    if (n, len(idx)) != (vb["count"], ib["count"]) and not (pad and fits):
        raise ValueError(f"counts differ: vertices {n} vs {vb['count']}, indices {len(idx)} vs {ib['count']}")
    b[vb["pos"]:vb["pos"] + vb["count"] * st] = bytes(vb["count"] * st)
    b[ib["pos"]:ib["pos"] + 2 * ib["count"]] = bytes(2 * ib["count"])
    for k in range(n):
        o = vb["pos"] + k * st
        struct.pack_into("<3f", b, o, *at["POSITION"][k])
        struct.pack_into("<4b", b, o + 12, *encode_dir(at["TANGENT"][k], at["_TANGENT_RAW"][k]))
        struct.pack_into("<4b", b, o + 16, *encode_dir(at["NORMAL"][k], at["_NORMAL_RAW"][k]))
        struct.pack_into("<2e", b, o + 20, *at["TEXCOORD_0"][k])
        struct.pack_into("<4H", b, o + 28, *at["_BONES"][k])
        if st == 48:
            struct.pack_into("<4H", b, o + 36, *at["_WEIGHTS"][k])
        else:
            struct.pack_into("<f", b, o + 36, at["_WEIGHTS"][k][0])
        b[o + st - 4:o + st] = bytes(at["COLOR_0"][k])
    for k in range(n, vb["count"]):  # padding: copies of vertex 0
        o = vb["pos"] + k * st
        b[o:o + st] = b[vb["pos"]:vb["pos"] + st]
    struct.pack_into(f"<{len(idx)}H", b, ib["pos"], *idx)  # padding indices stay 0 (degenerate)
    if bounds:
        write_bounds(b, at["POSITION"])
    msgs = check(bytes(b), parse(bytes(b)))
    if msgs:
        raise ValueError(msgs)
    return bytes(b)


def main():
    if sys.argv[1] == "export":
        n, i = export(sys.argv[2], sys.argv[3])
        print(f"exported {n} vertices, {i} indices -> {sys.argv[3]}.gltf/.bin/.obj")
    elif sys.argv[1] == "import":
        out = build(sys.argv[2], sys.argv[3], pad="--pad" in sys.argv, bounds="--bounds" in sys.argv)
        open(sys.argv[4], "wb").write(out)
        print(f"wrote {sys.argv[4]} ({len(out)} bytes)")


if __name__ == "__main__":
    main()
