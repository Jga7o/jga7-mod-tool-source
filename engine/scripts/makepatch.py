"""M17 session 3: make a mod's patch (changes.bin, plan §5.2) from the built game files and the originals. Stdlib only.
Format: applypatch.py. Per built file, walked in file order:
  - every resource record (standalone and embedded): matched with the original's record of the same class / name /
    link (in order of appearance); identical bytes -> one copy op;
  - otherwise the 48-byte record header is new bytes, the link header and an uncompressed body are copied from the
    original where equal (4 KB, then 64-byte blocks at the same offset) and new bytes elsewhere;
  - an LZ4 body is stored as the difference of the DECOMPRESSED bodies, recompressed by the applier with lz4hc. Only
    when lz4hc gives exactly the built bytes (a body made by another compressor does not): then the compressed body
    is stored as new bytes ("not recompressible", counted in the report);
  - the bytes between records (tables, padding) are compared with the original at the same offset.
New bytes go to blobs, stored once per content (SHA-256): a resource changed the same way in five files is stored once.
Self-test (§5.2): the finished patch is applied with applypatch.py to the originals; every rebuilt file must equal the
built one (SHA-256), else the patch is deleted and the make fails.
Usage: python -I makepatch.py <built dir> <originals dir> <changes.bin> [--work <dir>]"""
import hashlib
import json
import mmap
import os
import fsx
import shutil
import struct
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import applypatch  # noqa: E402
import dpc  # noqa: E402
import lz4dec  # noqa: E402
import lz4hc  # noqa: E402

FILES = ["P_AMICIA.DPC", "P_AMICIA_CHP1.DPC", "P_AMICIA_CHP2.DPC", "P_AMICIA_END.DPC", "P_AMICIA_UNIVERSITY.DPC",
         "CUSTOMIZATION.DPC"]
MINCOPY = 16   # a copy shorter than this is stored as new bytes


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest().upper()


def records(info):
    out = []
    for bd in info["blocks"]:
        res = bd["resources"]
        out += [e["data"] for e in res["resources"]] + [d for g in res["datas"] for d in g]
    return sorted(out, key=lambda d: d["pos"])


class Blobs:
    def __init__(self, path):
        self.f = open(path, "wb")
        self.tab, self.seen, self.off = [], {}, 0
        self.new_bytes = 0

    def add(self, b):
        b = bytes(b)
        k = hashlib.sha256(b).digest()
        if k in self.seen:
            return self.seen[k]
        z = zlib.compress(b, 6)
        self.f.write(z)
        self.tab.append([self.off, len(z), len(b)])
        self.off += len(z)
        self.new_bytes += len(b)
        self.seen[k] = len(self.tab) - 1
        return self.seen[k]


def diff_ops(t, o, obase, blobs):
    """ops making t from o (same offsets; copies refer to obase + i)."""
    n, m = len(t), len(o)
    runs = []   # (equal?, start, end)

    def push(eq, a, b):
        if runs and runs[-1][0] == eq and runs[-1][2] == a:
            runs[-1] = (eq, runs[-1][1], b)
        else:
            runs.append((eq, a, b))

    i = 0
    while i < n:
        j = min(i + 4096, n)
        if j <= m and t[i:j] == o[i:j]:
            push(True, i, j)
        else:
            for a in range(i, j, 64):
                b = min(a + 64, j)
                push(b <= m and t[a:b] == o[a:b], a, b)
        i = j
    ops, lit = [], None
    for eq, a, b in runs:
        if eq and b - a >= MINCOPY:
            if lit is not None:
                ops.append([1, blobs.add(t[lit[0]:lit[1]])])
                lit = None
            ops.append([0, obase + a, b - a])
        else:
            lit = (lit[0], b) if lit else (a, b)
    if lit is not None:
        ops.append([1, blobs.add(t[lit[0]:lit[1]])])
    return ops


