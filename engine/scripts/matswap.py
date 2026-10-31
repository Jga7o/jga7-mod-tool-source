"""Swap the materials of two meshes in a DPC copy (session 55, the "skin material on a host" test).

A mesh names its material in its link header (twice, link offsets 138 and 182 for a one-material mesh; notes in
output\\analysis\\fullrun2\\material_refs.txt). The link header is stored uncompressed in front of the body, so the swap
is an in-place change of those 8-byte fields; nothing moves, nothing is recompressed. Both meshes must have exactly
one material. After the swap the host draws with the donor's material (its textures, shading model, extra maps) and the
donor (normally a hidden piece, e.g. the game's head with "replace_head") takes the host's old material, so every
material keeps exactly one user, as in the game.
Usage: python -I matswap.py <in.DPC> <out.DPC> --res <unpacked resources> --swap <host mesh id>=<donor mesh id>
Prints the offsets and ids; exit 1 on any doubt (a mesh with 0 or 2+ materials, an id not a Material_Z).
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fsx  # noqa: E402  (renames with a retry: Windows may hold a new file)
import dpc  # noqa: E402
from patch_hide import find  # noqa: E402


def material_fields(link, res):
    """[(offset in link, material id)] of the link header's 8-byte fields that are Material_Z ids of this archive."""
    out = []
    for k in range(0, len(link) - 7):
        v = struct.unpack_from("<q", link, k)[0]
        if os.path.isfile(os.path.join(res, f"{v}.Material_Z")):
            out.append((k, v))
    return out


def swap(path, host, donor, res):
    """Swap in place in the file at path (a copy): memory-mapped read, then 8-byte writes."""
    import mmap
    with open(path, "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as buf:
        pos = find(dpc.parse(buf), {host, donor})
        fields, mats = {}, {}
        for m in (host, donor):
            d, _ = pos[m]
            link = bytes(buf[d["link_header_pos"]:d["body_pos"]])
            f_ = material_fields(link, res)
            ids = {v for _, v in f_}
            if len(ids) != 1:
                raise ValueError(f"mesh {m}: {len(ids)} materials in its link header (exactly one supported)")
            fields[m], mats[m] = [(d["link_header_pos"] + k, k) for k, _ in f_], ids.pop()
    log = []
    with open(path, "r+b") as f:
        for m, other in ((host, donor), (donor, host)):
            for p, k in fields[m]:
                f.seek(p)
                f.write(struct.pack("<q", mats[other]))
            log.append(f"mesh {m}: material {mats[m]} -> {mats[other]} at link offsets {[k for _, k in fields[m]]}")
    return log, mats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--res", required=True)
    ap.add_argument("--swap", required=True)
    a = ap.parse_args()
    host, donor = (int(x) for x in a.swap.split("="))
    import shutil
    shutil.copyfile(a.src, a.dst + ".tmp")
    try:
        log, _ = swap(a.dst + ".tmp", host, donor, a.res)
    except ValueError as e:
        fsx.remove(a.dst + ".tmp")
        print("ERROR:", e)
        sys.exit(1)
    fsx.replace(a.dst + ".tmp", a.dst)
    for x in log:
        print(x)
    print("wrote", a.dst)


if __name__ == "__main__":
    main()
