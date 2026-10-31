"""Export a BC1/BC3 Bitmap_Z of a DPC as a full-size PNG, or write an edited PNG back into it (M8 face kit).

export: python -I texpatch.py export <in.DPC> --id <bitmap id> --png <out.png> [--work <dir>]
        mip 0 decoded; BC1 -> RGB PNG, BC3 -> RGBA PNG (alpha kept).
apply:  python -I texpatch.py apply <in.DPC> <out.DPC> --id <bitmap id> --png <edited.png> [--work <dir>]
        [--reencode-all] [--report <json>]
- The PNG must have the texture's exact size (8-bit; RGB or RGBA; see texcodec.png_read).
- Same format, size and mip count as before. Mip 0 blocks whose pixels equal the decoded original are kept byte for
  byte; changed blocks are encoded again (texcodec). Mips 1.. are regenerated (2x2 box filter) only where the region
  below them changed; elsewhere the game's own mip blocks stay. --reencode-all encodes every block of every mip
  (used to measure the encoder's error).
- Alpha: BC3 takes the PNG's alpha; if the PNG has no alpha (all 255) the texture's own alpha is kept, unless --opaque
  (M16: alpha 255 everywhere, for a BC3 host used for an opaque part). BC1 textures
  without transparent pixels stay opaque (a PNG alpha is ignored, with a warning).
- The body is written raw, or recompressed into its slot via put_mesh.put (lz4hc.py since M17 session 2; bff-cli is
  no longer used); it must fit. If the result with the game's own blocks kept does not fit, every block is encoded
  again (our blocks compress better; B05: 2.12 MB, fits). --allow-lz4hc skips that pre-check (kept for old manifests).
"""
import argparse
import json
import math
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dpc  # noqa: E402
import texcodec as tc  # noqa: E402
from bitmaps import decode  # noqa: E402
from bmpdump import body, bitmaps  # noqa: E402

import paths  # noqa: E402
OUT = paths.OUT


def locate(buf, info, bid):
    d = next((x for x in bitmaps(info) if dpc.s64(x["name"]) == bid), None)
    if d is None:
        raise ValueError(f"bitmap {bid} not found as a standalone Bitmap_Z")
    bi = decode(bytes(buf), d)
    if bi["fmt_label"] not in ("BC1", "BC3") or bi["kind"] != 0 or bi["faces"] != 1:
        raise ValueError(f"bitmap {bid}: {bi['fmt_label']} kind {bi['kind']} faces {bi['faces']} not supported (BC1/BC3 2D only)")
    return d, bi