def make_file(name, built, orig, blobs, stats):
    with open(built, "rb") as tf, mmap.mmap(tf.fileno(), 0, access=mmap.ACCESS_READ) as T, \
            open(orig, "rb") as of, mmap.mmap(of.fileno(), 0, access=mmap.ACCESS_READ) as O:
        trs, ors = records(dpc.parse(T)), records(dpc.parse(O))
        pool = {}
        for d in ors:
            pool.setdefault((d["class_name"], d["name"], d["link_name"]), []).append(d)
        pieces = []

        def F(ops):
            if pieces and pieces[-1][0] == "F":
                pieces[-1][1].extend(ops)
            else:
                pieces.append(["F", list(ops)])

        def gap(a, b):
            if b > a:
                F(diff_ops(T[a:b], O[a:min(b, len(O))] if a < len(O) else b"", a, blobs))

        p = 0
        for d in trs:
            if d["pos"] < p:
                raise SystemExit(f"{name}: overlapping records at {d['pos']} (unsupported layout)")
            gap(p, d["pos"])
            lst = pool.get((d["class_name"], d["name"], d["link_name"]))
            od = lst.pop(0) if lst else None
            if od is None:
                stats["new_resources"] += 1
            if od and d["end"] - d["pos"] == od["end"] - od["pos"] and T[d["pos"]:d["end"]] == O[od["pos"]:od["end"]]:
                F([[0, od["pos"], d["end"] - d["pos"]]])
                stats["copied"] += 1
                p = d["end"]
                continue
            stats["changed"] += 1
            F([[1, blobs.add(T[d["pos"]:d["link_header_pos"]])]])
            tl = T[d["link_header_pos"]:d["body_pos"]]
            ol = O[od["link_header_pos"]:od["body_pos"]] if od else b""
            F(diff_ops(tl, ol, od["link_header_pos"] if od else 0, blobs) if od else [[1, blobs.add(tl)]] if tl else [])
            tb = T[d["body_pos"]:d["end"]]
            ob = O[od["body_pos"]:od["end"]] if od else b""
            if od and tb == ob:
                if tb:
                    F([[0, od["body_pos"], len(tb)]])
            elif d["compression_type"] == 2 and tb:
                raw = lz4dec.dpc_body(tb)
                if lz4hc.dpc_body(raw, 64) == tb:
                    src = b""
                    if od and ob:
                        src = lz4dec.dpc_body(ob) if od["compression_type"] == 2 else ob
                    ops = diff_ops(raw, src, 0, blobs) if src else [[1, blobs.add(raw)]]
                    pieces.append(["Z", od["body_pos"] if od and ob else -1, len(ob), od["compression_type"] if od else 0, ops])
                    stats["recompressed"] += 1
                    stats["recompressed_mb"] += len(raw) / 1e6
                else:
                    F([[1, blobs.add(tb)]])
                    stats["not_recompressible"] += 1
            elif tb:
                F(diff_ops(tb, ob, od["body_pos"], blobs) if od else [[1, blobs.add(tb)]])
            p = d["end"]
        gap(p, len(T))
        return pieces


def make(built_dir, originals, out, work=None, progress=None):
    """Write the patch to out (via out.tmp; renamed only after the self-test). Returns (file entries, stats)."""
    work = work or out + "_work"
    if os.path.isdir(work):
        shutil.rmtree(work)
    os.makedirs(work)
    names = [f for f in FILES if os.path.isfile(os.path.join(built_dir, f))]
    if not names:
        raise SystemExit(f"no game files in {built_dir}")
    blobs = Blobs(os.path.join(work, "blobs.bin"))
    files, stats = [], {}
    for i, f in enumerate(names, 1):
        if progress:
            progress(i, len(names), f"comparing {f}")
        st = dict(copied=0, changed=0, new_resources=0, recompressed=0, recompressed_mb=0.0, not_recompressible=0)
        b, o = os.path.join(built_dir, f), os.path.join(originals, f)
        pieces = make_file(f, b, o, blobs, st)
        files.append(dict(name=f, original_sha256=sha256_file(o), result_sha256=sha256_file(b), size=os.path.getsize(b),
                          pieces=pieces))
        st["recompressed_mb"] = round(st["recompressed_mb"], 1)
        stats[f] = st
    blobs.f.close()
    idx = dict(format=applypatch.FORMAT, lz4hc=lz4hc.VERSION, blobs=blobs.tab, files=files)
    z = zlib.compress(json.dumps(idx, separators=(",", ":")).encode(), 9)
    tmp = out + ".tmp"
    with open(tmp, "wb") as o:
        o.write(applypatch.MAGIC + struct.pack("<Q", len(z)) + z)
        with open(os.path.join(work, "blobs.bin"), "rb") as bf:
            shutil.copyfileobj(bf, o, 1 << 20)
    stats["_new_bytes_mb"] = round(blobs.new_bytes / 1e6, 1)
    # self-test: the players' applier on the originals must give the built files
    st_dir = os.path.join(work, "selftest")
    try:
        got = applypatch.apply(tmp, originals, st_dir, cache_dir=os.path.join(work, "cache"),
                               progress=(lambda k, n, t: progress(k, n, "self-test: " + t)) if progress else None)
    except applypatch.PatchError as e:
        fsx.remove(tmp)
        raise SystemExit(f"self-test FAILED: {e}")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    bad = [fe["name"] for fe in files if got.get(fe["name"]) != fe["result_sha256"]]
    if bad:
        fsx.remove(tmp)
        raise SystemExit(f"self-test FAILED: {bad}")
    applypatch.replace(tmp, out)
    stats["_patch_mb"] = round(os.path.getsize(out) / 1e6, 1)
    return [{k: v for k, v in fe.items() if k != "pieces"} for fe in files], stats


if __name__ == "__main__":
    a = sys.argv[1:]
    w = a[a.index("--work") + 1] if "--work" in a else None
    fl, st = make(a[0], a[1], a[2], w, progress=lambda k, n, t: print(f"PROGRESS {k}/{n} {t}", flush=True))
    print(json.dumps(dict(files=fl, stats=st), indent=1))
