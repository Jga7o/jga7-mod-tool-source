"""Hair replacement helpers (M15): which meshes of an Amicia archive are hair, which one hosts the new hair, and the
texture copy for a host with its own textures (CHP1).

Hair (notes\\hair_inventory.md): a mesh whose material uses the hair shading model (Material_Z +300 = 64) AND whose
colour texture is the hair atlas B01 or CHP1's nape-hair texture 1-00. END's fur (E-14, E-29: hair shader, fur
texture END-09) is not hair. Expected counts (bald test, passed in game): 16 per archive, 11 in CHP1.
Host: the largest skinned piece (stride 48, one vertex block, one index buffer, one submesh) whose colour texture is
B01 (M02 / C2-20 / E-03 / U-48). If there is none (CHP1), the skinned hair piece with its own textures (C1-20, 1-00 +
1-10); then the new hair's B01 UVs need B01's image in 1-00 and B02's in 1-10 (texture mode "own", copy_bitmap).
Usage: python -I hairedit.py <archive name, e.g. P_AMICIA_CHP1>     (prints the hair set and the host)
       python -I hairedit.py copy <in.DPC> <out.DPC> --res <resources> --from <id> --to <id> --work <dir>
"""
import argparse
import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paths  # noqa: E402
import growdpc  # noqa: E402
from hosts import material_info, material_of  # noqa: E402
from meshfmt import parse  # noqa: E402

OUT = paths.OUT
B01, B02 = 68700188137309512, -6742760145741188495
NAPE = -300597880980788166            # CHP1 1-00 (nape hair colour); its normal is 1-10
HAIR_COLOURS = {B01, NAPE}
EXPECTED = {"P_AMICIA": 16, "P_AMICIA_CHP1": 11, "P_AMICIA_CHP2": 16, "P_AMICIA_END": 16, "P_AMICIA_UNIVERSITY": 16}
TSV = {"P_AMICIA": "P_AMICIA_parts.tsv", "P_AMICIA_CHP1": "P_AMICIA_CHP1_parts.tsv", "P_AMICIA_CHP2": "P_AMICIA_CHP2_parts.tsv",
       "P_AMICIA_END": "P_AMICIA_END_parts.tsv", "P_AMICIA_UNIVERSITY": "P_AMICIA_UNIVERSITY_parts.tsv"}


def labels(archive):
    p = os.path.join(paths.TABLES, "analysis", TSV[archive])
    return {int(r.split("\t")[1]): r.split("\t")[0] for r in open(p).read().split("\n")[1:] if r}


def hair_set(archive, res=None):
    """[(mesh id, label, vertices, importable, colour id, normal id)] of the archive's hair meshes; checks the count."""
    res = res or os.path.join(OUT, f"{archive}_unpacked", "resources")
    lab = labels(archive)
    out = []
    for mid, l in sorted(lab.items(), key=lambda kv: kv[1]):
        b = open(os.path.join(res, f"{mid}.Mesh_Z"), "rb").read()
        mats = []
        lh = struct.unpack_from("<I", b, 0x2C)[0]
        for k in range(64, 64 + lh - 7):
            v = struct.unpack_from("<q", b, k)[0]
            if v not in mats and os.path.isfile(os.path.join(res, f"{v}.Material_Z")):
                mats.append(v)
        infos = [material_info(x, res) for x in mats]
        if not any(i["model"] == 64 and i["textures"].get("colour") in HAIR_COLOURS for i in infos):
            continue
        m = parse(b)
        imp = len(m["vbs"]) == 1 and len(m["ibs"]) == 1 and len(m["subs"]) == 1 and m["vbs"][0]["stride"] == 48
        out.append((mid, l, sum(v["count"] for v in m["vbs"] if v["flags"] == 2), imp,
                    infos[0]["textures"].get("colour"), infos[0]["textures"].get("normal")))
    if archive in EXPECTED and len(out) != EXPECTED[archive]:
        raise ValueError(f"{archive}: {len(out)} hair meshes found, expected {EXPECTED[archive]} (the bald test's set)")
    return out


def pick_host(hs, want=None):
    """(mesh id, label, mode): mode "b01" = the host draws B01 (CUSTOMIZATION per outfit option); "own" = its own textures."""
    if want is not None:
        h = next((x for x in hs if want in (x[0], x[1])), None)
        if h is None or not h[3]:
            raise ValueError(f"host {want} is not an importable (skinned, single-submesh) hair piece")
        return h[0], h[1], "b01" if h[4] == B01 else "own"
    for mode, ok in (("b01", lambda x: x[4] == B01), ("own", lambda x: x[4] != B01)):
        c = [x for x in hs if x[3] and ok(x)]
        if c:
            h = max(c, key=lambda x: x[2])
            return h[0], h[1], mode
    raise ValueError("no importable hair piece to host the new hair")


def copy_bitmap(src_dpc, dst_dpc, res, frm, to, work):
    """Copy bitmap frm's body (BC blocks, all mips) into bitmap to (same size, format, mips; to keeps its id, link header
    and storage). LZ4 slots are recompressed; a slot that is too small is enlarged (growdpc.py). Returns a log line and
    whether the slot was enlarged."""
    a = open(os.path.join(res, f"{frm}.Bitmap_Z"), "rb").read()
    b = open(os.path.join(res, f"{to}.Bitmap_Z"), "rb").read()
    la, lb = struct.unpack_from("<I", a, 0x2C)[0], struct.unpack_from("<I", b, 0x2C)[0]
    ka, kb = a[64:64 + la], b[64:64 + lb]
    same = lambda k: (struct.unpack_from("<II", k, 10), k[42], k[49])
    if same(ka) != same(kb) or len(a) - la != len(b) - lb:
        raise ValueError(f"bitmaps {frm} and {to} differ in size, format or mips: {same(ka)} vs {same(kb)}")
    new = b[:64 + lb] + a[64 + la:]
    g = growdpc.write(src_dpc, dst_dpc, {to: dict(resource=growdpc.make_resource(src_dpc, to, new, work))})
    return f"bitmap {to} <- body of {frm}: sectors inserted {g['sectors_inserted']}", bool(g["sectors_inserted"])


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "copy":
        ap = argparse.ArgumentParser()
        ap.add_argument("cmd"); ap.add_argument("src"); ap.add_argument("dst")
        ap.add_argument("--res", required=True); ap.add_argument("--from", dest="frm", type=int, required=True)
        ap.add_argument("--to", type=int, required=True); ap.add_argument("--work", required=True)
        a = ap.parse_args()
        os.makedirs(a.work, exist_ok=True)
        line, grown = copy_bitmap(a.src, a.dst, a.res, a.frm, a.to, a.work)
        print(line + (" (slot enlarged)" if grown else ""))
        return
    arch = sys.argv[1]
    hs = hair_set(arch)
    mid, lab, mode = pick_host(hs)
    for x in hs:
        print(f"{x[1]:6s} {x[0]:>21d} {x[2]:5d} v {'skinned' if x[3] else 'rigid/multi'} colour {x[4]}")
    print(f"host: {lab} ({mid}), texture mode {mode}")
    print(json.dumps(dict(host=mid, label=lab, mode=mode, hide=[x[0] for x in hs if x[0] != mid])))


if __name__ == "__main__":
    main()
