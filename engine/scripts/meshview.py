"""Flat-shaded front and side views of Mesh_Z files (orthographic, z-buffered triangles), as one PNG.

Each argument is a Mesh_Z (bff extract format); its views are placed side by side: front (x right = Amicia's left,
y up) and side (z right = her front). Usage: python -I meshview.py <out.png> <a.Mesh_Z> [<b.Mesh_Z> ...]
"""
import math
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bmppreview import write_png  # noqa: E402
from meshfmt import parse  # noqa: E402

PX = 900  # pixels per meter


def load(path):
    b = open(path, "rb").read()
    m = parse(b)
    vb, ib = m["vbs"][0], m["ibs"][0]
    P = [struct.unpack_from("<3f", b, vb["pos"] + k * vb["stride"]) for k in range(vb["count"])]
    idx = struct.unpack_from(f"<{ib['count']}H", b, ib["pos"])
    return P, [idx[k:k + 3] for k in range(0, len(idx), 3)]


def render(P, T, ax, depth, box):
    (u0, u1), (v0, v1) = box
    W, H = int((u1 - u0) * PX) + 1, int((v1 - v0) * PX) + 1
    img = [(40, 40, 40)] * (W * H)
    zb = [-1e9] * (W * H)
    light = (0.4, 0.5, 0.75)
    for t in T:
        a, b, c = (P[i] for i in t)
        e1 = [b[i] - a[i] for i in range(3)]
        e2 = [c[i] - a[i] for i in range(3)]
        n = [e1[1] * e2[2] - e1[2] * e2[1], e1[2] * e2[0] - e1[0] * e2[2], e1[0] * e2[1] - e1[1] * e2[0]]
        ln = math.sqrt(sum(x * x for x in n)) or 1
        lit = abs(sum(n[i] * light[i] for i in range(3))) / ln
        col = tuple(int(60 + 180 * lit) for _ in range(3))
        pts = [((p[ax] - u0) * PX, (v1 - p[1]) * PX, p[depth]) for p in (a, b, c)]
        xs, ys = [q[0] for q in pts], [q[1] for q in pts]
        den = (pts[1][1] - pts[2][1]) * (pts[0][0] - pts[2][0]) + (pts[2][0] - pts[1][0]) * (pts[0][1] - pts[2][1])
        if abs(den) < 1e-9:
            continue
        for y in range(max(0, int(min(ys))), min(H - 1, int(max(ys)) + 1) + 1):
            for x in range(max(0, int(min(xs))), min(W - 1, int(max(xs)) + 1) + 1):
                w0 = ((pts[1][1] - pts[2][1]) * (x - pts[2][0]) + (pts[2][0] - pts[1][0]) * (y - pts[2][1])) / den
                w1 = ((pts[2][1] - pts[0][1]) * (x - pts[2][0]) + (pts[0][0] - pts[2][0]) * (y - pts[2][1])) / den
                w2 = 1 - w0 - w1
                if w0 < 0 or w1 < 0 or w2 < 0:
                    continue
                z = w0 * pts[0][2] + w1 * pts[1][2] + w2 * pts[2][2]
                if z > zb[y * W + x]:
                    zb[y * W + x] = z
                    img[y * W + x] = col
    return W, H, img


def merge(paths):
    P, T = [], []
    for f in paths:
        p, t = load(f)
        T += [tuple(i + len(P) for i in tri) for tri in t]
        P += p
    return P, T


def main():
    """Arguments after <out.png>: Mesh_Z files, one panel pair each; '+' joins files into one panel (a+b+c)."""
    out, files = sys.argv[1], sys.argv[2:]
    meshes = [merge(f.split("+")) for f in files]
    allp = [p for P, _ in meshes for p in P]
    lo = [min(p[i] for p in allp) - 0.02 for i in range(3)]
    hi = [max(p[i] for p in allp) + 0.02 for i in range(3)]
    panels = []
    for P, T in meshes:
        panels.append(render(P, T, 0, 2, ((lo[0], hi[0]), (lo[1], hi[1]))))   # front: x across, nearest z wins
        panels.append(render(P, T, 2, 0, ((lo[2], hi[2]), (lo[1], hi[1]))))   # side: z across, from her left (+x)
    gap = 12
    W = sum(p[0] for p in panels) + gap * (len(panels) - 1)
    H = max(p[1] for p in panels)
    img = [(20, 20, 20)] * (W * H)
    x0 = 0
    for w, h, px in panels:
        for y in range(h):
            img[y * W + x0:y * W + x0 + w] = px[y * w:(y + 1) * w]
        x0 += w + gap
    write_png(out, W, H, img)
    print(f"wrote {out} ({W}x{H})")


if __name__ == "__main__":
    main()
