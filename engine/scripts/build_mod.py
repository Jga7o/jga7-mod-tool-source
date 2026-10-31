"""Build the whole mod from the manifest (M9, M10): for the base archive and each variant archive, start from the
original in samples\\originals, apply every edit in order, verify, and write output\\test_<name>\\<archive>.

Manifest (output\\mod\\amicia_mod.json): "archive", "test_name", "edits" (applied in order), "checks" for the base
archive (P_AMICIA), and "variants": a list of the same keys for the other archives. Edit types:
  face_mesh  source glb, meshes [M37, ...]       facepatch.py (positions only; lip-sync data untouched)
             variants: "targets" {"M37": <the variant's mesh id>}
  texture    source png, id (label for humans)    texpatch.py apply
  mesh       source glb, object, slot             blendimport.py (own geometry, weights, UVs)
             variants: "mesh_id" (slot = a label for humans); bones are remapped to the variant's skeleton by name
  hide       slots [Mxx, ...]                     patch_hide.py (index buffers zeroed)
             variants: "ids" [<mesh id>, ...]
  face_transfer  source glb, targets {"M37": id}  facepatch.py on P_AMICIA's M37, then facetransfer.py onto the variant's
                                                  own face mesh (copy at M37 positions, interpolate elsewhere)
  texture_delta  source png, original png, id     texdelta.py: the variant's texture + (edited - original), texpatch.py
  necklace   source glb, archive CHP1|CHP2        necklacepatch.py (positions only, from the necklace kit)
  positions  source json (posdelta.py make)        posdelta.py apply: the archive's own positions + stored deltas (package)
  texture_add  source .texdelta, id               texadd.py: the archive's own texture + stored colour difference (package)
  merge      slots [target, Mxx, ...]            mergemesh.py: the pieces (identical Material_Z contents) merged into the
             (variants: "ids" [...])             target's slot, enlarged by growdpc.py if needed; the others are hidden
                                                 (patch_hide.py). Uses the archive's original pieces: they must not be
                                                 edited by an earlier edit.
  grow       slots [Mxx, ...], sectors N         growdpc.py: slots enlarged by N sectors, content unchanged (self-test)
  full_body  source glb, parts [{object, host?,   your own body model in parts (M14b): per part blendimport.py into its
             kind?, mask?, compensate?}], keep?   host (slot enlarged when needed) and hosttex.py for the host's textures
                                                 from the object's own glTF material (+ optional mask PNG); then every
                                                 replaced outfit piece is hidden (hosts.OUTFIT and the pieces sharing a
                                                 host's textures) except "keep"; kept pieces that share a host's textures
                                                 keep their texels (hosttex.reserve). "host" optional (best free host of
                                                 the part's "kind": cloth | skin; hair comes with M15). No two parts on
                                                 one texture set. P_AMICIA only.
             + archives: "all" | [names],         M16: ONE edit for every listed archive (in the base archive's edits;
               per_archive? {arch: {hosts: {object:   bodyroute.expand adds the other archives, test names <stem>_chp1 ...).
               label}, keep: [...], hide: [...]}}  Per archive (bodyroute.plan): hosts among that archive's outfit pieces,
                                                 packing when it has too few hosts (each part's UV box in its own cell of
                                                 one host's textures, same scale along u and v), hide = every outfit piece
                                                 except "keep" (roles such as necklace, hood_buttons, shield, or labels;
                                                 default ["necklace"]); bones remapped by name hash with parent fallback.
                                                 Without "archives" the M14b behaviour above is unchanged. Per part:
                                                 "archives" (used only there), "share" (its share of a packed host's
                                                 texels; default: equal texel density per surface area, skin parts 1/4 of
                                                 that); per archive: "shares" {object: weight}. Host masks are written in
                                                 the host's own convention (part masks only on R-style hosts); BC3 colour
                                                 hosts get alpha 255 (texpatch --opaque). M17: the game's forearm skin
                                                 is never added (chapter I's hands end at the wrist: the body must
                                                 bring its own forearms); a "forearms" key is refused.
               replace_head?, replace_hands?,     M17 full body swap (default false): also hide the game's head, mouth
               keep_game_eyes?                    and teeth, eyes and lashes / the game's hands (bodyroute.REPLACE_*);
                                                 the skin part supplies them. keep_game_eyes keeps eyes and lashes.
                                                 With the game's eyes hidden, a later "texture" edit of the eye colour
                                                 (B31) is skipped with a note (nothing would show it).
               per_archive {arch: {material_from:  session 55: the part is imported into its host slot as usual, but
               {object: donor label}}}            draws with the donor's material (matswap.py swaps the two meshes'
                                                 material fields); the donor must be a piece hidden by replace_head /
                                                 replace_hands (e.g. the head M37: skin shading, B05 / B07 2048). The
                                                 part's textures go into the donor's textures; an extra translucency
                                                 map (+388, B06) is written flat (TRANSLUCENCY); B08 stays.
               part "skin_mask"? <png>              session 56, whole-model mode (one object, one texture set, on the
                                                 donor head's material): white = skin, black = cloth. B06 translucency =
                                                 TRANSLUCENCY x mask (cloth 0), B09 overlay mask = 165 skin / 148 cloth,
                                                 B08 pore detail written flat (B08_FLAT) unless the part has "pores": "keep".
               per_archive {arch: {texture_size:  session 56: the donor's colour and normal are written at this size
               4096}}                            (texresize.py, stored uncompressed; VERIFY --bitmap-size).
               part "texture_size"? 4096           session 61: the same for a part alone in its OWN host (no swap).
               part "colour_limit"? <float>        session 61: VERIFY's colour RMSE limit for this part (default 4.0).
  hair       source glb, object (default "hair"),  M15, optional: one new whole hair. blendimport.py into the archive's hair
             host? (label or mesh id)              host (hairedit.py: M02 / C2-20 / E-03 / U-48; CHP1 C1-20), bones
                                                 remapped by name, missing ones (CHP1 hair1-5) to their nearest parent;
                                                 every other hair piece hidden. CHP1's host draws its own textures, so
                                                 B01 / B02 are copied into 1-00 / 1-10 (UVs on the B01 atlas, route A).
                                                 Without a hair edit no hair piece is touched (full_body never does).
             test_uv_remap? ("islands" | "strip")  TEST ONLY, off by default: the glb's UVs (made for its own texture)
                                                 are moved onto B01 strand areas in a copy (hairuvmap.py; the source glb
                                                 is never written), for a geometry-only test. The test name must
                                                 contain "geo".
             texture? {colour, alpha, normal: "flat",  M15b route B: own hair texture (hairtex.py): colour PNG + grey alpha
             alpha_linearize?, alpha_sharpen?}     PNG -> straight RGBA, 16 px edge bleed, BC3 with all 12 mips (alpha-
                                                 weighted), written into the host's colour texture (B01; CHP1 1-00) and a
                                                 flat normal into its normal texture (B02; CHP1 1-10); M17 (D3):
                                                 "normal": <png> writes the user's own normal map there instead. Excludes
                                                 test_uv_remap. CUSTOMIZATION needs the "hair_texture" edit as well.
  hair_texture texture {...} (as above),         CUSTOMIZATION.DPC only (xple layout): the same texture into the outfit
             options? ["U39","U24","U42","U21"]   options' hair textures (raw, in place); checked by hairtex verify-xple.
  atlas      spec <json> (atlasmerge.py)         pieces with different textures merged into the host slot with one atlas:
                                                 atlasmerge.py -> growdpc.py (host) -> patch_hide.py (the other pieces +
                                                 the spec's "hide") -> texpatch.py for the host's colour, normal, mask
                                                 (hosttex.py). P_AMICIA only. Uses the original pieces and textures.
Paths are relative to C:\\modlab; a source with * takes the newest matching file.
Checks: {"slot_equals": "Mxx", "file": <DPC>}: the slot's content (class / name / link, compression type, link header,
body after decompression) must equal that in that file (e.g. a slot already tested in game); the compressed bytes may
differ (lz4hc vs the compressor of the older file).
{"same_positions": "M37", "file": <Mesh_Z>}: the face mesh built here has the same vertex positions as that file
(e.g. a variant's copy of M37 against the base build).
Every step writes <work>\\stepN.tmp.DPC; verify_face.py checks the result against the original (layoutcheck, extract,
only the edited resources differ, per-type rules). Only on PASS is the file copied (as .tmp, hash-checked, renamed).
Any error stops that archive's build; nothing is written to its test folder then.

Usage: python -I build_mod.py [--manifest <json>] [--build all|<test name>] [--only 1,2,...] [--dest-dir <dir>]
       --build: one archive by its test name (default: all, base first); --only: only these edit numbers (1-based;
       rehearsals, one archive); --dest-dir: instead of output\\test_<name> (one archive).
"""
import argparse
import glob
import hashlib
import json
import math
import mmap
import os
import shutil
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paths  # noqa: E402
import fsx  # noqa: E402  (renames with a retry: Windows may hold a new file)
import dpc  # noqa: E402
import lz4dec  # noqa: E402
from blendimport import slot_table  # noqa: E402
from facepatch import MESHES  # noqa: E402
from meshfmt import parse  # noqa: E402
from patch_hide import find, slot_limits  # noqa: E402

