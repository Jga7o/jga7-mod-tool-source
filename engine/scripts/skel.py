"""Decode a Skel_Z (bff extract file): bones with local transform, world bind matrix, parent, children, name hash.

Body layout (decoded in M6, P_AMICIA): u32 ?, u32 0, f32 x3 (?), u32 bone count N at body +0x14, then N records of
396 bytes from body +0x18:
  +8   local rotation quaternion (x, y, z, w)        +24  local scale (x, y, z)     +36  local translation (x, y, z)
  +48  u32 flags (0x03000007), u32 8                 +56  4x4 rotation matrix and a quaternion (+104, = local rot?)
  +168 u32 3 + six joint-limit angles (radians)       +216 u32 3 + more limits
  +296 u64 name hash                                 +304 u64 (same for every bone: an owner ID)
  +312 world bind matrix, 4 rows of 4 f32 (rotation rows, then translation x, y, z, 1)
  +376 u32 4    +380 i32 first child    +384 i32 parent    +388 i32 next sibling    +392 i32 previous sibling
Mesh vertices use these bone indices directly (0..N-1).

Usage: python -I skel.py <skel.Skel_Z> [--check <resources_dir>]
"""
import math
import os
import struct
import sys

REC = 396
START = 0x18


def qmat(q):
    x, y, z, w = q
    return [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]


def load(path):
    b = open(path, "rb").read()
    lh = struct.unpack_from("<I", b, 0x2C)[0]
    B = b[64 + lh:]
    n = struct.unpack_from("<I", B, 0x14)[0]
    bones = []
    for i in range(n):
        r = B[START + i * REC:START + (i + 1) * REC]
        m = struct.unpack_from("<16f", r, 312)
        bones.append(dict(
            index=i,
            rot=struct.unpack_from("<4f", r, 8), scale=struct.unpack_from("<3f", r, 24), trans=struct.unpack_from("<3f", r, 36),
            name=struct.unpack_from("<Q", r, 296)[0],
            world=[list(m[0:4]), list(m[4:8]), list(m[8:12]), list(m[12:16])],
            child=struct.unpack_from("<i", r, 380)[0], parent=struct.unpack_from("<i", r, 384)[0],
            next=struct.unpack_from("<i", r, 388)[0], prev=struct.unpack_from("<i", r, 392)[0]))
    return bones


def world_pos(bone):
    return bone["world"][3][:3]


def compose(bones):
    """World rotation/translation from the local chain (row-vector convention: p_world = p_local * R + t)."""
    out = {}
    for b in bones:
        R = qmat(b["rot"])
        Rt = [[R[j][i] for j in range(3)] for i in range(3)]  # row-vector form
        t = list(b["trans"])
        if b["parent"] >= 0:
            PR, Pt = out[b["parent"]]
            R2 = [[sum(Rt[i][k] * PR[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
            t2 = [sum(t[k] * PR[k][j] for k in range(3)) + Pt[j] for j in range(3)]
            out[b["index"]] = (R2, t2)
        else:
            out[b["index"]] = (Rt, t)
    return out


def main():
    bones = load(sys.argv[1])
    n = len(bones)
    roots = [b["index"] for b in bones if b["parent"] < 0]
    order_ok = all(b["parent"] < b["index"] for b in bones)
    unit_q = max(abs(math.sqrt(sum(x * x for x in b["rot"])) - 1) for b in bones)
    print(f"{n} bones, roots {roots}, parent before child: {order_ok}, max |quat| - 1: {unit_q:.2e}")
    comp = compose(bones)
    err_t = max(math.dist(comp[i][1], world_pos(bones[i])) for i in range(n))
    err_r = max(abs(comp[i][0][r][c] - bones[i]["world"][r][c]) for i in range(n) for r in range(3) for c in range(3))
    print(f"local chain vs stored world bind: max translation error {err_t:.2e} m, max rotation error {err_r:.2e}")
    kids = {}
    for b in bones:
        kids.setdefault(b["parent"], []).append(b["index"])
    links_ok = all((b["child"] == -1) == (b["index"] not in kids) for b in bones)
    print(f"first-child field consistent with parents: {links_ok}")
    if "--check" in sys.argv:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from meshfmt import parse
        d = sys.argv[sys.argv.index("--check") + 1]
        far, tot, used = [], 0, set()
        for f in os.listdir(d):
            if not f.endswith(".Mesh_Z"):
                continue
            mb = open(os.path.join(d, f), "rb").read()
            m = parse(mb)
            vb = m["vbs"][0]
            for k in range(vb["count"]):
                o = vb["pos"] + k * vb["stride"]
                p = struct.unpack_from("<3f", mb, o)
                bi = struct.unpack_from("<4H", mb, o + 28)
                if vb["stride"] == 48:
                    w = struct.unpack_from("<4H", mb, o + 36)
                    main_b = bi[max(range(4), key=lambda j: w[j])]
                else:
                    main_b = bi[0]
                used.add(main_b)
                far.append(math.dist(p, world_pos(bones[main_b])))
                tot += 1
        far.sort()
        print(f"vertices: {tot}; distance to their main bone: median {far[len(far) // 2]:.3f} m, 95% {far[int(len(far) * .95)]:.3f} m, "
              f"max {far[-1]:.3f} m; distinct main bones {len(used)}")


if __name__ == "__main__":
    main()
