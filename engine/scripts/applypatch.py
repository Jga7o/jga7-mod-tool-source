"""M17 session 3: apply a mod's patch (changes.bin, plan §5.2) to the original game files. Stdlib only.
The same code runs in the mod maker's self-test and in every install (players): what it rebuilds must equal mod.json's
result SHA-256, or nothing is used.

changes.bin = MAGIC + u64 index length + zlib(index JSON) + the blob area (each blob zlib-compressed on its own).
index: {"format": 1, "lz4hc": <lz4hc.VERSION>, "blobs": [[offset in the blob area, stored length, length], ...],
        "files": [{"name", "original_sha256", "result_sha256", "size", "pieces": [...]}]}
A file is its pieces written one after the other:
  ["F", ops]                     bytes made by ops against the original FILE
  ["Z", pos, len, ctype, ops]    a body: raw bytes made by ops against the original body at pos / len (decompressed
                                 when ctype 2; pos -1 = no source), then compressed with lz4hc (DPC LZ4 body)
  ops: [0, offset, length] = copy from the source; [1, blob] = the blob's bytes
Compressed bodies are cached by the raw bytes' SHA-256 (a texture shared by five game files is compressed once).
Usage: python -I applypatch.py <changes.bin> <originals dir> <out dir> [--only NAME]"""
import hashlib
import json
import mmap
import os
import fsx
import struct
import sys
import time
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import lz4dec  # noqa: E402
import lz4hc  # noqa: E402

MAGIC = b"AMPATCH1"
FORMAT = 1


class PatchError(Exception):
    pass


def replace(src, dst, tries=10):
    """os.replace with a short retry: Windows (the virus scanner) may still hold a just-written file."""
    for k in range(tries):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if k == tries - 1:
                raise
            time.sleep(0.5 * (k + 1))


def read_index(path):
    with open(path, "rb") as f:
        if f.read(8) != MAGIC:
            raise PatchError("not a mod patch (changes.bin)")
        n = struct.unpack("<Q", f.read(8))[0]
        idx = json.loads(zlib.decompress(f.read(n)))
    idx["_blob_base"] = 16 + n
    return idx


class Blobs:
    def __init__(self, path, idx):
        self.f = open(path, "rb")
        self.base = idx["_blob_base"]
        self.tab = idx["blobs"]

    def get(self, i):
        off, stored, length = self.tab[i]
        self.f.seek(self.base + off)
        b = zlib.decompress(self.f.read(stored))
        if len(b) != length:
            raise PatchError(f"blob {i} damaged")
        return b

    def close(self):
        self.f.close()


def build_ops(ops, src, blobs, out):
    """out.write the bytes of ops; src = a bytes-like source (copy ops)."""
    for op in ops:
        if op[0] == 0:
            _, o, n = op
            if o < 0 or o + n > len(src):
                raise PatchError("copy outside its source")
            out.write(src[o:o + n])
        else:
            out.write(blobs.get(op[1]))


class _Buf:
    def __init__(self):
        self.parts = []

    def write(self, b):
        self.parts.append(bytes(b))

    def value(self):
        return b"".join(self.parts)


def apply_file(fe, orig_path, out_path, blobs, cache_dir, progress=None):
    """Rebuild one game file into out_path (written as out_path + '.tmp', renamed only when its SHA-256 matches).
    Returns the SHA-256 (upper case)."""
    h = hashlib.sha256()

    class Out:
        def __init__(self, f):
            self.f, self.n = f, 0

        def write(self, b):
            self.f.write(b)
            h.update(b)
            self.n += len(b)

    tmp = out_path + ".tmp"
    with open(orig_path, "rb") as of, mmap.mmap(of.fileno(), 0, access=mmap.ACCESS_READ) as O, open(tmp, "wb") as f:
        out = Out(f)
        nz = sum(1 for p in fe["pieces"] if p[0] == "Z")
        k = 0
        for p in fe["pieces"]:
            if p[0] == "F":
                build_ops(p[1], O, blobs, out)
                continue
            _, pos, ln, ctype, ops = p
            src = b""
            if pos >= 0:
                src = bytes(O[pos:pos + ln])
                if ctype == 2 and src:
                    src = lz4dec.dpc_body(src)
            raw = _Buf()
            build_ops(ops, src, blobs, raw)
            raw = raw.value()
            key = hashlib.sha256(raw).hexdigest()
            cp = os.path.join(cache_dir, key + ".lz4") if cache_dir else None
            if cp and os.path.isfile(cp):
                body = open(cp, "rb").read()
            else:
                body = lz4hc.dpc_body(raw, 64)
                if cp:
                    open(cp + ".tmp", "wb").write(body)
                    replace(cp + ".tmp", cp)
            out.write(body)
            k += 1
            if progress:
                progress(k, nz, f"{fe['name']}: compressing {len(raw) // 1024} KB")
        if out.n != fe["size"]:
            raise PatchError(f"{fe['name']}: rebuilt size {out.n} != {fe['size']}")
    sha = h.hexdigest().upper()
    if sha != fe["result_sha256"].upper():
        fsx.remove(tmp)
        raise PatchError(f"{fe['name']}: rebuilt file differs from the mod's result (SHA-256 {sha[:8]})")
    replace(tmp, out_path)
    return sha


def apply(patch, originals, out_dir, only=None, cache_dir=None, progress=None):
    """Rebuild every file of the patch (or only=[names]) from originals\\<name> (or originals[name] when a dict) into
    out_dir\\<name>."""
    idx = read_index(patch)
    if idx.get("format", 0) > FORMAT or idx.get("lz4hc", 0) > lz4hc.VERSION:
        raise PatchError("this mod needs a newer version of the tool")
    if idx.get("lz4hc") != lz4hc.VERSION:
        raise PatchError(f"this mod was made with compressor version {idx.get('lz4hc')}, this tool has {lz4hc.VERSION}")
    os.makedirs(out_dir, exist_ok=True)
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
    blobs = Blobs(patch, idx)
    done = {}
    try:
        for fe in idx["files"]:
            if only and fe["name"] not in only:
                continue
            src = originals[fe["name"]] if isinstance(originals, dict) else os.path.join(originals, fe["name"])
            done[fe["name"]] = apply_file(fe, src, os.path.join(out_dir, fe["name"]), blobs, cache_dir, progress)
    finally:
        blobs.close()
    return done


if __name__ == "__main__":
    a = sys.argv[1:]
    only = [a[a.index("--only") + 1]] if "--only" in a else None
    r = apply(a[0], a[1], a[2], only, cache_dir=os.path.join(a[2], "_cache"))
    for k, v in r.items():
        print(f"{k}: {v}")