OUT = paths.OUT
ROOT = paths.REPO   # manifest paths are relative to C:\modlab
ORIG = paths.ORIG
LOG = []


class BuildError(Exception):
    pass


def say(*a):
    s = " ".join(str(x) for x in a)
    print(s)
    LOG.append(s)


def sha(p):
    h = hashlib.sha256()  # in 1 MB chunks (low memory)
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def run(args, what):
    r = subprocess.run([sys.executable, "-I"] + args, capture_output=True, text=True)
    out = (r.stdout + r.stderr).rstrip()
    for line in out.splitlines():
        say("   " + line)
    if r.returncode:
        raise BuildError(f"FAILED at {what}")
    return out


def source(rel):
    p = os.path.join(ROOT, rel.replace("/", os.sep))
    if "*" in p:
        hits = sorted(glob.glob(p), key=os.path.getmtime)
        if not hits:
            raise BuildError(f"no file matches {rel}")
        p = hits[-1]
    if not os.path.isfile(p):
        raise BuildError(f"missing source {rel}")
    return p


EYE_COLOUR = 7468423702097631877   # B31, the iris texture of every archive's eyes
SKIN_OVERLAY, CLOTH_OVERLAY = 165, 148   # B09 R: the face's own background / the cloth host's (notes\host_material.md)
B08_FLAT = (127, 128, 132, 128)   # the pore detail map without detail: X = Y = 0 (A, G 128); R, B as the game's means
TRANSLUCENCY = 48   # flat value for a donor head's B06 under a body part (guess: the face's cheeks are 50-100, the scalp 15)
CUSTOM_HAIR = {"U39": 68700188137309512, "U24": 1753504241371952130, "U42": 7473073631509535936, "U21": -7370720669712732020}


def hair_texture(tex, n):
    """Route B (M15b): prepare (cached) the own hair texture block {"colour", "alpha", "alpha_linearize"?, "alpha_sharpen"?,
    "normal": "flat" | <png>} with hairtex.py; returns the folder with hair_rgba.png, hair_colour.bc3 and
    normal_flat.png/.bc3 (or normal_own.png/.bc3: M17, D3, the user's own normal map)."""
    nrm = tex.get("normal", "flat")
    lin, sharp = bool(tex.get("alpha_linearize")), bool(tex.get("alpha_sharpen"))
    d = os.path.join(OUT, "import_work", "hairtex", f"lin{int(lin)}_sharp{int(sharp)}" + ("" if nrm == "flat" else "_normal"))
    out = run([os.path.join(HERE, "hairtex.py"), "prepare", "--colour", source(tex["colour"]), "--alpha", source(tex["alpha"]),
               "--out", d] + (["--alpha-linearize"] if lin else []) + (["--alpha-sharpen"] if sharp else [])
              + ([] if nrm == "flat" else ["--normal", source(nrm)]), f"edit {n} (hairtex prepare)")
    say("   " + out.strip().splitlines()[-1])
    return d