def levels(bi):
    """[(offset, w, h, bytes)] per mip."""
    out, off, w, h = [], 0, bi["w"], bi["h"]
    bs = tc.block_size(bi["fmt_label"])
    for _ in range(bi["mips"]):
        n = max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * bs
        out.append((off, w, h, n))
        off += n
        w, h = max(1, w // 2), max(1, h // 2)
    return out


def export(a):
    buf = open(a.dpc, "rb").read()
    info = dpc.parse(buf)
    d, bi = locate(buf, info, a.id)
    raw = body(buf, d, a.work)
    img = tc.decode_bc(raw, bi["fmt_label"], bi["w"], bi["h"])
    tc.png_write(a.png, bi["w"], bi["h"], img, alpha=bi["fmt_label"] == "BC3")
    print(f"{a.id}: {bi['w']}x{bi['h']} {bi['fmt_label']} mips {bi['mips']} -> {a.png}")


def build(raw, bi, png, reencode_all=False, opaque=False):
    """New pixel bytes (all mips + 4 zero bytes) and a report."""
    fmt = bi["fmt_label"]
    bs = tc.block_size(fmt)
    lv = levels(bi)
    w, h, src, has_alpha = tc.png_read(png)
    if (w, h) != (bi["w"], bi["h"]):
        raise ValueError(f"PNG is {w}x{h}, the texture is {bi['w']}x{bi['h']}: same size required")
    warns = []
    orig = [tc.decode_bc(raw[o:o + n], fmt, lw, lh) for o, lw, lh, n in lv]
    orig_alpha = any(x != 255 for x in orig[0][3::4])
    keep_alpha = False
    if fmt == "BC1" and not orig_alpha:
        if has_alpha:
            warns.append("the PNG has transparency but the texture is opaque BC1: alpha ignored")
        src[3::4] = b"\xff" * (w * h)
    elif opaque:
        src[3::4] = b"\xff" * (w * h)
    elif not has_alpha and orig_alpha:
        keep_alpha = True
        warns.append("the PNG has no alpha: the texture's own alpha is kept")
    if keep_alpha:
        src[3::4] = orig[0][3::4]
    out = bytearray(raw[:lv[-1][0] + lv[-1][3]])
    work = src
    dirty_prev = None
    rep = dict(levels=[])
    for k, (o, lw, lh, n) in enumerate(lv):
        bw, bh = max(1, (lw + 3) // 4), max(1, (lh + 3) // 4)
        if k == 0:
            cur = work
            cmp = (lambda p, q: p == q) if (fmt == "BC3" or orig_alpha) else (lambda p, q: p[:3] == q[:3])
            dirty = set()
            for by in range(bh):
                for bx in range(bw):
                    if reencode_all or any(not cmp(p, q) for p, q in zip(tc.block_pixels(cur, lw, lh, bx, by),
                                                                         tc.block_pixels(orig[0], lw, lh, bx, by))):
                        dirty.add((bx, by))
        else:
            pw, ph = lv[k - 1][1], lv[k - 1][2]
            cur = bytearray(orig[k])
            dirty = {(bx, by) for bx in range(bw) for by in range(bh)} if reencode_all else \
                {(bx, by) for bx in range(bw) for by in range(bh)
                 if any((2 * bx + i, 2 * by + j) in dirty_prev for i in (0, 1) for j in (0, 1))}
            for bx, by in dirty:  # box filter from the previous level's working pixels
                for y in range(by * 4, min(lh, by * 4 + 4)):
                    for x in range(bx * 4, min(lw, bx * 4 + 4)):
                        q = 4 * (y * lw + x)
                        xs = (min(pw - 1, 2 * x), min(pw - 1, 2 * x + 1))
                        ys = (min(ph - 1, 2 * y), min(ph - 1, 2 * y + 1))
                        for c in range(4):
                            cur[q + c] = (sum(prev[4 * (yy * pw + xx) + c] for xx in xs for yy in ys) + 2) >> 2
                        if keep_alpha or (fmt == "BC1" and not orig_alpha):
                            cur[q + 3] = orig[k][q + 3]
        sq = cnt = 0
        for bx, by in dirty:
            px = tc.block_pixels(cur, lw, lh, bx, by)
            blk = tc.encode_block(px, fmt)
            p = o + (by * bw + bx) * bs
            out[p:p + bs] = blk
            dec = tc.decode_block(blk, fmt)
            for i in range(16):
                x, y = bx * 4 + i % 4, by * 4 + i // 4
                if x < lw and y < lh:
                    sq += sum((px[i][c] - dec[i][c]) ** 2 for c in range(4 if fmt == "BC3" else 3))
                    cnt += 4 if fmt == "BC3" else 3
        rep["levels"].append(dict(mip=k, size=f"{lw}x{lh}", blocks=bw * bh, encoded=len(dirty),
                                  rmse_vs_source=round(math.sqrt(sq / cnt), 3) if cnt else 0.0))
        prev, dirty_prev = cur, dirty
    rep["warnings"] = warns
    new = bytes(out) + raw[len(out):]
    if len(new) != len(raw):
        raise ValueError("size changed")
    return new, rep


def compare(raw, new, bi):
    """Per mip: RMSE and max abs error of the decoded new texture against the decoded original."""
    fmt = bi["fmt_label"]
    res = []
    for k, (o, lw, lh, n) in enumerate(levels(bi)):
        a = tc.decode_bc(raw[o:o + n], fmt, lw, lh)
        b = tc.decode_bc(new[o:o + n], fmt, lw, lh)
        ch = 4 if fmt == "BC3" else 3
        d = [abs(a[i] - b[i]) for i in range(len(a)) if i % 4 < ch]
        res.append(dict(mip=k, rmse=round(math.sqrt(sum(x * x for x in d) / len(d)), 3), max=max(d),
                        identical_bytes=raw[o:o + n] == new[o:o + n]))
    return res


def apply(a):
    buf = bytearray(open(a.dpc, "rb").read())
    info = dpc.parse(bytes(buf))
    d, bi = locate(buf, info, a.id)
    raw = body(buf, d, a.work)
    new, rep = build(raw, bi, a.png, a.reencode_all, a.opaque)
    if d["compression_type"] and not a.reencode_all and not a.allow_lz4hc and new != raw:
        # the game's own BC blocks may no longer fit the slot after the edit: blocks from our encoder compress better,
        # so re-encode everything when they do not fit (M17 session 2: compressed with lz4hc, as put_mesh does)
        from patch_bitmaps import lz4_body
        from patch_hide import slot_limits
        lim = slot_limits(info)[d["pos"]]
        if d["body_pos"] + len(lz4_body(new, a.work, str(a.id))) > lim:
            print("does not fit with the game's own blocks kept: re-encoding every block")
            new, rep = build(raw, bi, a.png, True, a.opaque)
            rep["reencoded_all_to_fit"] = True
    rep["vs_original"] = compare(raw, new, bi)
    rep["identical"] = new == raw
    for w in rep["warnings"]:
        print("WARNING:", w)
    for L, c in zip(rep["levels"], rep["vs_original"]):
        print(f"mip {L['mip']:2d} {L['size']:>9s}: {L['encoded']:6d}/{L['blocks']} blocks encoded, "
              f"RMSE vs source {L['rmse_vs_source']:.3f}; vs original RMSE {c['rmse']:.3f} max {c['max']}"
              f"{' (bytes identical)' if c['identical_bytes'] else ''}")
    from put_mesh import put
    lh = d["body_pos"] - d["link_header_pos"]
    fake = bytearray(64) + bytes(buf[d["link_header_pos"]:d["body_pos"]]) + new
    struct.pack_into("<I", fake, 0x2C, lh)
    try:
        rep["put"] = put(buf, info, a.id, bytes(fake), a.work)
    except ValueError as e:   # session 55: a body that does not fit its LZ4 slot even with lz4hc -> enlarge the slot
        if not (a.allow_grow and "does not fit" in str(e)):
            raise
        import growdpc
        del buf
        g = growdpc.write(a.dpc, a.out, {a.id: dict(resource=growdpc.make_resource(a.dpc, a.id, bytes(fake), a.work))})
        rep["put"] = f"slot enlarged by {g['sectors_inserted']} sectors (did not fit: {e})"
        rep["grown"] = g["sectors_inserted"]
        print("put:", rep["put"])
        print("wrote", a.out)
        if a.report:
            json.dump(rep, open(a.report, "w"), indent=1)
        return
    print("put:", rep["put"])
    open(a.out, "wb").write(buf)
    print("wrote", a.out)
    if a.report:
        json.dump(rep, open(a.report, "w"), indent=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["export", "apply"])
    ap.add_argument("dpc")
    ap.add_argument("out", nargs="?")
    ap.add_argument("--id", type=int, required=True)
    ap.add_argument("--png", required=True)
    ap.add_argument("--work", default=os.path.join(OUT, "import_work", "texpatch"))
    ap.add_argument("--reencode-all", action="store_true")
    ap.add_argument("--opaque", action="store_true", help="M16: write alpha 255 everywhere (a BC3 host whose own alpha cuts holes)")
    ap.add_argument("--allow-lz4hc", action="store_true", help="skip the re-encode pre-check (kept for old manifests; lz4hc is always the compressor)")
    ap.add_argument("--allow-grow", action="store_true", help="session 55: enlarge the slot (growdpc) when the body does not fit")
    ap.add_argument("--report")
    a = ap.parse_args()
    os.makedirs(a.work, exist_ok=True)
    if a.mode == "export":
        export(a)
    else:
        if not a.out:
            sys.exit("apply needs <out.DPC>")
        apply(a)


if __name__ == "__main__":
    main()
