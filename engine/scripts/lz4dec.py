"""M17 session 2: our own LZ4 block decoder (plan §4; replaces bff-cli unlz). Stdlib only.
DPC convention for an LZ4 body: u32 decompressed size, u32 total size (block + 8), then the LZ4 block.
Usage (test): python -I lz4dec.py <dpc body file> <out raw>"""
import struct
import sys


class LZ4Error(Exception):
    pass


def block(src, size):
    """Decompress one standard LZ4 block into exactly `size` bytes."""
    out = bytearray()
    i, n = 0, len(src)
    while i < n:
        tok = src[i]
        i += 1
        lit = tok >> 4
        if lit == 15:
            while True:
                b = src[i]
                i += 1
                lit += b
                if b != 255:
                    break
        out += src[i:i + lit]
        i += lit
        if i >= n:   # the last sequence has literals only
            break
        off = src[i] | src[i + 1] << 8
        i += 2
        if off == 0 or off > len(out):
            raise LZ4Error(f"bad match offset {off} at output {len(out)}")
        ml = tok & 15
        if ml == 15:
            while True:
                b = src[i]
                i += 1
                ml += b
                if b != 255:
                    break
        ml += 4
        start = len(out) - off
        if ml <= off:
            out += out[start:start + ml]
        else:   # overlapping match: repeat the last `off` bytes
            while ml > 0:
                k = min(ml, off)
                out += out[start:start + k]
                start += k
                ml -= k
    if len(out) != size:
        raise LZ4Error(f"decompressed {len(out)} bytes, expected {size}")
    return bytes(out)


def dpc_body(body):
    """A DPC LZ4 body (u32 decompressed size, u32 total, block) -> the raw bytes."""
    dec, tot = struct.unpack_from("<II", body)
    return block(body[8:tot], dec)


if __name__ == "__main__":
    open(sys.argv[2], "wb").write(dpc_body(open(sys.argv[1], "rb").read()))