def body_multi(ed, n, arch, base, cur, nxt, work, unpacked):
    """M16: one full_body edit with "archives" applied to this archive (bodyroute.plan: hosts, packing, hide list).
    Returns (verify args, touched labels, the tools' output)."""
    import bodyroute
    import hosts
    import hosttex
    a = arch[:-4]
    glb = source(ed["source"])
    keep = ed.get("keep", bodyroute.DEFAULT_KEEP)
    if "forearms" in ed:   # M17: removed (M16c-d added the game's forearm skin in chapter I)
        raise BuildError(f"edit {n}: \"forearms\" is no longer supported: the game's forearm skin is never added; "
                         "model the forearms into your body (check-model warns where chapter I's arm is not covered)")
    ov = bodyroute.override_for(ed, a)
    import gltfread
    g = gltfread.load(glb)
    parts = []
    for pt in ed["parts"]:
        ni = g.find_node(pt["object"])
        if ni is None:
            raise BuildError(f"edit {n}: object '{pt['object']}' not in {os.path.basename(glb)}")
        prims = g.doc["meshes"][g.doc["nodes"][ni]["mesh"]]["primitives"]
        nv = sum(g.doc["accessors"][p["attributes"]["POSITION"]]["count"] for p in prims)
        area = uva = 0.0   # surface area (m²) / UV area used: the default packing share (the same texel density for all)
        uvs = []
        for p in prims:
            P = g.accessor(p["attributes"]["POSITION"])
            U = g.accessor(p["attributes"]["TEXCOORD_0"])
            uvs += U
            idx = [x[0] for x in g.accessor(p["indices"])]
            for i in range(0, len(idx), 3):
                a0, a1, a2 = P[idx[i]], P[idx[i + 1]], P[idx[i + 2]]
                e1 = [a1[q] - a0[q] for q in range(3)]
                e2 = [a2[q] - a0[q] for q in range(3)]
                area += 0.5 * math.sqrt((e1[1] * e2[2] - e1[2] * e2[1]) ** 2 + (e1[2] * e2[0] - e1[0] * e2[2]) ** 2
                                        + (e1[0] * e2[1] - e1[1] * e2[0]) ** 2)
                u0, u1, u2 = U[idx[i]], U[idx[i + 1]], U[idx[i + 2]]
                uva += 0.5 * abs((u1[0] - u0[0]) * (u2[1] - u0[1]) - (u2[0] - u0[0]) * (u1[1] - u0[1]))
        ts = bodyroute.part_texsize(g, ni)
        parts.append(dict(pt, kind=pt.get("kind", "cloth"), vertices=nv, area=round(area / max(0.02, min(1.0, uva)), 5),
                          texsize=list(ts), uvbox=bodyroute.uv_box(uvs, ts[0], ts[1]),
                          uv_outside=sum(1 for q in uvs if not (-1e-3 <= q[0] <= 1.001 and -1e-3 <= q[1] <= 1.001))))
    rows = hosts.table(a)
    try:
        pl = bodyroute.plan(a, parts, keep, ov, rows, replace=dict(head=bool(ed.get("replace_head")),
                                                                    hands=bool(ed.get("replace_hands")),
                                                                    keep_eyes=bool(ed.get("keep_game_eyes"))))
    except bodyroute.PlanError as e:
        raise BuildError(f"edit {n}: {e}")
    table = {r["label"]: r for r in rows}
    fw = os.path.join(work, f"body{n}")
    os.makedirs(fw, exist_ok=True)
    say(f"edit {n}: full body {os.path.basename(glb)} in {a} (chapter {bodyroute.CHAPTER[a]}):")
    for gr in pl["groups"]:
        for w in gr["why"]:
            say(f"   {w}")
    say(f"   hide {', '.join(pl['hide'])}" + (f"; keep {', '.join(f'{l} ({pl['outfit'][l]})' for l in pl['kept'])}" if pl["kept"] else ""))
    if pl["replaced"]:
        say(f"   replaced by your skin part (game pieces hidden): " + ", ".join(f"{l} ({r})" for l, r in pl["replaced"].items()))
    for x in pl["notes"]:
        say(f"   NOTE: {x}")
    if pl["skipped"]:
        say(f"   parts not for this archive (their \"archives\"): {', '.join(pl['skipped'])}")
    byobj0 = {pt["object"]: pt for pt in parts}
    for gr in pl["groups"]:
        if gr.get("shares"):
            say(f"   {gr['host']} packing (cells keep each part's aspect, at most 2:1): " + ", ".join(
                f"'{o}' {100 * sh:.0f} % ({c['px'][0]} x {c['px'][1]} texels for UV u {c['src'][0]:.3f}-{c['src'][1]:.3f} "
                f"v {c['src'][2]:.3f}-{c['src'][3]:.3f}, {bodyroute.cell_density(c, byobj0[o])[0]:.0f} texels/m)"
                for o, sh, c in zip(gr["objects"], gr["shares"], gr["cells"])))
            for o in gr["objects"]:
                if byobj0[o].get("uv_outside"):
                    say(f"   NOTE: '{o}': {byobj0[o]['uv_outside']} UVs outside 0..1 are clamped (a packed part cannot tile)")
    rdir = os.path.join(unpacked, "resources")
    vargs, logs = [], []
    byobj = {pt["object"]: pt for pt in parts}
    mfrom = ov.get("material_from", {})
    swaps = []
    for k, gr in enumerate(pl["groups"]):
        host, mid = gr["host"], gr["id"]
        donor = mfrom.get(gr["objects"][0])
        if donor:
            if len(gr["objects"]) > 1 or donor not in pl["replaced"]:
                raise BuildError(f"edit {n}: material_from {donor} for '{gr['objects'][0]}': the donor must be a piece hidden by "
                                 f"replace_head / replace_hands ({', '.join(pl['replaced']) or 'none here'}) and the part alone in its host")
            swaps.append((mid, table[donor]["id"], host, donor))
            say(f"   '{gr['objects'][0]}' in {host} draws with {donor}'s material (material swap)")
        spec = hosttex.host_spec(donor or host, a)
        sm_path = byobj[gr["objects"][0]].get("skin_mask") if len(gr["objects"]) == 1 else None
        skin_mask = hosttex.read_png(source(sm_path)) if sm_path and donor else None
        part1 = byobj[gr["objects"][0]] if len(gr["objects"]) == 1 else {}
        tsize = (ov.get("texture_size") if donor else None) or part1.get("texture_size")   # session 61: also a part's own host
        if tsize:   # session 56: the donor's colour and normal at a new size (texresize.py)
            for role in ("colour", "normal"):
                spec[role] = dict(spec[role], w=int(tsize), h=int(tsize))
            say(f"   {donor or host}'s colour and normal written at {tsize} x {tsize} (new size, stored uncompressed)")
        own_mask = True   # part masks (grey PNG) are in the R (host-style) convention
        if spec.get("mask"):   # M16 fix: each host's mask in ITS convention, read from its own texture
            st = bodyroute.mask_style(a, spec["mask"]["id"])
            if st in ("R", "G"):
                import texlib
                _, _, _, mimg = texlib.load(rdir, spec["mask"]["id"])
                ch = mimg[0 if st == "R" else 1::4]
                spec["mask"].update(style=st, neutral=max(range(256), key=ch.count))
            own_mask = spec["mask"]["style"] == "R"
        mw = os.path.join(fw, host)
        os.makedirs(mw, exist_ok=True)
        if len(gr["objects"]) == 1:
            src_glb, obj = glb, gr["objects"][0]
            ims, notes = hosttex.gltf_images(glb, obj)
            mpath = byobj[obj].get("mask")
            if mpath and own_mask:
                ims["mask"] = hosttex.read_png(source(mpath))
            if skin_mask and donor and own_mask:   # whole-model mode: the overlay mask follows the skin mask
                sw_, sh_, sm = skin_mask
                ov_img = bytearray(4 * sw_ * sh_)
                for q in range(sw_ * sh_):
                    t = sm[4 * q] / 255
                    ov_img[4 * q] = ov_img[4 * q + 1] = ov_img[4 * q + 2] = round(SKIN_OVERLAY * t + CLOTH_OVERLAY * (1 - t))
                    ov_img[4 * q + 3] = 255
                ims["mask"] = (sw_, sh_, ov_img)
                notes.append(f"skin mask {sw_}x{sh_}: overlay mask {SKIN_OVERLAY} on skin, {CLOTH_OVERLAY} on cloth")
        else:
            obj = f"packed_{host}"
            src_glb = os.path.join(mw, "packed.glb")
            bodyroute.pack_glb(glb, gr["objects"], gr["cells"], src_glb, obj)
            per, notes = [], []
            for o in gr["objects"]:
                im, nt = hosttex.gltf_images(glb, o)
                notes += [f"'{o}': {x}" for x in nt]
                if byobj[o].get("mask") and own_mask:
                    im["mask"] = hosttex.read_png(source(byobj[o]["mask"]))
                per.append(im)
            sz = dict(colour=(spec["colour"]["w"], spec["colour"]["h"]), normal=(spec["normal"]["w"], spec["normal"]["h"]),
                      metal=(spec["normal"]["w"], spec["normal"]["h"]), rough=(spec["normal"]["w"], spec["normal"]["h"]))
            neutral = spec["mask"]["neutral"] if spec.get("mask") else 148
            if spec.get("mask"):
                sz["mask"] = (spec["mask"]["w"], spec["mask"]["h"])
            ims = bodyroute.pack_images(per, gr["cells"], sz, dict(
                colour=(128, 128, 128, 255), normal=(128, 128, 255, 255), metal=(0, 0, 0, 255),
                rough=(hosttex.ROUGH_DEFAULT,) * 3 + (255,), mask=(neutral,) * 3 + (255,)))
            if not spec.get("mask"):
                ims.pop("mask", None)
            notes.append(f"{len(gr['objects'])} parts packed, cells u x v " + ", ".join(
                f"{c['x0']:.4f}-{c['x1']:.4f} x {c['y0']:.4f}-{c['y1']:.4f}" for c in gr["cells"]))
        rep_p = os.path.join(mw, "import.json")
        m1 = os.path.join(work, f"step{n}_{k}m.tmp.DPC")
        logs.append(run([os.path.join(HERE, "blendimport.py"), src_glb, "--object", obj, f"--slot={host if base else mid}",
                         "--dpc", cur, "--out", m1, "--work", mw, "--report", rep_p] + ([] if base else [f"--res={rdir}", "--bone-fallback"]),
                        f"edit {n} (blendimport '{obj}' -> {host})"))
        r = json.load(open(rep_p))
        for w in r.get("warnings", []):
            if "bones missing" in w or "nearest existing parent" in w:
                say(f"   '{obj}': {w}")
        vargs += [f"--replace={mid}={os.path.join(mw, r['slot'] + '.Mesh_Z')}"] + ([f"--resized={mid}"] if r.get("enlarged") else [])
        try:
            lay, n2 = hosttex.build_layouts(ims.get("colour"), ims.get("normal"), ims.get("metal"), ims.get("rough"),
                                            ims.get("mask"), bool(byobj[gr["objects"][0]].get("compensate", False)), spec)
            if gr["texture_sharing_kept"]:
                n2 += hosttex.reserve(lay, spec, gr["texture_sharing_kept"], res=rdir, arch=a)
            if spec.get("mask") and not own_mask:
                n2.append(f"the host's mask is {spec['mask']['style']} style (meaning not decoded): written neutral "
                          f"{spec['mask']['neutral']} everywhere, the parts' masks (R style) are not used here")
            opaque = spec["colour"].get("fmt") == "BC3"
            if opaque:
                n2.append("colour is BC3 with its own alpha (cut-outs): alpha 255 written everywhere")
            for x in notes + n2:
                say(f"   {host} textures: {x}")
            paths = hosttex.write_layouts(lay, os.path.join(mw, "textures"), spec)
            t1 = os.path.join(work, f"step{n}_{k}t.tmp.DPC")
            resized_now = []
            if tsize:   # colour and normal at the new size, one by one, then the rest as usual
                c0 = m1
                for role in ("colour", "normal"):
                    c1 = os.path.join(work, f"step{n}_{k}r{role}.tmp.DPC")
                    o2 = run([os.path.join(HERE, "texresize.py"), c0, c1, "--res", rdir, "--id", str(spec[role]["id"]),
                              "--png", paths[role], "--work", mw, "--over-largest", "--raw"], f"edit {n} (texresize {role})")
                    say(f"   {host} {role}: {o2.strip().splitlines()[-1]}")
                    if c0 != m1:
                        fsx.remove(c0)
                    c0 = c1
                    resized_now.append(spec[role]["id"])
                rest = {x: v for x, v in paths.items() if x not in ("colour", "normal")}
                if rest:
                    steps = hosttex.apply_layouts(c0, t1, rest, mw, spec=spec)
                    fsx.remove(c0)
                else:
                    steps = []
                    fsx.replace(c0, t1)
            else:
                steps = hosttex.apply_layouts(m1, t1, paths, mw, spec=spec, opaque_colour=opaque)
        except (ValueError, KeyError) as e:
            raise BuildError(f"edit {n} (textures {host}): {e}")
        for st in steps:
            say(f"   {host} {st['texture']}: " + " | ".join(st["log"]))
            logs += st["log"]
        fsx.remove(m1)
        if os.path.basename(cur).startswith(f"step{n}_"):
            fsx.remove(cur)
        cur = t1
        lim = dict(colour=float(part1.get("colour_limit", 4.0)), normal=8.0, mask=2.5)   # session 61: per-part colour limit
        vargs += [f"--bitmap={spec[x]['id']}={paths[x]}@{lim[x]}" for x in paths]
        vargs += [f"--resized={st['id']}" for st in steps if st["report"].get("grown")]   # session 55: texture slot enlarged
        vargs += [x for i in resized_now for x in (f"--resized={i}", f"--bitmap-size={i}")]
        gr["textures"] = {x: dict(id=spec[x]["id"], png=paths[x]) for x in paths}
        gr["mesh_file"] = os.path.join(mw, r["slot"] + ".Mesh_Z")
        if donor:   # the donor's extra translucency map (+388), laid out for the game head: flat under the body part
            import hosts
            dmat = hosts.material_info(hosts.material_of(open(os.path.join(rdir, f"{table[donor]['id']}.Mesh_Z"), "rb").read(),
                                                         rdir), rdir)["textures"]
            ext = dmat.get("extra")
            import texlib
            import texcodec
            if skin_mask and dmat.get("extra2") and byobj[gr["objects"][0]].get("pores", "flat") == "flat":
                e2 = dmat["extra2"]   # B08: the pore detail, tiled 20 x over the UVs: flat under a whole-model atlas
                ti = texlib.info(os.path.join(rdir, f"{e2}.Bitmap_Z"))
                png = os.path.join(mw, f"detail_flat_{e2}.png")
                texcodec.png_write(png, ti["w"], ti["h"], bytearray(B08_FLAT) * (ti["w"] * ti["h"]), alpha=True)
                t2 = os.path.join(work, f"step{n}_{k}d.tmp.DPC")
                out = run([os.path.join(HERE, "texpatch.py"), "apply", cur, t2, f"--id={e2}", "--png", png, "--work", mw,
                           "--allow-grow"], f"edit {n} (texpatch pore detail {e2})")
                say(f"   {donor} pore detail {e2}: flat ({out.strip().splitlines()[-1]})")
                fsx.remove(cur)
                cur = t2
                vargs += [f"--bitmap={e2}={png}"] + ([f"--resized={e2}"] if "slot enlarged" in out else [])
            if ext:
                ti = texlib.info(os.path.join(rdir, f"{ext}.Bitmap_Z"))
                png = os.path.join(mw, f"translucency_{ext}.png")
                if skin_mask:   # translucency only on skin
                    sw_, sh_, sm = skin_mask
                    tm = bytearray(4 * sw_ * sh_)
                    for q in range(sw_ * sh_):
                        v = round(TRANSLUCENCY * sm[4 * q] / 255)
                        tm[4 * q:4 * q + 4] = bytes((v, v, v, 255))
                    texcodec.png_write(png, ti["w"], ti["h"], hosttex.resample(tm, sw_, sh_, ti["w"], ti["h"]))
                else:
                    texcodec.png_write(png, ti["w"], ti["h"], bytearray([TRANSLUCENCY] * 3 + [255]) * (ti["w"] * ti["h"]))
                t2 = os.path.join(work, f"step{n}_{k}x.tmp.DPC")
                out = run([os.path.join(HERE, "texpatch.py"), "apply", cur, t2, f"--id={ext}", "--png", png, "--work", mw,
                           "--allow-grow"], f"edit {n} (texpatch translucency {ext})")
                say(f"   {donor} translucency {ext}: {'skin mask x ' if skin_mask else 'flat '}{TRANSLUCENCY} ({out.strip().splitlines()[-1]})")
                fsx.remove(cur)
                cur = t2
                vargs += [f"--bitmap={ext}={png}" + ("@4.0" if skin_mask else "")] + ([f"--resized={ext}"] if "slot enlarged" in out else [])
    out = run([os.path.join(HERE, "patch_hide.py"), cur, nxt, os.path.join(work, f"hide{n}"),
               "--ids=" + ",".join(str(table[x]["id"]) for x in pl["hide"])], f"edit {n} (patch_hide)")
    if os.path.basename(cur).startswith(f"step{n}_"):
        fsx.remove(cur)
    vargs += [f"--hide={table[x]['id']}" for x in pl["hide"]]
    for hid, did, hl, dl in swaps:   # after hiding: the host and the hidden donor swap their materials
        out2 = run([os.path.join(HERE, "matswap.py"), nxt, nxt, "--res", rdir, "--swap", f"{hid}={did}"],
                   f"edit {n} (matswap {hl} <-> {dl})")
        out += "\n" + out2
        vargs += [f"--matswap={hid}={did}"]
    json.dump(dict(pl, source=glb, parts=parts), open(os.path.join(fw, "plan.json"), "w"), indent=1, default=str)
    return vargs, set(pl["hide"]) | {gr["host"] for gr in pl["groups"]}, "\n".join(logs + [out])


