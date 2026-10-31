"""Hypothesis parser for Mesh_Z bodies (bff extract files). Prints the layout of each mesh and checks it.

body = 20 bytes ? | u32 nVB | nVB x (u32 count, u32 stride, u32 flags, count*stride bytes)
     | u32 nIB | nIB x (u32 count, u32 bytes_per_index, count*size bytes)
     | u32 nSub | nSub x 32-byte entry | rest
"""
import os, struct, sys

def parse(b):
    lh = struct.unpack_from("<I", b, 0x2C)[0]
    s = 64 + lh
    p = s + 20
    out = dict(link=lh, body=s, vbs=[], ibs=[], subs=[])
    nvb = struct.unpack_from("<I", b, p)[0]; p += 4
    if not 0 < nvb < 16:
        raise ValueError(f"nVB {nvb}")
    for _ in range(nvb):
        c, st, fl = struct.unpack_from("<III", b, p); p += 12
        out["vbs"].append(dict(count=c, stride=st, flags=fl, pos=p)); p += c * st
    nib = struct.unpack_from("<I", b, p)[0]; p += 4
    if not 0 < nib < 16:
        raise ValueError(f"nIB {nib} at {p - 4}")
    for _ in range(nib):
        c, sz = struct.unpack_from("<II", b, p); p += 8
        out["ibs"].append(dict(count=c, size=sz, pos=p)); p += c * sz
    nsub = struct.unpack_from("<I", b, p)[0]; p += 4
    if not 0 < nsub < 64:
        raise ValueError(f"nSub {nsub} at {p - 4}")
    for _ in range(nsub):
        e = b[p:p + 32]
        prim, x, vmin, vmax, nv, si, nt, z, bv = struct.unpack_from("<HHHHIIIII", e, 0)
        st, ff = struct.unpack_from("<HH", e, 28)
        out["subs"].append(dict(pos=p, prim=prim, x=x, vmin=vmin, vmax=vmax, nv=nv, start=si, ntri=nt, z=z, base=bv, stride=st, ff=ff))
        p += 32
    out["rest"] = len(b) - p
    out["rest_zero"] = not any(b[p:])
    out["rest_pos"] = p
    return out

def check(b, m):
    """Indices < vertex count; submesh triangles cover each IB; each submesh's indices lie in [base, base + nv - 1].
    One IB: all submeshes share it (start = first index). Several IBs: submesh k uses IB k (and the k-th geometry VB)."""
    msgs = []
    geo = [v for v in m["vbs"] if v["flags"] == 2]  # flags 258 = per-vertex morph data (8 entries per vertex)
    ibs = m["ibs"]
    if len(ibs) not in (1, len(m["subs"])):
        return [f"{len(ibs)} IBs for {len(m['subs'])} submeshes"]
    for k, s in enumerate(m["subs"]):
        ib = ibs[0] if len(ibs) == 1 else ibs[k]
        vb = geo[0] if len(ibs) == 1 else geo[k]
        if ib["size"] != 2:
            msgs.append(f"index size {ib['size']}")
            continue
        idx = struct.unpack_from(f"<{ib['count']}H", b, ib["pos"])
        if max(idx) >= vb["count"]:
            msgs.append(f"max index {max(idx)} >= vertices {vb['count']}")
        seg = idx[s["start"]:s["start"] + 3 * s["ntri"]]
        if not (s["vmin"] <= min(seg) and max(seg) <= s["vmin"] + s["nv"] - 1 and max(seg) <= s["vmax"]):
            msgs.append(f"sub {k} indices {min(seg)}-{max(seg)} outside [{s['vmin']}, {s['vmin'] + s['nv'] - 1}]")
    for k, ib in enumerate(ibs):
        tot = sum(s["ntri"] for j, s in enumerate(m["subs"]) if len(ibs) == 1 or j == k) * 3
        if tot != ib["count"]:
            msgs.append(f"IB {k}: submesh triangles*3 {tot} != index count {ib['count']}")
    return msgs

if __name__ == "__main__":
    d = sys.argv[1]
    labels = {}
    if len(sys.argv) > 2:
        for line in open(sys.argv[2]):
            p = line.split("\t")
            if p[0] != "label":
                labels[p[1]] = p[0]
    for f in sorted(os.listdir(d)):
        if not f.endswith(".Mesh_Z"):
            continue
        i = f.split(".")[0]
        b = open(os.path.join(d, f), "rb").read()
        try:
            m = parse(b)
        except Exception as e:
            print(labels.get(i, i), "PARSE FAIL", e); continue
        vbs = " ".join(f"{v['count']}x{v['stride']}/f{v['flags']}" for v in m["vbs"])
        ibs = " ".join(f"{x['count']}x{x['size']}" for x in m["ibs"])
        subs = " ".join(f"[p{s['prim']} x{s['x']} v{s['vmin']}-{s['vmax']} n{s['nv']} i{s['start']} t{s['ntri']} b{s['base']} s{s['stride']} {s['z']}]" for s in m["subs"])
        print(f"{labels.get(i, i)}\tlink {m['link']}\tVB {vbs}\tIB {ibs}\tsubs {len(m['subs'])} {subs}\trest {m['rest']} zero={m['rest_zero']}\t{'; '.join(check(b, m)) or 'OK'}")
