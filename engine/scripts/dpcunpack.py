"""M17 session 2: unpack a DPC into the same files as bff-cli's `extract` (plan §4; replaces it). Stdlib only.
Output: <dir>\\manifest.json and <dir>\\resources\\<name>.<Class> for every standalone resource, then every embedded
data resource, each file = a 64-byte header + the link header + the body decompressed (lz4dec.py).
  header: b"BFF0" + PREFIX (12 bytes, the same in every file seen), the record's class / name / link name (24 bytes),
          u32 link + body size, u32 link header size, u32 body size, 12 zero bytes
  manifest: version, platform, version_xple (header u0), bigfile_type, blocks [{offset (working buffer offset),
          resources [{name, compress (an LZ4 body present)}]}] in file order; json with 2-space indent, as bff-cli writes it
Class names: TABLES\\analysis\\class_names.json (the 20 classes of Amicia's files).
Every file is written under a temporary name and renamed; an existing <dir> is refused unless --force.
Usage: python -I dpcunpack.py <file.DPC> <dir> [--force]"""
import json
import mmap
import os
import shutil
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import dpc  # noqa: E402
import lz4dec  # noqa: E402
import paths  # noqa: E402

PREFIX = bytes.fromhex("0a00000002008000 5c001300".replace(" ", ""))


def rmtree(d, tries=10):
    """shutil.rmtree with a short retry: Windows may still hold a just-written file (e.g. the virus scanner)."""
    import time
    for k in range(tries):
        try:
            shutil.rmtree(d)
            return
        except FileNotFoundError:
            return
        except PermissionError:
            if k == tries - 1:
                raise
            time.sleep(0.5 * (k + 1))


def class_names():
    return {int(k): v for k, v in json.load(open(os.path.join(paths.TABLES, "analysis", "class_names.json"))).items()}


def unpack(src, dst, force=False):
    if os.path.exists(dst):
        if not force:
            raise SystemExit(f"{dst} exists (use --force)")
        rmtree(dst)
    names = class_names()
    tmp = dst + ".tmp"
    if os.path.exists(tmp):
        rmtree(tmp)
    os.makedirs(os.path.join(tmp, "resources"))
    with open(src, "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as buf:   # mapped: low memory
        info = dpc.parse(buf)
        h = info["header"]
        man = dict(version=h["version"], platform="PC", version_xple=h["u0"],
                   bigfile_type="Normal" if h["is_not_rtc"] else "Rtc", blocks=[])
        count = 0
        for bd in info["blocks"]:
            res = bd["resources"]
            recs = [e["data"] for e in res["resources"]] + [d for g in res["datas"] for d in g]
            man["blocks"].append(dict(offset=res["working_buffer_offset"],
                                      resources=[dict(name=dpc.s64(d["name"]), compress=d["compression_type"] == 2 and d["compressed_body_size"] > 0)
                                                 for d in recs]))
            for d in recs:
                cls = dpc.s64(d["class_name"])
                if cls not in names:
                    raise SystemExit(f"unknown class {cls} (resource {dpc.s64(d['name'])}): add it to class_names.json")
                link = bytes(buf[d["link_header_pos"]:d["body_pos"]])
                body = bytes(buf[d["body_pos"]:d["end"]])
                if d["compression_type"] == 2:
                    if body:   # xple stubs: LZ4 flag but no body here (it sits elsewhere in the archive)
                        body = lz4dec.dpc_body(body)
                elif d["compression_type"] != 0:
                    raise SystemExit(f"resource {dpc.s64(d['name'])}: unknown compression {d['compression_type']}")
                head = b"BFF0" + PREFIX + bytes(buf[d["pos"]:d["pos"] + 24]) + struct.pack(
                    "<III", len(link) + len(body), len(link), len(body)) + b"\0" * 12
                p = os.path.join(tmp, "resources", f"{dpc.s64(d['name'])}.{names[cls]}")
                with open(p, "wb") as o:
                    o.write(head + link + body)
                count += 1
    with open(os.path.join(tmp, "manifest.json"), "w", newline="\n") as o:
        json.dump(man, o, indent=2)
    import time
    for k in range(10):   # the rename may meet the same short Windows lock as rmtree
        try:
            os.replace(tmp, dst)
            break
        except PermissionError:
            if k == 9:
                raise
            time.sleep(0.5 * (k + 1))
    return count


if __name__ == "__main__":
    n = unpack(sys.argv[1], sys.argv[2], "--force" in sys.argv)
    print(f"dpcunpack: {n} resources -> {sys.argv[2]}")