def build_xple(spec, a, man_path):
    """CUSTOMIZATION (xple layout): only "hair_texture" edits; raw bodies overwritten in place, checked by hairtex verify-xple."""
    LOG.clear()
    name, arch = spec["test_name"], spec["archive"]
    work = os.path.join(OUT, "import_work", f"mod_{name}")
    os.makedirs(work, exist_ok=True)
    src = os.path.join(ORIG, arch)
    want, got = known_hash(arch), sha(src)
    say(f"=== {name}: original {src}: sha256 {got[:16]}... matches notes\\datas_hashes.txt: {got == want}")
    if got != want:
        raise BuildError("the original does not match its recorded hash")
    stubs = os.path.join(OUT, f"{arch[:-4]}_unpacked", "resources")
    cur = os.path.join(work, "base.tmp.DPC")
    shutil.copyfile(src, cur)
    wr, png = [], None
    for n, ed in enumerate(spec["edits"], 1):
        if ed["type"] != "hair_texture":
            raise BuildError(f"edit {n}: {arch} takes only \"hair_texture\" edits")
        d = hair_texture(ed["texture"], n)
        png = os.path.join(d, "hair_rgba.png")
        labs = ed.get("options", list(CUSTOM_HAIR))
        if any(x not in CUSTOM_HAIR for x in labs):
            raise BuildError(f"edit {n}: options must be among {list(CUSTOM_HAIR)}")
        say(f"edit {n}: own hair texture -> {', '.join(f'{x} ({CUSTOM_HAIR[x]})' for x in labs)} (outfit options)")
        wr += [f"--write={CUSTOM_HAIR[x]}={os.path.join(d, 'hair_colour.bc3')}" for x in labs]
    nxt = os.path.join(work, "step1.tmp.DPC")
    out = run([os.path.join(HERE, "hairtex.py"), "xple", cur, nxt, "--stubs", stubs] + wr, "hairtex xple")
    for line in out.strip().splitlines():
        say("   " + line)
    vargs = ["--stubs", stubs] + wr + ["--png", png]
    say("verify:")
    run([os.path.join(HERE, "hairtex.py"), "verify-xple", src, nxt] + vargs, "verify")
    dst_dir = a.dest_dir or os.path.join(OUT, f"test_{name}")
    os.makedirs(dst_dir, exist_ok=True)
    tmp = os.path.join(dst_dir, arch + ".tmp")
    shutil.copyfile(nxt, tmp)
    if sha(tmp) != sha(nxt):
        raise BuildError("copy hash mismatch")
    fsx.replace(tmp, os.path.join(dst_dir, arch))
    say(f"READY: {os.path.join(dst_dir, arch)} sha256 {sha(nxt)}")
    json.dump(dict(manifest=man_path, log=list(LOG), archive=arch, original=src, xple_verify_args=vargs,
                   output=os.path.join(dst_dir, arch), sha256=sha(nxt)),
              open(os.path.join(work, "build_summary.json"), "w"), indent=1)


