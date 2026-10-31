"""UV-space helpers for M14: read a mesh's UVs/triangles, rasterize triangles into a texel mask, find UV islands.

UV convention: as stored (v = 0 at the top row of the texture, DirectX style; notes\\mesh_format.md, uvmap.py).
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from meshfmt import parse  # noqa: E402


def mesh_uv(b):
    """(uvs, triangles) of a single-block Mesh_Z (bff extract form)."""
    m = parse(b)
    vb, ib = m["vbs"][0], m["ibs"][0]
    uv = [struct.unpack_from("<2e", b, vb["pos"] + k * vb["stride"] + 20) for k in range(vb["count"])]
    idx = struct.unpack_from(f"<{ib['count']}H", b, ib["pos"])
    tris = [idx[k:k + 3] for k in range(0, len(idx), 3) if len(set(idx[k:k + 3])) == 3]
    return uv, tris


def raster(pts, w, h, emit):
    """Call emit(x, y, b0, b1, b2) for every texel centre inside the triangle pts (3 points in texel units; texels outside
    0..w/0..h are passed through unwrapped)."""
    (x0, y0), (x1, y1), (x2, y2) = pts
    den = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
    if abs(den) < 1e-12:
        return
    for y in range(int(min(y0, y1, y2) // 1), int(max(y0, y1, y2) // 1) + 1):
        cy = y + 0.5
        for x in range(int(min(x0, x1, x2) // 1), int(max(x0, x1, x2) // 1) + 1):
            cx = x + 0.5
            b0 = ((y1 - y2) * (cx - x2) + (x2 - x1) * (cy - y2)) / den
            b1 = ((y2 - y0) * (cx - x2) + (x0 - x2) * (cy - y2)) / den
            b2 = 1 - b0 - b1
            if b0 >= -1e-6 and b1 >= -1e-6 and b2 >= -1e-6:
                emit(x, y, b0, b1, b2)


def coverage(uv, tris, w, h, dilate=0):
    """bytearray w*h: 1 where a triangle covers the texel (UVs wrapped into the texture), plus `dilate` texels around."""
    m = bytearray(w * h)

    def emit(x, y, *_):
        m[(y % h) * w + (x % w)] = 1
    for t in tris:
        raster([(uv[i][0] * w, uv[i][1] * h) for i in t], w, h, emit)
        for i in t:  # tiny triangles: at least their corners
            emit(int(uv[i][0] * w // 1), int(uv[i][1] * h // 1))
    for _ in range(dilate):
        n = bytearray(m)
        for y in range(h):
            for x in range(w):
                if m[y * w + x]:
                    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        n[((y + dy) % h) * w + (x + dx) % w] = 1
        m = n
    return m


def islands(uv, tris):
    """Connected triangle groups (shared vertex indices). Returns a list of (triangle index list, vertex index set)."""
    parent = list(range(len(uv)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    for t in tris:
        ra = find(t[0])
        for v in t[1:]:
            rb = find(v)
            if ra != rb:
                parent[rb] = ra
    groups = {}
    for k, t in enumerate(tris):
        groups.setdefault(find(t[0]), []).append(k)
    return [(ts, {v for k in ts for v in tris[k]}) for ts in groups.values()]
