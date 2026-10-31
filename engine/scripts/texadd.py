"""Texture edits as a colour difference (M12 mod package): store "edited - original" instead of the edited image.

make:  python -I texadd.py make <original.png> <edited.png> <out.texdelta>
       File: b"MLTD1" + u32 header length + JSON header (width, height, changed texels) + zlib(int16 LE R, G, B per
       texel, 0 where the edit did not change the texel).
apply: python -I texadd.py apply <target.png> <delta.texdelta> <out.png> [--report <json>]
       out = target + delta, clamped to 0..255, only on texels the delta changes (the others keep the target exactly).
       With target = the same original this gives the edited image back exactly; with a similar texture (CHP1's 1-02)
       it carries the colour changes over (the same as texdelta.py).
"""
import json
import os
import struct
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import texcodec as tc  # noqa: E402

MAGIC = b"MLTD1"


def make(orig, edit, out):
    w, h, A, _ = tc.png_read(orig)
    w2, h2, B, _ = tc.png_read(edit)
    if (w, h) != (w2, h2):
        sys.exit(f"sizes differ: {w}x{h} vs {w2}x{h2}")
    d = bytearray(6 * w * h)
    changed = 0
    for i in range(w * h):
        o = 4 * i
        if A[o:o + 3] != B[o:o + 3]:
            changed += 1
            struct.pack_into("<3h", d, 6 * i, B[o] - A[o], B[o + 1] - A[o + 1], B[o + 2] - A[o + 2])
    hdr = json.dumps(dict(format="texture delta 1", width=w, height=h, changed_texels=changed)).encode()
    open(out, "wb").write(MAGIC + struct.pack("<I", len(hdr)) + hdr + zlib.compress(bytes(d), 9))
    print(f"{changed} texels changed -> {out} ({os.path.getsize(out)} bytes)")


def load(path):
    raw = open(path, "rb").read()
    if raw[:5] != MAGIC:
        sys.exit(f"{path}: not a texture delta file")
    n = struct.unpack_from("<I", raw, 5)[0]
    hdr = json.loads(raw[9:9 + n])
    return hdr, zlib.decompress(raw[9 + n:])


def apply(target, delta, out, report=None):
    w, h, C, _ = tc.png_read(target)
    hdr, d = load(delta)
    if (w, h) != (hdr["width"], hdr["height"]):
        sys.exit(f"texture is {w}x{h}, the delta was made for {hdr['width']}x{hdr['height']}")
    O = bytearray(C)
    clipped = changed = 0
    for i in range(w * h):
        r, g, b = struct.unpack_from("<3h", d, 6 * i)
        if r == g == b == 0:
            continue
        changed += 1
        o = 4 * i
        for c, v in enumerate((r, g, b)):
            x = C[o + c] + v
            if x < 0 or x > 255:
                clipped += 1
            O[o + c] = max(0, min(255, x))
    tc.png_write(out, w, h, O)
    rep = dict(texels_changed=changed, channel_values_clipped=clipped)
    print(json.dumps(rep))
    if report:
        json.dump(rep, open(report, "w"))


def main():
    if sys.argv[1] == "make":
        make(*sys.argv[2:5])
    elif sys.argv[1] == "apply":
        apply(*sys.argv[2:5], report=sys.argv[sys.argv.index("--report") + 1] if "--report" in sys.argv else None)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
