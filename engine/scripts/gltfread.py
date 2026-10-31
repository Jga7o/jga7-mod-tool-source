"""Minimal glTF 2.0 reader (.glb or .gltf with external/embedded buffers): accessors, node world matrices, skins.

Supports what Blender's glTF exporter writes: byteStride, normalized integer attributes, TRS or matrix nodes,
multiple primitives, JOINTS_n/WEIGHTS_n sets. Not supported: sparse accessors, Draco/meshopt compression (refused).
Matrices are 4x4 lists of rows in column-vector convention (glTF's).
"""
import base64
import json
import math
import os
import struct

CT = {5120: ("b", 1, 127.0), 5121: ("B", 1, 255.0), 5122: ("h", 2, 32767.0), 5123: ("H", 2, 65535.0),
      5125: ("I", 4, None), 5126: ("f", 4, None)}
WIDTH = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


def load(path):
    raw = open(path, "rb").read()
    if raw[:4] == b"glTF":
        jl = struct.unpack_from("<I", raw, 12)[0]
        doc = json.loads(raw[20:20 + jl])
        bins = [raw[28 + jl:28 + jl + struct.unpack_from("<I", raw, 20 + jl)[0]]] if len(raw) > 20 + jl else [b""]
    else:
        doc = json.loads(raw)
        bins = []
        for b in doc.get("buffers", []):
            uri = b.get("uri", "")
            if uri.startswith("data:"):
                bins.append(base64.b64decode(uri.split(",", 1)[1]))
            else:
                bins.append(open(os.path.join(os.path.dirname(os.path.abspath(path)), uri), "rb").read())
    for ext in doc.get("extensionsRequired", []):
        raise ValueError(f"glTF extension required but not supported: {ext} (export without compression)")
    return Gltf(doc, bins)


def mmul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def from_colmajor(v):
    return [[v[c * 4 + r] for c in range(4)] for r in range(4)]


def trs(n):
    if "matrix" in n:
        return from_colmajor(n["matrix"])
    t = n.get("translation", [0, 0, 0])
    x, y, z, w = n.get("rotation", [0, 0, 0, 1])
    s = n.get("scale", [1, 1, 1])
    R = [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
         [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
         [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]
    return [[R[i][0] * s[0], R[i][1] * s[1], R[i][2] * s[2], t[i]] for i in range(3)] + [[0, 0, 0, 1]]


class Gltf:
    def __init__(self, doc, bins):
        self.doc, self.bins = doc, bins
        self.parent = {}
        for i, n in enumerate(doc.get("nodes", [])):
            for c in n.get("children", []):
                self.parent[c] = i
        self._world = {}

    def accessor(self, i):
        a = self.doc["accessors"][i]
        if "sparse" in a:
            raise ValueError("sparse accessors are not supported")
        fmt, size, norm = CT[a["componentType"]]
        w = WIDTH[a["type"]]
        n = a["count"]
        if "bufferView" not in a:
            return [[0.0] * w for _ in range(n)]
        v = self.doc["bufferViews"][a["bufferView"]]
        buf = self.bins[v.get("buffer", 0)]
        base = v.get("byteOffset", 0) + a.get("byteOffset", 0)
        stride = v.get("byteStride", size * w)
        out = []
        for k in range(n):
            vals = list(struct.unpack_from(f"<{w}{fmt}", buf, base + k * stride))
            if a.get("normalized") and norm:
                vals = [max(x / norm, -1.0) for x in vals]
            out.append(vals)
        return out

    def world(self, i):
        if i not in self._world:
            m = trs(self.doc["nodes"][i])
            p = self.parent.get(i)
            self._world[i] = mmul(self.world(p), m) if p is not None else m
        return self._world[i]

    def find_node(self, name):
        nodes = self.doc.get("nodes", [])
        exact = [i for i, n in enumerate(nodes) if n.get("name") == name and "mesh" in n]
        if exact:
            return exact[0]
        pref = [i for i, n in enumerate(nodes) if n.get("name", "").startswith(name) and "mesh" in n]
        if len(pref) == 1:
            return pref[0]
        raise ValueError(f"mesh object '{name}' not found (or ambiguous: {[nodes[i]['name'] for i in pref]})")

    def mesh_nodes(self):
        return [(i, n.get("name", f"node{i}")) for i, n in enumerate(self.doc.get("nodes", [])) if "mesh" in n]

    def skin_matrices(self, skin_index):
        """Per joint: inverse(armature world) x world(joint) x inverseBind, and the joint node indices.
        The armature is the parent node of the topmost joint (Blender's armature object); removing its world transform
        keeps positions in skeleton space when the whole armature was moved or rotated."""
        s = self.doc["skins"][skin_index]
        ibm = [from_colmajor(v) for v in self.accessor(s["inverseBindMatrices"])] if "inverseBindMatrices" in s else \
            [[[1 if r == c else 0 for c in range(4)] for r in range(4)] for _ in s["joints"]]
        js = set(s["joints"])
        tops = [j for j in s["joints"] if self.parent.get(j) not in js]
        arm = self.parent.get(tops[0]) if tops else None
        A = inverse(self.world(arm)) if arm is not None else None
        mats = [mmul(self.world(j), b) for j, b in zip(s["joints"], ibm)]
        return ([mmul(A, m) for m in mats] if A else mats), s["joints"]


def inverse(m):
    """General 4x4 inverse (Gauss-Jordan)."""
    a = [list(map(float, r)) + [1.0 if i == j else 0.0 for j in range(4)] for i, r in enumerate(m)]
    for c in range(4):
        p = max(range(c, 4), key=lambda r: abs(a[r][c]))
        a[c], a[p] = a[p], a[c]
        d = a[c][c]
        a[c] = [x / d for x in a[c]]
        for r in range(4):
            if r != c:
                f = a[r][c]
                a[r] = [x - f * y for x, y in zip(a[r], a[c])]
    return [r[4:] for r in a]


def is_identity(m, tol=1e-5):
    return all(abs(m[r][c] - (1.0 if r == c else 0.0)) < tol for r in range(4) for c in range(4))


def apply(m, p, w=1.0):
    return [m[r][0] * p[0] + m[r][1] * p[1] + m[r][2] * p[2] + m[r][3] * w for r in range(3)]


def unit(v):
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v] if n > 1e-12 else list(v)
