"""Carry a texture edit to a similar texture of another archive (M10, CHP1's face 1-02): out = target + (edited - original),
per RGB channel, clamped to 0..255. Texels the edit did not touch keep the target's own colour exactly.
Usage: python -I texdelta.py <original.png> <edited.png> <target.png> <out.png> [--report <json>]
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import texcodec as tc  # noqa: E402


def main():
    orig, edit, target, out = sys.argv[1:5]
    w, h, A, _ = tc.png_read(orig)
    w2, h2, B, _ = tc.png_read(edit)
    w3, h3, C, _ = tc.png_read(target)
    if not (w, h) == (w2, h2) == (w3, h3):
        sys.exit(f"sizes differ: {w}x{h}, {w2}x{h2}, {w3}x{h3}")
    O = bytearray(C)
    changed = clipped = 0
    for i in range(0, len(A), 4):
        if A[i:i + 3] == B[i:i + 3]:
            continue
        changed += 1
        for c in range(3):
            v = C[i + c] + B[i + c] - A[i + c]
            if v < 0 or v > 255:
                clipped += 1
            O[i + c] = max(0, min(255, v))
        O[i + 3] = 255
    tc.png_write(out, w, h, O)
    rep = dict(size=[w, h], texels_changed=changed, channel_values_clipped=clipped,
               target_vs_original_rmse=round((sum((C[i] - A[i]) ** 2 for i in range(0, len(A), 4 * 7)) / (len(A) // 28)) ** 0.5, 2))
    print(json.dumps(rep))
    if "--report" in sys.argv:
        json.dump(rep, open(sys.argv[sys.argv.index("--report") + 1], "w"), indent=1)


if __name__ == "__main__":
    main()