def known_hash(name):
    raw = open(paths.HASHES, "rb").read()
    text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8", "replace")
    for line in text.splitlines():
        parts = line.split()
        if parts and line.rstrip().upper().endswith("\\" + name.upper()):
            return parts[0].upper()
    return None


def slot_content(path, mid):
    """The slot's content as the game reads it: class / name / link (24 bytes), compression type, link header and the
    body decompressed. Compressed bytes, sizes and padding are left out (they depend on the compressor; user,
    2026-10-29: our own lz4hc replaces bff-cli)."""
    with open(path, "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as buf:  # mapped: low memory
        info = dpc.parse(buf)
        d, e = find(info, {mid})[mid]
        body = bytes(buf[d["body_pos"]:d["end"]])
        if d["compression_type"] == 2 and body:
            body = lz4dec.dpc_body(body)
        return (bytes(buf[d["pos"]:d["pos"] + 24]), d["compression_type"], bytes(buf[d["link_header_pos"]:d["body_pos"]]),
                body)


def positions(path):
    b = open(path, "rb").read()
    m = parse(b)
    return [struct.unpack_from("<3f", b, v["pos"] + 48 * k) for v in m["vbs"] if v["flags"] == 2 for k in range(v["count"])]


def build(spec, base, a, man_path):
    """Build one archive. spec: archive, test_name, edits, checks; base: True for P_AMICIA (slot labels)."""
    LOG.clear()
    name = spec["test_name"]
    arch = spec["archive"]
    work = os.path.join(OUT, "import_work", f"mod_{name}")
    os.makedirs(work, exist_ok=True)
    unpacked = os.path.join(OUT, f"{arch[:-4]}_unpacked")
    res = None if base else os.path.join(unpacked, "resources")
    src = os.path.join(ORIG, arch)
    want, got = known_hash(arch), sha(src)
    say(f"=== {name}: original {src}: sha256 {got[:16]}... matches notes\\datas_hashes.txt: {got == want}")
    if got != want:
        raise BuildError("the original does not match its recorded hash")
    cur = os.path.join(work, "base.tmp.DPC")
    shutil.copyfile(src, cur)
    slots = slot_table()
    only = {int(x) for x in a.only.split(",")} if a.only else None
    vargs, touched, summary, built_faces = [], set(), [], {}
    for n, ed in enumerate(spec["edits"], 1):
        if only and n not in only:
            continue
        nxt = os.path.join(work, f"step{n}.tmp.DPC")
        t = ed["type"]
        if t == "face_mesh":
            glb = source(ed["source"])
            targets = {k: int(v) for k, v in ed.get("targets", {}).items()}
            say(f"edit {n}: face mesh {os.path.basename(glb)} -> {','.join(ed['meshes'])}"
                + (f" (as {', '.join(f'{k} = {v}' for k, v in targets.items())})" if targets else ""))
            fw = os.path.join(work, f"face{n}")
            rep = os.path.join(fw, "facepatch_report.json")
            os.makedirs(fw, exist_ok=True)
            out = run([os.path.join(HERE, "facepatch.py"), glb, "--dpc", cur, "--out", nxt, "--work", fw, "--report", rep,
                       "--meshes", ",".join(ed["meshes"])] + [f"--target={k}={v}" for k, v in targets.items()],
                      f"edit {n} (facepatch)")
            for r in json.load(open(rep)):
                if r.get("moved"):
                    mid = targets.get(r["mesh"], MESHES[r["mesh"]][0])
                    f = os.path.join(fw, r["mesh"] + ".Mesh_Z")
                    vargs += [f"--mesh={mid}={f}"]
                    touched.add(r["mesh"])
                    built_faces[r["mesh"]] = f
        elif t == "texture":
            import bodyroute
            eyes = {l for l, r in bodyroute.pieces(arch[:-4])[1].items() if r == "eyes"} if arch[:-4] in bodyroute.PIECES else set()
            if int(ed["id"]) == EYE_COLOUR and eyes and eyes <= touched:
                say(f"edit {n}: texture {ed.get('label', '')} ({ed['id']}): SKIPPED (NOTE: the game's eyes are hidden by "
                    f"\"replace_head\": the eye colour image is ignored)")
                continue
            png = source(ed["source"])
            say(f"edit {n}: texture {ed.get('label', '')} ({ed['id']}) <- {os.path.basename(png)}")
            tw = os.path.join(work, f"tex{n}")
            out = run([os.path.join(HERE, "texpatch.py"), "apply", cur, nxt, f"--id={ed['id']}", "--png", png, "--work", tw,
                       "--report", os.path.join(work, f"texpatch{n}.json")], f"edit {n} (texpatch)")
            vargs += [f"--bitmap={ed['id']}={png}"]
            touched.add(ed.get("label", str(ed["id"])))
        elif t == "face_transfer":
            glb = source(ed["source"])
            fw = os.path.join(work, f"face{n}")
            os.makedirs(fw, exist_ok=True)
            for lab, mid in ed["targets"].items():
                mid = int(mid)
                say(f"edit {n}: face transfer {os.path.basename(glb)} {lab} -> {mid}")
                bw = os.path.join(fw, "base")
                run([os.path.join(HERE, "facepatch.py"), glb, "--dpc", os.path.join(ORIG, "P_AMICIA.DPC"), "--work", bw,
                     "--meshes", lab], f"edit {n} (facepatch on P_AMICIA for the transfer)")
                rep = os.path.join(fw, f"facetransfer_{lab}.json")
                out = run([os.path.join(HERE, "facetransfer.py"), "--dpc", cur, "--out", nxt, f"--target={mid}", "--label", lab,
                           "--orig", os.path.join(OUT, "P_AMICIA_unpacked", "resources", f"{MESHES[lab][0]}.Mesh_Z"),
                           "--edited", os.path.join(bw, f"{lab}.Mesh_Z"), "--work", fw, "--report", rep],
                          f"edit {n} (facetransfer {lab})")
                f = os.path.join(fw, f"{lab}.Mesh_Z")
                vargs += [f"--mesh={mid}={f}"]
                touched.add(lab)
                built_faces[lab] = f
                cur = nxt
        elif t == "positions":
            dl = source(ed["source"])
            doc = json.load(open(dl))
            mid, lab = int(doc["mesh_id"]), doc["label"]
            say(f"edit {n}: position deltas {os.path.basename(dl)} -> {lab} ({mid}), {doc['moved']} vertices")
            pw = os.path.join(work, f"pos{n}")
            out = run([os.path.join(HERE, "posdelta.py"), "apply", "--dpc", cur, "--dst", nxt, "--deltas", dl, "--work", pw,
                       "--report", os.path.join(work, f"positions{n}.json")], f"edit {n} (posdelta)")
            f = os.path.join(pw, f"{lab}.Mesh_Z")
            vargs += [f"--mesh={mid}={f}"]
            touched.add(lab)
            built_faces[lab] = f
        elif t == "texture_add":
            dl = source(ed["source"])
            tw = os.path.join(work, f"tex{n}")
            os.makedirs(tw, exist_ok=True)
            say(f"edit {n}: texture {ed.get('label', '')} ({ed['id']}) += {os.path.basename(dl)}")
            tgt, res_png = os.path.join(tw, "target.png"), os.path.join(tw, "edited.png")
            run([os.path.join(HERE, "texpatch.py"), "export", cur, f"--id={ed['id']}", "--png", tgt, "--work", tw],
                f"edit {n} (texpatch export)")
            run([os.path.join(HERE, "texadd.py"), "apply", tgt, dl, res_png], f"edit {n} (texadd)")
            out = run([os.path.join(HERE, "texpatch.py"), "apply", cur, nxt, f"--id={ed['id']}", "--png", res_png, "--work", tw,
                       "--report", os.path.join(work, f"texpatch{n}.json")], f"edit {n} (texpatch)")
            vargs += [f"--bitmap={ed['id']}={res_png}"]
            touched.add(ed.get("label", str(ed["id"])))
        elif t == "necklace":
            glb = source(ed["source"])
            from necklacepatch import NECKLACES
            cfg = NECKLACES[ed["archive"]]
            say(f"edit {n}: necklace {os.path.basename(glb)} -> {cfg['label']} ({cfg['mesh']})")
            nw = os.path.join(work, f"necklace{n}")
            out = run([os.path.join(HERE, "necklacepatch.py"), glb, "--archive", ed["archive"], "--dpc", cur, "--out", nxt,
                       "--work", nw, "--report", os.path.join(work, f"necklace{n}.json")], f"edit {n} (necklacepatch)")
            vargs += [f"--mesh={cfg['mesh']}={os.path.join(nw, cfg['label'] + '.Mesh_Z')}"]
            touched.add(cfg["label"])
        elif t == "texture_delta":
            png = source(ed["source"])
            tw = os.path.join(work, f"tex{n}")
            os.makedirs(tw, exist_ok=True)
            say(f"edit {n}: texture {ed.get('label', '')} ({ed['id']}) += {os.path.basename(png)} - {os.path.basename(ed['original'])}")
            tgt, dlt = os.path.join(tw, "target.png"), os.path.join(tw, "delta_applied.png")
            run([os.path.join(HERE, "texpatch.py"), "export", cur, f"--id={ed['id']}", "--png", tgt, "--work", tw],
                f"edit {n} (texpatch export)")
            run([os.path.join(HERE, "texdelta.py"), source(ed["original"]), png, tgt, dlt, "--report",
                 os.path.join(work, f"texdelta{n}.json")], f"edit {n} (texdelta)")
            out = run([os.path.join(HERE, "texpatch.py"), "apply", cur, nxt, f"--id={ed['id']}", "--png", dlt, "--work", tw,
                       "--report", os.path.join(work, f"texpatch{n}.json")], f"edit {n} (texpatch)")
            vargs += [f"--bitmap={ed['id']}={dlt}"]
            touched.add(ed.get("label", str(ed["id"])))
        elif t == "mesh":
            glb = source(ed["source"])
            mid = int(ed["mesh_id"]) if "mesh_id" in ed else slots[ed["slot"]]
            say(f"edit {n}: mesh {ed['object']} ({os.path.basename(glb)}) -> {ed['slot']} ({mid})")
            mw = os.path.join(work, f"mesh{n}")
            rep = os.path.join(work, f"import{n}.json")
            out = run([os.path.join(HERE, "blendimport.py"), glb, "--object", ed["object"],
                       f"--slot={ed['slot'] if base else mid}", "--dpc", cur, "--out", nxt, "--work", mw, "--report", rep]
                      + ([f"--res={res}"] if res else []), f"edit {n} (blendimport {ed['slot']})")
            r = json.load(open(rep))
            vargs += [f"--replace={mid}={os.path.join(mw, r['slot'] + '.Mesh_Z')}"] + ([f"--resized={mid}"] if r.get("enlarged") else [])
            touched.add(ed["slot"])
        elif t == "merge":
            ids = [int(i) for i in ed["ids"]] if "ids" in ed else [slots[s] for s in ed["slots"]]
            labs = ed.get("slots", [str(i) for i in ids])
            if set(labs) & touched:
                raise BuildError(f"edit {n}: merge uses the original pieces, but {sorted(set(labs) & touched)} were edited before")
            say(f"edit {n}: merge {', '.join(labs)} -> {labs[0]} ({ids[0]}); hide {', '.join(labs[1:])}")
            mw = os.path.join(work, f"merge{n}")
            os.makedirs(mw, exist_ok=True)
            merged = os.path.join(mw, f"{labs[0]}_merged.Mesh_Z")
            run([os.path.join(HERE, "mergemesh.py"), "--res", os.path.join(unpacked, "resources"), "--out", merged,
                 "--report", os.path.join(work, f"merge{n}.json")] + [str(i) for i in ids], f"edit {n} (mergemesh)")
            grown = os.path.join(work, f"step{n}a.tmp.DPC")
            run([os.path.join(HERE, "growdpc.py"), cur, grown, "--mesh", f"{ids[0]}={merged}", "--work", mw], f"edit {n} (growdpc)")
            out = run([os.path.join(HERE, "patch_hide.py"), grown, nxt, os.path.join(work, f"hide{n}"),
                       "--ids=" + ",".join(str(i) for i in ids[1:])], f"edit {n} (patch_hide)")
            vargs += [f"--replace={ids[0]}={merged}", f"--resized={ids[0]}"] + [f"--hide={i}" for i in ids[1:]]
            touched |= set(labs)
        elif t == "atlas":
            if not base:
                raise BuildError(f"edit {n}: atlas is P_AMICIA only")
            spec_p = source(ed["spec"])
            spec = json.load(open(spec_p))
            labs = [lab for g in spec["groups"] for lab in g["pieces"]]
            host = spec["host"]
            if set(labs) & touched:
                raise BuildError(f"edit {n}: atlas uses the original pieces, but {sorted(set(labs) & touched)} were edited before")
            say(f"edit {n}: atlas {os.path.basename(spec_p)}: {len(labs)} pieces -> {host}; hide {', '.join(labs[1:] + spec.get('hide', []))}")
            aw = os.path.join(work, f"atlas{n}")
            run([os.path.join(HERE, "atlasmerge.py"), spec_p, "--out", aw], f"edit {n} (atlasmerge)")
            merged = os.path.join(aw, f"{host}_atlas.Mesh_Z")
            g1 = os.path.join(work, f"step{n}a.tmp.DPC")
            run([os.path.join(HERE, "growdpc.py"), cur, g1, "--mesh", f"{slots[host]}={merged}", "--work", aw], f"edit {n} (growdpc)")
            hid = [slots[x] for x in labs[1:] + spec.get("hide", [])]
            g2 = os.path.join(work, f"step{n}b.tmp.DPC")
            run([os.path.join(HERE, "patch_hide.py"), g1, g2, os.path.join(work, f"hide{n}"), "--ids=" + ",".join(str(i) for i in hid)],
                f"edit {n} (patch_hide)")
            fsx.remove(g1)
            from hosttex import HOST, apply_layouts
            paths = {k: os.path.join(aw, f"game_{k}.png") for k in ("colour", "normal", "mask")}
            try:
                steps = apply_layouts(g2, nxt, paths, aw)
            except ValueError as e:
                raise BuildError(f"edit {n} (texpatch): {e}")
            for st in steps:
                say(f"   {st['texture']}: " + " | ".join(st["log"]))
            fsx.remove(g2)
            out = ""
            vargs += [f"--replace={slots[host]}={merged}", f"--resized={slots[host]}"] + [f"--hide={i}" for i in hid]
            # whole new textures: the BC encoder's error on resampled game textures is RMSE 3.0 (B12, BC1) and 4.7-7.2
            # (B13, B16, BC3: three unrelated channels share one colour block), so these limits instead of 2.5
            lim = dict(colour=4.0, normal=8.0, mask=2.5)
            vargs += [f"--bitmap={HOST[k]}={paths[k]}@{lim[k]}" for k in ("colour", "normal", "mask")]
            touched |= set(labs) | set(spec.get("hide", []))
        elif t == "full_body" and "archives" in ed:   # M16: one edit for every listed archive
            v2, t2, out = body_multi(ed, n, arch, base, cur, nxt, work, unpacked)
            vargs += v2
            touched |= t2
        elif t == "full_body":
            if not base:
                raise BuildError(f"edit {n}: full_body is P_AMICIA only")
            import hosts
            import hosttex
            glb = source(ed["source"])
            keep = list(ed.get("keep", []))
            table = {r["label"]: r for r in hosts.table()}
            assigned = []
            for pt in ed["parts"]:
                kind = pt.get("kind", "cloth")
                if kind == "hair":
                    raise BuildError(f"edit {n}: hair is not part of full_body: use a separate \"hair\" edit (part '{pt['object']}')")
                host = pt.get("host")
                if host is None:
                    host = next((h for h in hosts.recommend(kind) if not any(
                        set(table[h]["replaces"]) & set(table[x]["replaces"]) for _, x in assigned)), None)
                    if host is None:
                        raise BuildError(f"edit {n}: no free {kind} host left for part '{pt['object']}'")
                    say(f"edit {n}: part '{pt['object']}': host {host} chosen ({kind}, best free host of list-hosts)")
                if host not in table or not table[host]["importable"]:
                    raise BuildError(f"edit {n}: {host} is not an importable slot")
                for o, x in assigned:
                    if set(table[host]["replaces"]) & set(table[x]["replaces"]):
                        raise BuildError(f"edit {n}: parts '{o}' and '{pt['object']}' use hosts sharing textures ({x}, {host})")
                if host in keep:
                    raise BuildError(f"edit {n}: {host} is a host and cannot be kept")
                assigned.append((pt["object"], host))
            hosts_used = [h for _, h in assigned]
            hide = sorted((set(hosts.OUTFIT) | {u for h in hosts_used for u in table[h]["replaces"]}) - set(hosts_used) - set(keep))
            import hairedit   # hair is optional and only a "hair" edit touches it: full_body never hides or hosts a hair piece
            hairlabs = {x[1] for x in hairedit.hair_set("P_AMICIA")}
            if hairlabs & (set(hide) | set(hosts_used)):
                raise BuildError(f"edit {n}: full_body would touch hair pieces {sorted(hairlabs & (set(hide) | set(hosts_used)))}")
            say(f"edit {n}: full body {os.path.basename(glb)}: " + ", ".join(f"'{o}' -> {h}" for o, h in assigned)
                + f"; hide {', '.join(hide)}" + (f"; keep {', '.join(keep)}" if keep else ""))
            fw = os.path.join(work, f"body{n}")
            os.makedirs(fw, exist_ok=True)
            for k, (pt, (obj, host)) in enumerate(zip(ed["parts"], assigned)):
                mid = slots[host]
                mw = os.path.join(fw, obj)
                rep_p = os.path.join(fw, f"import_{obj}.json")
                m1 = os.path.join(work, f"step{n}_{k}m.tmp.DPC")
                run([os.path.join(HERE, "blendimport.py"), glb, "--object", obj, f"--slot={host}", "--dpc", cur, "--out", m1,
                     "--work", mw, "--report", rep_p], f"edit {n} (blendimport '{obj}' -> {host})")
                r = json.load(open(rep_p))
                vargs += [f"--replace={mid}={os.path.join(mw, r['slot'] + '.Mesh_Z')}"] + ([f"--resized={mid}"] if r.get("enlarged") else [])
                spec = hosttex.host_spec(host)
                try:
                    ims, notes = hosttex.gltf_images(glb, obj)
                    if pt.get("mask"):
                        ims["mask"] = hosttex.read_png(source(pt["mask"]))
                    lay, n2 = hosttex.build_layouts(ims.get("colour"), ims.get("normal"), ims.get("metal"), ims.get("rough"),
                                                    ims.get("mask"), bool(pt.get("compensate", False)), spec)
                    n2 += hosttex.reserve(lay, spec, [x for x in keep if set(table[x]["replaces"]) & set(table[host]["replaces"])])
                    for x in notes + n2:
                        say(f"   '{obj}' textures: {x}")
                    paths = hosttex.write_layouts(lay, os.path.join(mw, "textures"), spec)
                    t1 = os.path.join(work, f"step{n}_{k}t.tmp.DPC")
                    steps = hosttex.apply_layouts(m1, t1, paths, mw, spec=spec)
                except (ValueError, KeyError) as e:
                    raise BuildError(f"edit {n} (textures '{obj}'): {e}")
                for st in steps:
                    say(f"   '{obj}' {st['texture']}: " + " | ".join(st["log"]))
                fsx.remove(m1)
                if cur != src and os.path.basename(cur).startswith(f"step{n}_"):
                    fsx.remove(cur)
                cur = t1
                lim = dict(colour=4.0, normal=8.0, mask=2.5)   # whole new textures: see the atlas edit
                vargs += [f"--bitmap={spec[x]['id']}={paths[x]}@{lim[x]}" for x in paths]
            out = run([os.path.join(HERE, "patch_hide.py"), cur, nxt, os.path.join(work, f"hide{n}"),
                       "--ids=" + ",".join(str(slots[x]) for x in hide)], f"edit {n} (patch_hide)")
            fsx.remove(cur)
            vargs += [f"--hide={slots[x]}" for x in hide]
            touched |= set(hide) | set(hosts_used)
        elif t == "hair":
            import hairedit
            glb = source(ed["source"])
            obj = ed.get("object", "hair")
            rdir = os.path.join(unpacked, "resources")
            try:
                hs = hairedit.hair_set(arch[:-4], rdir)
                want = ed.get("host")
                hid_, hlab, mode = hairedit.pick_host(hs, int(want) if isinstance(want, int) or (want or "").lstrip("-").isdigit() else want)
            except ValueError as e:
                raise BuildError(f"edit {n}: {e}")
            hair = {x[1]: x for x in hs}
            if set(hair) & touched:
                raise BuildError(f"edit {n}: hair pieces {sorted(set(hair) & touched)} were edited before the hair edit")
            others = [x for x in hs if x[0] != hid_]
            say(f"edit {n}: hair '{obj}' ({os.path.basename(glb)}) -> {hlab} ({hid_}), texture mode {mode}; "
                f"hide {', '.join(x[1] for x in others)}")
            hw = os.path.join(work, f"hair{n}")
            os.makedirs(hw, exist_ok=True)
            remap = ed.get("test_uv_remap")
            tex = ed.get("texture")
            if remap and tex:
                raise BuildError(f"edit {n}: test_uv_remap (UVs onto B01) and an own \"texture\" exclude each other")
            if remap:   # TEST ONLY: own-texture UVs moved onto B01 strands in a copy; the source glb stays untouched
                if remap not in ("islands", "strip"):
                    raise BuildError(f"edit {n}: test_uv_remap must be \"islands\" or \"strip\", not {remap!r}")
                if "geo" not in name:
                    raise BuildError(f"edit {n}: test_uv_remap is for geometry-only tests: the test name must contain \"geo\" ({name})")
                rglb = os.path.join(hw, "hair_b01remap.glb")
                out = run([os.path.join(HERE, "hairuvmap.py"), glb, rglb, "--object", obj, "--mode", remap,
                           "--report", os.path.join(hw, "hair_b01remap.json")], f"edit {n} (hairuvmap, TEST ONLY)")
                say("   " + out.strip().splitlines()[-1])
                glb = rglb
            rep = os.path.join(work, f"import{n}.json")
            m1 = os.path.join(work, f"step{n}m.tmp.DPC")
            run([os.path.join(HERE, "blendimport.py"), glb, "--object", obj, f"--slot={hlab if base else hid_}", "--dpc", cur,
                 "--out", m1, "--work", hw, "--report", rep, "--bone-fallback"] + ([f"--res={res}"] if res else []),
                f"edit {n} (blendimport '{obj}' -> {hlab})")
            r = json.load(open(rep))
            vargs += [f"--replace={hid_}={os.path.join(hw, r['slot'] + '.Mesh_Z')}"] + ([f"--resized={hid_}"] if r.get("enlarged") else [])
            c1 = m1
            if tex:   # route B: the own texture into the host's colour texture (B01 / 1-00), a flat normal into B02 / 1-10
                d = hair_texture(tex, n)
                nf = "normal_flat" if tex.get("normal", "flat") == "flat" else "normal_own"
                for k, (to, f, png, lim) in enumerate(((hair[hlab][4], "hair_colour.bc3", "hair_rgba.png", 4.0),
                                                       (hair[hlab][5], f"{nf}.bc3", f"{nf}.png", 3.0 if nf == "normal_flat" else 8.0))):
                    t1 = os.path.join(work, f"step{n}t{k}.tmp.DPC")
                    out = run([os.path.join(HERE, "hairtex.py"), "put", c1, t1, "--res", rdir, "--id", str(to),
                               "--body", os.path.join(d, f), "--work", hw], f"edit {n} (hairtex put {f} -> {to})")
                    say("   " + out.strip().splitlines()[-1])
                    vargs += [f"--bitmap={to}={os.path.join(d, png)}@{lim}"] + ([f"--resized={to}"] if "slot enlarged" in out else [])
                    fsx.remove(c1)
                    c1 = t1
            elif mode == "own":   # CHP1: the host draws its own textures; the new hair's UVs are on B01, so B01 / B02 go there
                for k, (frm, to) in enumerate(((hairedit.B01, hair[hlab][4]), (hairedit.B02, hair[hlab][5]))):
                    t1 = os.path.join(work, f"step{n}t{k}.tmp.DPC")
                    out = run([os.path.join(HERE, "hairedit.py"), "copy", c1, t1, "--res", rdir, "--from", str(frm), "--to", str(to),
                               "--work", hw], f"edit {n} (texture copy {frm} -> {to})")
                    png = os.path.join(hw, f"expect_{to}.png")
                    run([os.path.join(HERE, "texpatch.py"), "export", src, f"--id={frm}", "--png", png, "--work", hw],
                        f"edit {n} (export {frm} for the check)")
                    vargs += [f"--bitmap={to}={png}@0.01"] + ([f"--resized={to}"] if "slot enlarged" in out else [])
                    fsx.remove(c1)
                    c1 = t1
            out = run([os.path.join(HERE, "patch_hide.py"), c1, nxt, os.path.join(work, f"hide{n}"),
                       "--ids=" + ",".join(str(x[0]) for x in others)], f"edit {n} (patch_hide)")
            fsx.remove(c1)
            vargs += [f"--hide={x[0]}" for x in others]
            touched |= set(hair)
        elif t == "grow":
            ids = [int(i) for i in ed["ids"]] if "ids" in ed else [slots[s] for s in ed["slots"]]
            say(f"edit {n}: enlarge {', '.join(ed.get('slots', [str(i) for i in ids]))} by {ed['sectors']} sectors each (content unchanged)")
            out = run([os.path.join(HERE, "growdpc.py"), cur, nxt] + [x for i in ids for x in ("--pad", f"{i}={ed['sectors']}")],
                      f"edit {n} (growdpc)")
            vargs += [f"--grown={i}" for i in ids]
        elif t == "hide":
            ids = [int(i) for i in ed["ids"]] if "ids" in ed else [slots[s] for s in ed["slots"]]
            say(f"edit {n}: hide {','.join(ed['slots'])} ({','.join(str(i) for i in ids)})")
            out = run([os.path.join(HERE, "patch_hide.py"), cur, nxt, os.path.join(work, f"hide{n}"),
                       "--ids=" + ",".join(str(i) for i in ids)], f"edit {n} (patch_hide)")
            for i in ids:
                vargs += [f"--hide={i}"]
            touched |= set(ed["slots"])
        else:
            raise BuildError(f"edit {n}: unknown type {t}")
        summary.append(dict(edit=n, type=t, lz4hc="lz4hc (" in out or "with lz4hc" in out, step=os.path.basename(nxt)))
        cur = nxt
    if not summary:
        raise BuildError("no edits for this archive")
    say("verify:")
    run([os.path.join(HERE, "verify_face.py"), src, cur, unpacked, os.path.join(work, "unpacked"), "--allow-slot-gaps"] + vargs,
        "verify")
    for c in spec.get("checks", []):
        if "slot_equals" in c:
            lab = c["slot_equals"]
            if lab not in touched:
                say(f"check {lab} slot equals {c['file']}: skipped ({lab} not built)")
                continue
            ref = os.path.join(ROOT, c["file"].replace("/", os.sep))
            same = slot_content(cur, slots[lab]) == slot_content(ref, slots[lab])
            say(f"check {lab} slot equals {c['file']}: {same}")
            if not same:
                raise BuildError("FAILED at checks")
        if "same_positions" in c:
            lab = c["same_positions"]
            if lab not in built_faces:
                say(f"check {lab} positions equal {c['file']}: skipped ({lab} not built)")
                continue
            pa, pb = positions(built_faces[lab]), positions(os.path.join(ROOT, c["file"].replace("/", os.sep)))
            d = max(math.dist(p, q) for p, q in zip(pa, pb)) if len(pa) == len(pb) else None
            ok = d is not None and d < 2e-6
            say(f"check {lab} positions equal {c['file']}: {ok} (vertices {len(pa)} / {len(pb)}, max difference "
                f"{d * 1000 if d is not None else float('nan'):.4f} mm)")
            if not ok:
                raise BuildError("FAILED at checks")
    say("lz4hc used:", [s["edit"] for s in summary if s["lz4hc"]] or "no")
    dst_dir = a.dest_dir or os.path.join(OUT, f"test_{name}")
    os.makedirs(dst_dir, exist_ok=True)
    tmp = os.path.join(dst_dir, arch + ".tmp")
    shutil.copyfile(cur, tmp)
    if sha(tmp) != sha(cur):
        raise BuildError("copy hash mismatch")
    fsx.replace(tmp, os.path.join(dst_dir, arch))
    say(f"READY: {os.path.join(dst_dir, arch)} sha256 {sha(cur)}")
    json.dump(dict(manifest=man_path, edits=summary, log=list(LOG), archive=arch, original=src, unpacked=unpacked,
                   verify_args=vargs, output=os.path.join(dst_dir, arch), sha256=sha(cur)),
              open(os.path.join(work, "build_summary.json"), "w"), indent=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default=os.path.join(OUT, "mod", "amicia_mod.json"))
    ap.add_argument("--build", default="all")
    ap.add_argument("--name", help="override the base test name (rehearsals)")
    ap.add_argument("--only")
    ap.add_argument("--dest-dir")
    a = ap.parse_args()
    man = json.load(open(a.manifest))
    import bodyroute   # M16: a full_body edit with "archives" adds the archives it lists
    specs = [(s, s.pop("base")) for s in bodyroute.expand(man)]
    if a.name:
        specs[0][0]["test_name"] = a.name
    if a.build != "all":
        specs = [(s, b) for s, b in specs if s["test_name"] == a.build]
        if not specs:
            raise SystemExit(f"no archive with test name {a.build}")
    if (a.only or a.dest_dir) and len(specs) != 1:
        raise SystemExit("--only and --dest-dir need --build <one test name>")
    failed = []
    for spec, is_base in specs:
        if not spec["edits"]:
            say(f"=== {spec['test_name']}: no edits in the manifest: nothing built ({spec.get('note', '')})")
            continue
        try:
            if spec["archive"].upper() == "CUSTOMIZATION.DPC":
                build_xple(spec, a, a.manifest)
            else:
                build(spec, is_base, a, a.manifest)
        except BuildError as e:
            say(str(e))
            failed.append(spec["test_name"])
    print("SUMMARY:", "all built" if not failed else f"FAILED: {', '.join(failed)}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
