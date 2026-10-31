"""Recompute derived header/size fields of a DPC from its resources and compare.

If every derived field is reproduced, an in-place editor can recompute them after a change.
Also reports where each class is stored and the free slack after each standalone resource.
M13 (enlarged archives, growdpc.py): also checks the fields that move when sectors are inserted (map right after the
block descriptions, data_resources_map_offset = data_offset - map, the table ends before data_offset, every slot
starts where the previous one ends) and the working-buffer relations seen in all five originals (x1 = max(group wbo +
padded), map wbo = last group wbo + padded), slot fill 0xFF, group fill 0x00, and each standalone resource at most
the largest seen in the game's archives.
--gaps-ok: a slot may be larger than pad(end) (enlarged or shrunk body) if the rest of the slot is 0xFF; the line
"slot == pad(end)" then reads "slot >= pad(end), fill 0xFF".
Usage: python -I layoutcheck.py <file.DPC> [class_dir] [--gaps-ok]
"""
import collections
import mmap
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dpc  # noqa: E402

S = dpc.SECTOR
HDR = 48  # DataResource fixed header
LARGEST_SEEN = 5592539  # largest standalone resource (stored) in the five Amicia archives (growdpc.py)


def pad(n):
    return (n + S - 1) // S * S


def main():
    gaps_ok = "--gaps-ok" in sys.argv
    argv = [x for x in sys.argv if x != "--gaps-ok"]
    path = argv[1]
    fh = open(path, "rb")
    buf = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)  # low memory
    info = dpc.parse(buf)
    h = info["header"]
    cls = {}
    if len(argv) > 2:
        for f in os.listdir(argv[2]):
            n, c = f.rsplit(".", 1)
            cls[int(n)] = c
    checks = []

    def chk(what, got, want):
        checks.append(got == want)
        print(f"  [{'OK' if got == want else 'MISMATCH'}] {what}: file={want} computed={got}")

    chk("header.file_size == len", len(buf), h["file_size"])
    total_dec = n_res = 0
    pad_total = 0
    for bi, bd in enumerate(info["blocks"]):
        res = bd["resources"]
        ents = res["resources"]
        for e in ents:
            d = e["data"]
            n_res += 1
            total_dec += HDR + d["link_header_size"] + d["decompressed_body_size"]
            comp = d["compressed_body_size"] != 0
            chk_list = [
                e["size"] == HDR + d["size"],
                d["size"] == d["link_header_size"] + d["body_len"],
                e["decompressed_size"] == (d["decompressed_body_size"] if comp else 0),
                d["zero"] == 0,
            ]
            if not all(chk_list):
                print(f"  entry {dpc.s64(e['name'])}: size rules {chk_list}")
            checks.append(all(chk_list))
        # standalone resources are sector aligned and in increasing order
        offs = [e["offset"] * S for e in ents]
        chk(f"block{bi} standalone offsets increasing", offs, sorted(offs))
        ends = [e["data"]["end"] for e in ents]
        # layout: header | standalone resources | block descriptions | resource map | data groups
        nxt = offs[1:] + [h["block_description_offset"] * S]
        chk(f"block{bi} each standalone ends before next slot", all(a <= b for a, b in zip(ends, nxt)), True)
        if gaps_ok:
            chk(f"block{bi} slot >= pad(end), fill 0xFF", all(pad(e) <= n and set(buf[e:n]) <= {0xFF} for e, n in zip(ends, nxt)), True)
        else:
            chk(f"block{bi} slot == pad(end)", [pad(e) for e in ends] == nxt, True)
        # M13: fields that move when sectors are inserted
        chk(f"block{bi} first slot at 4096", offs[0] if offs else 4096, 4096)
        chk(f"block{bi} resources map right after the block descriptions", bd["resources_map_offset"],
            h["block_description_offset"] + 1)
        chk(f"block{bi} data_resources_map_offset == data_offset - map", bd["data_resources_map_offset"],
            res["data_offset"] - bd["resources_map_offset"])
        tend = res["table_pos"] + 4 + len(ents) * 36 + 8
        chk(f"block{bi} table ends before data_offset", pad(tend) <= res["data_offset"] * S, True)
        chk(f"block{bi} map fill 0xFF", set(buf[tend:res["data_offset"] * S]) <= {0xFF}, True)
        chk(f"block{bi} slot fill 0xFF", all(set(buf[e:n]) <= {0xFF} for e, n in zip(ends, nxt)), True)
        big = max((e - o for e, o in zip(ends, offs)), default=0)
        chk(f"block{bi} largest standalone resource <= {LARGEST_SEEN} (largest in the game's archives)", big <= LARGEST_SEEN, True)
        dds = res["data_descriptions"]
        if dds:
            chk(f"block{bi} x1 == max(group wbo + padded)", max(d["working_buffer_offset"] + d["padded_size"] for d in dds), res["x1"])
            chk(f"block{bi} map wbo == last group wbo + padded", dds[-1]["working_buffer_offset"] + dds[-1]["padded_size"],
                res["working_buffer_offset"])
        gpad = 0
        for gi, (dd, g) in enumerate(zip(res["data_descriptions"], res["datas"])):
            size = g[-1]["end"] - g[0]["pos"]
            for d in g:
                n_res += 1
                total_dec += HDR + d["link_header_size"] + d["decompressed_body_size"]
            chk(f"block{bi} data[{gi}].size", size, dd["size"])
            chk(f"block{bi} data[{gi}].padded_size", pad(size), dd["padded_size"])
            chk(f"block{bi} data[{gi}].resource_count", len(g), dd["resource_count"])
            chk(f"block{bi} data[{gi}] fill 0x00", set(buf[g[-1]["end"]:pad(g[-1]["end"])]) <= {0}, True)
            gpad += dd["padded_size"] - dd["size"]
        chk(f"block{bi} data groups end at EOF", res["data_offset"] * S + res["padded_size"], len(buf))
        chk(f"block{bi} resources.padded_size == sum(data padded)",
            sum(dd["padded_size"] for dd in res["data_descriptions"]), res["padded_size"])
        chk(f"block{bi} resources.padding_size == sum(data pad)", gpad, res["padding_size"])
        pad_total += gpad
        wb = [dd["working_buffer_offset"] for dd in res["data_descriptions"]]
        print(f"  block{bi} data working_buffer_offsets={wb} resources.wbo={res['working_buffer_offset']}")
        # storage stats per class
        where = collections.Counter()
        slack = collections.defaultdict(list)
        for e, end, n in zip(ents, ends, nxt):
            c = cls.get(dpc.s64(e["name"]), "?")
            where[(c, "standalone")] += 1
            slack[c].append(n - end)
        for g in res["datas"]:
            for d in g:
                where[(cls.get(dpc.s64(d["name"]), "?"), "data-group")] += 1
        print("  storage:", dict(where))
        for c, s in slack.items():
            print(f"  standalone slack {c}: n={len(s)} min={min(s)} median={sorted(s)[len(s)//2]} max={max(s)}")
    chk("header.total_resources", n_res, h["total_resources"])
    chk("header.total_decompressed_size", total_dec, h["total_decompressed_size"])
    chk("header.padding_size == sum(data-group padding)", pad_total, h["padding_size"])
    print(f"ALL OK: {all(checks)} ({sum(checks)}/{len(checks)})")


if __name__ == "__main__":
    main()
