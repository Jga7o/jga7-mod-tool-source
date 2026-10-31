"""Pure-Python LZ4 block compressor with hash chains and lazy matching (closer to LZ4-HC than bff-cli's fast mode).

Output: a standard LZ4 block. dpc_body() wraps it in the DPC LZ4 convention: u32 decompressed size, u32 total size
(block + 8), block. Every result is checked by decompressing with lz4dec.py (see verify()).
M17 session 2: the ONLY LZ4 compressor of the engine (bff-cli is no longer used). FROZEN: a patch mod's result hashes
depend on its exact output, so any change to compress() must raise VERSION (written into mod.json, plan §4).
Usage (test): python -I lz4hc.py <raw file> [depth]
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lz4dec  # noqa: E402

VERSION = 1   # the compressor's version (plan §4: frozen)

MINMATCH = 4
LASTLITERALS = 5
MFLIMIT = 12
MAXDIST = 65535


def compress(src, depth=64):
    n = len(src)
    out = bytearray()
    head = {}
    chain = [-1] * n
    anchor = 0
    i = 0
    limit = n - MFLIMIT

    def insert(p):
        if p + 4 <= n:
            k = src[p:p + 4]
            chain[p] = head.get(k, -1)
            head[k] = p

    def best(p):
        if p >= limit:
            return 0, 0
        k = src[p:p + 4]
        c = head.get(k, -1)
        bl, bo, d = 0, 0, 0
        maxlen = n - LASTLITERALS - p
        while c >= 0 and p - c <= MAXDIST and d < depth:
            if src[c + bl] == src[p + bl] if p + bl < n and c + bl < n else False:
                ln = 0
                while ln < maxlen and src[c + ln] == src[p + ln]:
                    ln += 1
                if ln > bl:
                    bl, bo = ln, p - c
                    if ln >= maxlen:
                        break
            c = chain[c]
            d += 1
        return (bl, bo) if bl >= MINMATCH else (0, 0)

    def emit(lit_start, lit_end, mlen, off):
        ll = lit_end - lit_start
        tok = (min(ll, 15) << 4) | (min(mlen - MINMATCH, 15) if mlen else 0)
        out.append(tok)
        if ll >= 15:
            r = ll - 15
            while r >= 255:
                out.append(255)
                r -= 255
            out.append(r)
        out.extend(src[lit_start:lit_end])
        if mlen:
            out.extend(struct.pack("<H", off))
            if mlen - MINMATCH >= 15:
                r = mlen - MINMATCH - 15
                while r >= 255:
                    out.append(255)
                    r -= 255
                out.append(r)

    while i < limit:
        ml, off = best(i)
        if ml == 0:
            insert(i)
            i += 1
            continue
        insert(i)
        # lazy: a longer match one byte later wins
        ml2, off2 = best(i + 1)
        if ml2 > ml + 1:
            i += 1
            insert(i)
            ml, off = ml2, off2
        emit(anchor, i, ml, off)
        for p in range(i + 1, min(i + ml, n)):
            insert(p)
        i += ml
        anchor = i
    emit(anchor, n, 0, 0)
    return bytes(out)


def dpc_body(raw, depth=64):
    blk = compress(raw, depth)
    return struct.pack("<II", len(raw), len(blk) + 8) + blk


def verify(raw, body, work=None):
    """Decompress body (DPC convention) with lz4dec.py and compare with raw (work: unused, kept for old callers)."""
    try:
        return lz4dec.dpc_body(body) == raw
    except (lz4dec.LZ4Error, IndexError, struct.error):
        return False


if __name__ == "__main__":
    raw = open(sys.argv[1], "rb").read()
    d = int(sys.argv[2]) if len(sys.argv) > 2 else 64
    body = dpc_body(raw, d)
    print(len(raw), "->", len(body), "verified:", verify(raw, body))
