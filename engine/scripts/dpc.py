"""Minimal parser for A Plague Tale: Innocence .DPC bigfiles (v2.128.92.19 PC).

Layout follows tools/ImZouna-master/patterns/apti/DPC.hexpat.
Usage: python -I dpc.py <file.DPC> [--json out.json]
"""
import json
import struct
import sys

SECTOR = 2048


class R:
    def __init__(self, buf, pos=0):
        self.b, self.p = buf, pos

    def u16(self):
        v = struct.unpack_from("<H", self.b, self.p)[0]; self.p += 2; return v

    def u32(self):
        v = struct.unpack_from("<I", self.b, self.p)[0]; self.p += 4; return v

    def u64(self):
        v = struct.unpack_from("<Q", self.b, self.p)[0]; self.p += 8; return v


def s64(v):
    return v - (1 << 64) if v >= 1 << 63 else v


def parse_data_resource(buf, pos):
    r = R(buf, pos)
    d = dict(pos=pos, class_name=r.u64(), name=r.u64(), link_name=r.u64(),
             size=r.u32(), link_header_size=r.u32(), decompressed_body_size=r.u32(),
             compression_type=r.u32(), compressed_body_size=r.u32(), zero=r.u32())
    d["link_header_pos"] = r.p
    d["body_pos"] = r.p + d["link_header_size"]
    d["body_len"] = d["compressed_body_size"] or d["decompressed_body_size"]
    d["end"] = d["body_pos"] + d["body_len"]
    return d


def parse(buf):
    r = R(buf)
    h = dict(version=buf[:256].split(b"\0")[0].decode())
    r.p = 256
    h["u0"] = r.u32(); h["is_not_rtc"] = r.u32(); h["block_description_offset"] = r.u32()
    h["u1"] = r.u32(); h["pool_offset"] = r.u32(); h["u2"] = r.u32(); h["u3"] = r.u32(); h["u4"] = r.u32()
    h["padding_size"] = r.u64(); h["u5"] = r.u64(); h["file_size"] = r.u64()
    h["total_decompressed_size"] = r.u64(); h["u6"] = r.u64(); h["total_resources"] = r.u32()

    r.p = h["block_description_offset"] * SECTOR
    blocks = []
    for _ in range(r.u32()):
        bd = dict(a=r.u64(), b=r.u64(), c=r.u64(),
                  resources_map_offset=r.u32(), data_resources_map_offset=r.u32())
        blocks.append(bd)

    for bd in blocks:
        r2 = R(buf, bd["resources_map_offset"] * SECTOR)
        res = dict(data_count=r2.u32(), data_offset=r2.u32(), working_buffer_offset=r2.u32(),
                   x0=r2.u32(), x1=r2.u64(), padded_size=r2.u64(), padding_size=r2.u64())
        descs = []
        for i in range(52):
            dd = dict(resource_count=r2.u32(), padded_size=r2.u64(), size=r2.u64(),
                      working_buffer_offset=r2.u64())
            if i < res["data_count"]:
                descs.append(dd)
        res["data_descriptions"] = descs
        res["table_pos"] = r2.p
        resources = []
        for _ in range(r2.u32()):
            e = dict(entry_pos=r2.p, name=r2.u64(), class_name=r2.u64(), offset=r2.u32(),
                     size=r2.u64(), decompressed_size=r2.u64())
            e["data"] = parse_data_resource(buf, e["offset"] * SECTOR)
            resources.append(e)
        res["resources"] = resources
        # data resources (embedded objects) following data_offset
        datas = []
        p = res["data_offset"] * SECTOR
        for dd in descs:
            group = []
            for _ in range(dd["resource_count"]):
                d = parse_data_resource(buf, p)
                group.append(d)
                p = d["end"]
            p = (p + SECTOR - 1) // SECTOR * SECTOR
            datas.append(group)
        res["datas"] = datas
        bd["resources"] = res
    return dict(header=h, blocks=blocks, file_len=len(buf))


def main():
    path = sys.argv[1]
    buf = open(path, "rb").read()
    info = parse(buf)
    h = info["header"]
    print(f"file_len={info['file_len']} header.file_size={h['file_size']} "
          f"total_resources={h['total_resources']} pool_offset={h['pool_offset']}")
    for bi, bd in enumerate(info["blocks"]):
        res = bd["resources"]
        print(f"block {bi}: map@{bd['resources_map_offset']*SECTOR:#x} data_count={res['data_count']} "
              f"data_offset={res['data_offset']*SECTOR:#x} resources={len(res['resources'])}")
        for e in res["resources"]:
            d = e["data"]
            print(f"  res name={s64(e['name']):>21} @{e['offset']*SECTOR:#08x} size={e['size']:>7} "
                  f"dsize={e['decompressed_size']:>7} | hdr: size={d['size']} link={d['link_header_size']} "
                  f"dec={d['decompressed_body_size']} ctype={d['compression_type']} comp={d['compressed_body_size']} "
                  f"zero={d['zero']} end={d['end']:#x}")
        for gi, g in enumerate(res["datas"]):
            for d in g:
                print(f"  data[{gi}] name={s64(d['name']):>21} @{d['pos']:#08x} link={d['link_header_size']} "
                      f"dec={d['decompressed_body_size']} ctype={d['compression_type']} comp={d['compressed_body_size']}")
    if "--json" in sys.argv:
        with open(sys.argv[sys.argv.index("--json") + 1], "w") as f:
            json.dump(info, f, indent=1)


if __name__ == "__main__":
    main()
