"""Verify a face test file (M8: facepatch.py positions + texpatch.py textures) against the original.

Archive: layoutcheck (slot-gap flag accepted with --allow-slot-gaps); same size; outside the target slots only the
8-byte table-entry size of LZ4 targets may differ; resource header only its size fields; link header: meshes only the
bounds floats, bitmaps identical; LZ4 slot tail 0xFF; same decompressed size; bff extract: manifest identical and
exactly the target files differ.
Meshes (extracted): only bytes 0-19 (position, tangent, normal) of geometry vertices and the bounds floats differ; the
lip-sync block, indices, submeshes, name table, UVs, bones, weights, colors are identical; tangent/normal w bytes
unchanged; changed normals/tangents unit length and perpendicular (|N.T| < 0.1); equals the --mesh file if given.
Replaced meshes (--replace, blendimport.py): link header only bounds; the extracted file equals the importer's Mesh_Z
(link header + body). Hidden meshes (--hide, patch_hide.py): link header identical; only index bytes differ, all 0.
Material swap (--matswap <host>=<donor>, matswap.py, session 55): the host must be a --replace and the donor a --hide
mesh; in both link headers exactly the material fields may differ, and they must hold the other mesh's original material.
New-size textures (--bitmap-size <id>, texresize.py, session 56): with --bitmap and --resized for the same id; the
resource header may change its size and compression fields (written uncompressed), the link header only its size
fields (+10 width, +14 height, +26 data size, +42 mip count); mip 0 is compared with the PNG at the new size.
Bitmaps (extracted): same size/mips; mip 0 decoded vs the edited PNG: RMSE and max error (pass: RMSE < 2.5, or the
limit given as --bitmap <id>=<png>@<limit>: whole new textures such as an atlas carry the BC encoder's full error).
Usage: python -I verify_face.py <orig.DPC> <patched.DPC> <orig_unpacked_dir> <out_unpack_dir>
         [--mesh <id>[=<expected.Mesh_Z>]] ... [--bitmap <id>=<edited.png>] ... [--allow-slot-gaps] [--bff-diff]
Memory: both archives are memory-mapped and compared in 1 MB chunks; the texture error is accumulated texel by texel.
         [--replace <id>=<imported.Mesh_Z>] ... [--hide <id>] ... [--grown <id>] ... [--resized <id>] ...
Enlarged archives (M13, growdpc.py): --grown <id> = slot enlarged, resource bytes identical; --resized <id> = a
replaced resource whose size changed (with --replace, or a texture copied into a too-small LZ4 slot with --bitmap: M15). Then layoutcheck --gaps-ok must pass fully, the header, every
other slot, the block descriptions + map and the data groups are compared at their new positions, and the moved
fields (block_description_offset, resources_map_offset, data_offset, entry offsets, file_size,
total_decompressed_size) must have exactly their expected values.
"""
import argparse
import filecmp
import math
import mmap
import os
import shutil
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import dpc  # noqa: E402
import growdpc  # noqa: E402
import texcodec as tc  # noqa: E402
from facepatch import GameMesh  # noqa: E402
import dpcunpack  # noqa: E402

BFF = os.environ.get("MODLAB_BFF", r"C:\modlab\tools\bff-cli.exe")   # only for --bff-diff (research)
from patch_hide import find, ib_ranges, slot_limits  # noqa: E402

BOUNDS = (16, 96)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("orig"); ap.add_argument("patched"); ap.add_argument("orig_dir"); ap.add_argument("out_dir")
    ap.add_argument("--mesh", action="append", default=[])
    ap.add_argument("--bitmap", action="append", default=[])
    ap.add_argument("--allow-slot-gaps", action="store_true")
    ap.add_argument("--replace", action="append", default=[])
    ap.add_argument("--hide", action="append", default=[])
    ap.add_argument("--bff-diff", action="store_true", help="also print bff-cli's diff (uses more memory)")
    ap.add_argument("--grown", action="append", default=[], help="slot enlarged by growdpc.py, content unchanged")
    ap.add_argument("--resized", action="append", default=[], help="replaced resource that may change size (enlarged slot)")
    ap.add_argument("--matswap", action="append", default=[], help="host=donor: the two meshes' materials swapped (matswap.py)")
    ap.add_argument("--bitmap-size", action="append", default=[], help="a texture written at a new size (texresize.py)")
    a = ap.parse_args()
    meshes = {int(m.split("=")[0]): (m.split("=", 1)[1] if "=" in m else None) for m in a.mesh}
    bms = {int(b.split("=")[0]): b.split("=", 1)[1].rsplit("@", 1)[0] for b in a.bitmap}
    blim = {int(b.split("=")[0]): float(b.rsplit("@", 1)[1]) if "@" in b.split("=", 1)[1] else 2.5 for b in a.bitmap}
    repl = {int(m.split("=")[0]): m.split("=", 1)[1] for m in a.replace}
    hides = {int(x) for x in a.hide}
    grown = {int(x) for x in a.grown}
    resized = {int(x) for x in a.resized}
    targets = set(meshes) | set(bms) | set(repl) | hides
    bsized = {int(x) for x in a.bitmap_size}
    if bsized - (set(bms) & resized):
        sys.exit("--bitmap-size needs --bitmap and --resized for the same id")
    BSIZE_LINK = set(range(10, 18)) | set(range(26, 30)) | {42}
    mswap = {}   # mesh id -> [(link offset, expected new material id)]
    if a.matswap:
        from matswap import material_fields
        ro0 = os.path.join(a.orig_dir, "resources")
        for x in a.matswap:
            h, d = (int(v) for v in x.split("="))
            if h not in repl or d not in hides:
                sys.exit(f"--matswap {x}: the host must be a --replace and the donor a --hide mesh")
            f = {}
            for m in (h, d):
                q = open(os.path.join(ro0, f"{m}.Mesh_Z"), "rb").read()
                f[m] = material_fields(q[64:64 + struct.unpack_from("<I", q, 0x2C)[0]], ro0)
                if len({v for _, v in f[m]}) != 1:
                    sys.exit(f"--matswap {x}: mesh {m} does not have exactly one material")
            mswap[h] = [(k, f[d][0][1]) for k, _ in f[h]]
            mswap[d] = [(k, f[h][0][1]) for k, _ in f[d]]
    if resized - set(repl) - set(bms):
        sys.exit("--resized needs --replace or --bitmap for the same id")
    ok = True
    grow = set(grown) | resized
    fo, fp = open(a.orig, "rb"), open(a.patched, "rb")
    ob = mmap.mmap(fo.fileno(), 0, access=mmap.ACCESS_READ)
    nb = mmap.mmap(fp.fileno(), 0, access=mmap.ACCESS_READ)
    enlarged = len(ob) != len(nb)
    # an enlarged archive (growdpc.py): layoutcheck with --gaps-ok must pass completely
    r = subprocess.run([sys.executable, "-I", os.path.join(HERE, "layoutcheck.py"), a.patched] + (["--gaps-ok"] if enlarged else []),
                       capture_output=True, text=True)
    lines = r.stdout.strip().splitlines()
    print("layoutcheck:", lines[-1])
    bad = [x.strip() for x in lines if "[MISMATCH]" in x]
    if not lines[-1].startswith("ALL OK: True"):
        if a.allow_slot_gaps and not enlarged and bad and all("slot == pad(end)" in x for x in bad):
            print("  accepted (--allow-slot-gaps):", "; ".join(bad))
        elif bsized and bad and all("largest standalone resource" in x for x in bad):
            # session 57: a texture at a new size (texresize.py) is meant to exceed our "largest seen" rule; every
            # structural check still has to pass
            print("  accepted (--bitmap-size: a new-size texture is larger than any original resource):", "; ".join(bad))
        else:
            ok = False
    # low memory: the archives are memory-mapped (read on demand, never two whole copies in memory) and compared in
    # 1 MB chunks; only chunks that differ are looked at byte by byte
    print("file sizes:", len(ob), len(nb))
    if enlarged and not grow:
        print("VERIFY: FAIL")
        sys.exit(1)
    info, ninfo = dpc.parse(ob), dpc.parse(nb)
    found, nfound, lim, nlim = find(info, targets | grow), find(ninfo, targets | grow), slot_limits(info), slot_limits(ninfo)
    outside, CH = 0, 1 << 20
    if not enlarged:
        allowed = []
        for n, (d, e) in found.items():
            allowed += [(e["entry_pos"] + 20, e["entry_pos"] + 28), (d["pos"], lim[d["pos"]])]
            if n in resized:   # larger content that still fits its slot (M15): the decompressed sizes change too
                allowed.append((e["entry_pos"] + 28, e["entry_pos"] + 36))
        if resized:
            allowed.append((312, 320))     # header total decompressed size, checked exactly below
            dec_delta = sum(nfound[n][0]["link_header_size"] + nfound[n][0]["decompressed_body_size"]
                            - d["link_header_size"] - d["decompressed_body_size"] for n, (d, e) in found.items())
            tot = info["header"]["total_decompressed_size"] + dec_delta == ninfo["header"]["total_decompressed_size"]
            ent = all(nfound[n][1]["decompressed_size"] == nfound[n][0]["decompressed_body_size"] for n in resized if n in found)
            print(f"resized within their slots: header total decompressed size correct: {tot}; entry sizes consistent: {ent}")
            ok &= tot and ent
        for pos in range(0, len(ob), CH):
            x, y = ob[pos:pos + CH], nb[pos:pos + CH]
            if x == y or any(s <= pos and pos + len(x) <= t for s, t in allowed):
                continue
            for j in range(len(x)):
                if x[j] != y[j] and not any(s <= pos + j < t for s, t in allowed):
                    outside += 1
    else:
        # region by region (header | slots | block descriptions + map | data groups), each at its own new position;
        # the moved-offset fields are checked against their expected values instead of byte by byte
        ho, hn, ro, rn = info["header"], ninfo["header"], info["blocks"][0]["resources"], ninfo["blocks"][0]["resources"]
        k = hn["block_description_offset"] - ho["block_description_offset"]
        dec_delta = sum(nfound[n][0]["link_header_size"] + nfound[n][0]["decompressed_body_size"]
                        - d["link_header_size"] - d["decompressed_body_size"] for n, (d, e) in found.items())
        ms = ho["block_description_offset"] * dpc.SECTOR
        mo = info["blocks"][0]["resources_map_offset"] * dpc.SECTOR - ms
        skip = {"header": [(264, 268), (304, 320)], "map": [(28, 32), (mo + 4, mo + 8)]}
        for e in ro["resources"]:
            skip["map"].append((e["entry_pos"] - ms + 16, e["entry_pos"] - ms + 20))
            if dpc.s64(e["name"]) in targets | grow:
                skip["map"].append((e["entry_pos"] - ms + 20, e["entry_pos"] - ms + 36))
        moved = dict(bdo=hn["block_description_offset"] == ho["block_description_offset"] + k,
                     map=ninfo["blocks"][0]["resources_map_offset"] == info["blocks"][0]["resources_map_offset"] + k,
                     data_offset=rn["data_offset"] == ro["data_offset"] + k,
                     file_size=hn["file_size"] == len(nb) == len(ob) + k * dpc.SECTOR,
                     total_decompressed=hn["total_decompressed_size"] == ho["total_decompressed_size"] + dec_delta)
        diff_len = []
        for kind, n, s0, t0, s1, t1 in growdpc.offset_map(info, ninfo):
            if kind == "slot" and n in targets | grow:
                continue
            if t0 - s0 != t1 - s1:
                diff_len.append(n)
                continue
            sk = skip.get(kind, [])
            for pos in range(0, t0 - s0, CH):
                x, y = ob[s0 + pos:min(t0, s0 + pos + CH)], nb[s1 + pos:min(t1, s1 + pos + CH)]
                if x == y:
                    continue
                for j in range(len(x)):
                    if x[j] != y[j] and not any(s <= pos + j < t for s, t in sk):
                        outside += 1
        print(f"enlarged archive: {k} sectors inserted; moved fields correct: {all(moved.values())} {moved}; "
              f"regions with a different length (other than the enlarged slots): {diff_len}")
        ok &= all(moved.values()) and not diff_len
    print(f"bytes differing outside the target slots: {outside}")
    ok &= not outside
    for n, (d, e) in found.items():
        nd = nfound[n][0]
        same_size = n not in resized
        hdr = [i for i in range(48) if ob[d["pos"] + i] != nb[nd["pos"] + i]]
        hdr_ok = all(24 <= i < 28 or 40 <= i < 44 or (not same_size and 32 <= i < 36) or (n in bsized and 24 <= i < 44) for i in hdr)
        if d["link_header_size"] == nd["link_header_size"]:
            link = [i for i in range(d["link_header_size"]) if ob[d["link_header_pos"] + i] != nb[nd["link_header_pos"] + i]]
        else:
            link = list(range(max(d["link_header_size"], nd["link_header_size"])))
        msw = {i for k, _ in mswap.get(n, []) for i in range(k, k + 8)}
        msw |= BSIZE_LINK if n in bsized else set()
        link_ok = all(BOUNDS[0] <= i < BOUNDS[1] or i in msw for i in link) if n in meshes or n in repl else all(i in msw for i in link)
        if n in mswap:   # the swapped material fields hold exactly the other mesh's material
            link_ok &= all(struct.unpack_from("<q", nb, nd["link_header_pos"] + k)[0] == v for k, v in mswap[n])
            print(f"  {n}: material swapped to {mswap[n][0][1]} at link offsets {[k for k, _ in mswap[n]]}: {link_ok}")
        tail_ok = set(nb[nd["end"]:nlim[nd["pos"]]]) <= {0xFF} if (d["compression_type"] or n in grow) else True
        if n in grown:   # enlarged slot, content unchanged
            same = ob[d["pos"]:d["end"]] == nb[nd["pos"]:nd["end"]]
            print(f"  {n}: slot {(lim[d['pos']] - d['pos']) // dpc.SECTOR} -> {(nlim[nd['pos']] - nd['pos']) // dpc.SECTOR} sectors; "
                  f"resource bytes identical: {same}; rest of the slot 0xFF: {tail_ok}")
            ok &= same and tail_ok
            continue
        print(f"  {n}: {'raw' if d['compression_type'] == 0 else 'lz4'} {d['body_len']} -> {nd['body_len']}; header only size "
              f"fields: {hdr_ok}; link header {'only bounds' if n in meshes or n in repl else 'identical'}: {link_ok} ({len(link)} bytes); "
              f"slot tail ok: {tail_ok}; dec size {d['decompressed_body_size']} -> {nd['decompressed_body_size']}"
              + (f"; slot {(lim[d['pos']] - d['pos']) // dpc.SECTOR} -> {(nlim[nd['pos']] - nd['pos']) // dpc.SECTOR} sectors"
                 if n in resized else ""))
        ok &= hdr_ok and link_ok and tail_ok and (d["decompressed_body_size"] == nd["decompressed_body_size"] or not same_size)
    ob.close(), nb.close(), fo.close(), fp.close()
    if os.path.exists(a.out_dir):
        shutil.rmtree(a.out_dir)
    try:   # M17 session 2: our own unpacker (byte-identical to bff-cli extract on every original and test archive)
        nres, err = dpcunpack.unpack(a.patched, a.out_dir), ""
    except (SystemExit, Exception) as e:
        nres, err = 0, str(e)
    print(f"extract (dpcunpack): {nres} resources" + (f"; FAILED: {err[:300]}" if err else ""))
    ok &= not err
    if err or not os.path.isfile(os.path.join(a.out_dir, "manifest.json")):
        print("VERIFY: FAIL")
        sys.exit(1)
    same_manifest = filecmp.cmp(os.path.join(a.orig_dir, "manifest.json"), os.path.join(a.out_dir, "manifest.json"), shallow=False)
    if not same_manifest and bsized:   # session 57: new-size textures are stored uncompressed: only their "compress" flags may differ
        import json
        mo = json.load(open(os.path.join(a.orig_dir, "manifest.json")))
        mn = json.load(open(os.path.join(a.out_dir, "manifest.json")))

        def strip(m):
            def walk(x):
                if isinstance(x, dict):
                    return {k: walk(v) for k, v in x.items() if not (k == "compress" and x.get("name") in bsized)}
                if isinstance(x, list):
                    return [walk(v) for v in x]
                return x
            return walk(m)
        same_manifest = strip(mo) == strip(mn)
        print(f"  manifest: identical except the compress flag of the new-size textures {sorted(bsized)}: {same_manifest}")
    ro, rn = (os.path.join(x, "resources") for x in (a.orig_dir, a.out_dir))
    fo, fn = sorted(os.listdir(ro)), sorted(os.listdir(rn))
    dfiles = {f for f in fo if f in fn and not filecmp.cmp(os.path.join(ro, f), os.path.join(rn, f), shallow=False)}
    want = {f"{n}.Mesh_Z" for n in set(meshes) | set(repl) | hides} | {f"{n}.Bitmap_Z" for n in bms}
    print(f"manifest identical: {same_manifest}; files {len(fo)} vs {len(fn)}; differing extracted files: {sorted(dfiles)}")
    ok &= same_manifest and fo == fn and dfiles == want
    for n, expect in meshes.items():
        o = open(os.path.join(ro, f"{n}.Mesh_Z"), "rb").read()
        q = open(os.path.join(rn, f"{n}.Mesh_Z"), "rb").read()
        ga, gb = GameMesh(o), GameMesh(q)
        allowed = set(range(64 + BOUNDS[0], 64 + BOUNDS[1]))
        for vb in ga.geo:
            for i in range(vb["count"]):
                allowed |= set(range(vb["pos"] + 48 * i, vb["pos"] + 48 * i + 20))
        out = [i for i in range(len(o)) if o[i] != q[i] and i not in allowed]
        changed = [k for k in ga.P if ga.N[k] != gb.N[k] or ga.T[k] != gb.T[k]]
        wb = sum(ga.N[k][3] != gb.N[k][3] or ga.T[k][3] != gb.T[k][3] for k in ga.P)
        unit_bad = sum(not 0.97 < math.sqrt(sum(x * x for x in v[:3])) / 127 < 1.03 for k in changed for v in (gb.N[k], gb.T[k]))
        perp = max((abs(sum(x * y for x, y in zip(gb.N[k][:3], gb.T[k][:3]))) / 127 / 127 for k in changed), default=0)
        moved = [k for k in ga.P if ga.P[k] != gb.P[k]]
        mx = max((math.dist(ga.P[k], gb.P[k]) for k in moved), default=0)
        same_file = expect is None or open(expect, "rb").read()[64:] == q[64:]  # link header + body (not bff's file header)
        good = not out and not wb and not unit_bad and perp < 0.1 and same_file and len(o) == len(q)
        print(f"  mesh {n}: {len(moved)} vertices moved (max {mx * 1000:.2f} mm), {len(changed)} normals/tangents changed, "
              f"bytes changed elsewhere {len(out)}, w bytes changed {wb}, non-unit {unit_bad}, max |N.T| {perp:.3f}, "
              f"lip-sync block identical {all(o[v['pos']:v['pos'] + v['count'] * 20] == q[v['pos']:v['pos'] + v['count'] * 20] for v in ga.morph)}, "
              f"equals facepatch output: {same_file} -> {'OK' if good else 'FAIL'}")
        ok &= good
    for n, expect in repl.items():
        q = open(os.path.join(rn, f"{n}.Mesh_Z"), "rb").read()
        exp = bytearray(open(expect, "rb").read())
        for k, v in mswap.get(n, []):   # the importer wrote the host's own material; matswap changed it afterwards
            exp[64 + k:64 + k + 8] = struct.pack("<q", v)
        good = bytes(exp[64:]) == q[64:]
        print(f"  replaced mesh {n}: equals the importer output{' (+ material swap)' if n in mswap else ''}: {good} -> {'OK' if good else 'FAIL'}")
        ok &= good
    for n in sorted(hides):
        o = open(os.path.join(ro, f"{n}.Mesh_Z"), "rb").read()
        q = open(os.path.join(rn, f"{n}.Mesh_Z"), "rb").read()
        ranges, _ = ib_ranges(o)
        msw = {64 + i for k, _ in mswap.get(n, []) for i in range(k, k + 8)}
        out = [i for i in range(len(o)) if o[i] != q[i] and not any(s <= i < t for s, t in ranges) and i not in msw]
        zero = all(not any(q[s:t]) for s, t in ranges) and all(struct.unpack_from("<q", q, 64 + k)[0] == v for k, v in mswap.get(n, []))
        good = len(o) == len(q) and not out and zero
        print(f"  hidden mesh {n}: bytes changed outside the index buffers {len(out)}, all indices 0: {zero} -> {'OK' if good else 'FAIL'}")
        ok &= good
    for n, png in bms.items():
        q = open(os.path.join(rn, f"{n}.Bitmap_Z"), "rb").read()
        o = open(os.path.join(ro, f"{n}.Bitmap_Z"), "rb").read()
        lh = struct.unpack_from("<I", q, 0x2C)[0]
        link = q[64:64 + lh]
        w, h = struct.unpack_from("<II", link, 10)
        fmt = {0x1b: "BC1", 0x1d: "BC3"}[link[49]]
        bs = tc.block_size(fmt)
        n0 = max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * bs
        dec = tc.decode_bc(q[64 + lh:64 + lh + n0], fmt, w, h)
        pw, ph, img, has_alpha = tc.png_read(png)
        ch = 4 if fmt == "BC3" and has_alpha else 3
        ss, cnt, mx = 0, 0, 0   # streamed (no list of 12 million differences)
        for i in range(0, len(img), 4):
            for c in range(ch):
                dd = abs(dec[i + c] - img[i + c])
                ss += dd * dd
                cnt += 1
                if dd > mx:
                    mx = dd
        rm = math.sqrt(ss / cnt)
        if n in bsized:   # new size: only the size fields of the link header may differ
            lo = o[64:64 + struct.unpack_from("<I", o, 0x2C)[0]]
            same_layout = len(lo) == lh and all(lo[i] == link[i] or i in BSIZE_LINK for i in range(lh))                 and len(q) - 64 - lh == struct.unpack_from("<I", link, 26)[0] + 4
        else:
            same_layout = len(o) == len(q) and o[:64 + lh] == q[:64 + lh]
        good = (pw, ph) == (w, h) and rm < blim[n] and same_layout
        print(f"  bitmap {n}: {w}x{h} {fmt}; mip 0 vs edited PNG RMSE {rm:.3f} (limit {blim[n]}), max {mx}; header/size same: {same_layout} "
              f"-> {'OK' if good else 'FAIL'}")
        ok &= good
    if a.bff_diff and os.path.isfile(BFF):  # research only (bff-cli loads both archives); off by default
        r = subprocess.run([BFF, "diff", a.orig, a.patched], capture_output=True, text=True)
        print("bff diff:", (r.stdout + r.stderr).strip()[:600])
    print("VERIFY:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
