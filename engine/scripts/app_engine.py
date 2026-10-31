"""M17 session 1: the only door between the app's pages and the engine (plan §3.1, v5 §0.2, §18). Stdlib only.

Commands (the last line printed is always one JSON object, the result; "PROGRESS i/n text" lines come before it):
  detect-game [--steam <folder>]      the game folder: <Steam>\\steamapps\\libraryfolders.vdf's libraries, each
                                      \\steamapps\\common\\A Plague Tale Innocence with DATAS\\P_AMICIA.DPC. Without --steam
                                      the Steam folder comes from the registry (HKCU\\Software\\Valve\\Steam, SteamPath).
  state <game folder>                 each of Amicia's six files: original / changed / missing (SHA-256 against the
                                      supported list); "supported" = all six original (the Steam version).
  prepare <game folder> --data <dir>  verified copies of the six originals in <data>\\originals (each copied under a
                                      temporary name, hash-checked, renamed), unpacked into <data>\\work\\<ARCHIVE>_unpacked
                                      (dpcunpack.py). Done once; later calls only check.
  check <choices.json> --data <dir>   the inputs prepared (appinputs.py) into <data>\\inputs and checked: check-model
                                      on the model with the app's hosts, the hair check; {"fatal": [...], "notes": [...]},
                                      each item {code, text, advice}.
  build <choices.json> --data <dir>   check, then the manifest (today's format, <data>\\inputs\\manifest.json) and
                                      build_mod.py for one game file at a time into <data>\\built (each work folder freed
                                      after its file).
  kits --data <dir>                   the kit folders made from the player's own game files into <data>\\kits (once;
                                      a stamp holds the originals' hashes).
  inspect <model.glb|.gltf>           the Model / Hair pages' summary: parts, vertices, the textures found in the file
                                      (colour, normal, roughness, metallic: found + size).
  make-mod --data <dir> --name N --version V --description D --out <dir> [--preview <png>] [--changes a,b] [--tool-page URL]
           [--groups g,h]
                                      session 3: <data>\\built -> the mod folder + .zip (modinstall.py, makepatch.py;
                                      the self-test must pass).
  install <mod folder|.zip> <game folder> --data <dir> [--replace]
                                      session 3 (plan §5.3): {"ok": true} / {"confirm_replace": {...}} (another mod is
                                      installed: ask, then again with --replace) / {"needs_elevation": true, "plan": p}
                                      (the exe runs "copy-in <p>" elevated) / {"ok": false, "error": {code, text, advice}}.
  restore <game folder> --data <dir>  every file this tool changed back to the original (the same answers).
  copy-in <plan.json>                 the only command that writes into the game folder (the exe runs it elevated).
  state <game folder> [--data <dir>]  with --data: also "ours", the installed mod, an interrupted install.
choices.json (what the pages chose; paths absolute):
  {"groups": ["chp1", "chp2", "main", "final"],                       optional (Advanced); default all
   "model": {"cloth": {"file", "colour"?, "normal"?, "roughness"?, "metallic"?, "overlay"?},
             "skin":  {"file", ... the same}},                         optional; both parts required when given
   "hair": {"file", "colour"?, "alpha"?, "rgba"?, "normal"?},          optional; none: the game's hair stays
   "eyes": {"colour": <png>}}                                          optional; the iris on the game's eyes
The model route (v5): Cloth and Skin each in their own slot in every chapter group (§18 table), the game's head, hands
and every other piece hidden, only its eyes and lashes kept; texture size = the part's colour image size (2048 or 4096
written at that size; 1024 or smaller: the slot's own size).
Folders: MODLAB_ROOT = <data>\\work, MODLAB_ORIGINALS = <data>\\originals for every engine step (paths.py).
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paths  # noqa: E402
import fsx  # noqa: E402  (renames with a retry: Windows may hold a new file)

FILES = ["P_AMICIA.DPC", "P_AMICIA_CHP1.DPC", "P_AMICIA_CHP2.DPC", "P_AMICIA_END.DPC", "P_AMICIA_UNIVERSITY.DPC",
         "CUSTOMIZATION.DPC"]
GAME_DIR = os.path.join("steamapps", "common", "A Plague Tale Innocence")
GROUPS = {"chp1": ["P_AMICIA_CHP1"], "chp2": ["P_AMICIA_CHP2"], "main": ["P_AMICIA", "P_AMICIA_UNIVERSITY"],
          "final": ["P_AMICIA_END"]}
HOSTS = {"P_AMICIA": ("M42", "M38"), "P_AMICIA_CHP1": ("C1-22", "C1-10"), "P_AMICIA_CHP2": ("C2-16", "C2-26"),
         "P_AMICIA_END": ("E-25", "E-38"), "P_AMICIA_UNIVERSITY": ("U-40", "U-46")}   # (skin, cloth), plan §18
EXTRA_HIDE = {"P_AMICIA_END": ["E-05"]}   # the final chapters' knee skin (proven in test_cloth4k)
SHORT = {"P_AMICIA": "base", "P_AMICIA_CHP1": "chp1", "P_AMICIA_CHP2": "chp2", "P_AMICIA_END": "end",
         "P_AMICIA_UNIVERSITY": "university", "CUSTOMIZATION": "custom"}
IRIS = 7468423702097631877   # B31, the same id in every Amicia file
COLOUR_LIMIT = 8.0   # VERIFY's colour limit for the user's parts: their textures are never softened (§18)


def rmtree(d):
    import dpcunpack   # a delete with a short retry (Windows may still hold a just-written file)
    dpcunpack.rmtree(d)


def result(obj):
    print(json.dumps(obj), flush=True)


def progress(i, n, text):
    print(f"PROGRESS {i}/{n} {text}", flush=True)


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest().upper()


def known_hashes():
    raw = open(paths.HASHES, "rb").read()
    text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8", "replace")
    out = {}
    for line in text.splitlines():
        parts = line.split()
        if parts and len(parts[0]) == 64:
            out[re.split(r"[\\/]", line.rstrip())[-1].upper()] = parts[0].upper()
    return out


# ---------- the game ----------
def steam_folder():
    import winreg   # only in the app on the player's PC (never run during development: the rules)
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
        return winreg.QueryValueEx(k, "SteamPath")[0].replace("/", os.sep)


def libraries(steam):
    libs = [steam]
    vdf = os.path.join(steam, "steamapps", "libraryfolders.vdf")
    if os.path.isfile(vdf):
        for m in re.finditer(r'"path"\s+"([^"]+)"', open(vdf, encoding="utf-8", errors="replace").read()):
            p = m.group(1).replace("\\\\", "\\")
            if os.path.normcase(p) not in map(os.path.normcase, libs):
                libs.append(p)
    return libs


def detect_game(a):
    steam = a.steam or steam_folder()
    for lib in libraries(steam):
        g = os.path.join(lib, GAME_DIR)
        if os.path.isfile(os.path.join(g, "DATAS", "P_AMICIA.DPC")):
            return result(dict(game=g))
    result(dict(game=None))


def file_state(game):
    known = known_hashes()
    files = {}
    for f in FILES:
        p = os.path.join(game, "DATAS", f)
        files[f] = "missing" if not os.path.isfile(p) else "original" if sha256(p) == known.get(f.upper()) else "changed"
    return files


def state(a):
    if a.data:
        import modinstall
        return result(modinstall.status(a.game, a.data, known_hashes()))
    files = file_state(a.game)
    result(dict(files=files, supported=all(v == "original" for v in files.values())))


# ---------- the kits, made from the player's own files (session 4, plan §9.1) ----------
KIT_FOLDERS = ["Amicia Clean slate + weight body", "Amicia Parts", "hair_kit", "head_kit", "Amicia Seamless Textures"]


def kit_readmes():
    """Our own README texts for the kit folders: engine\\data\\kit_readmes (release) or output\\blender_kit_v2 (development)."""
    for base in (os.path.join(paths.TABLES, "data", "kit_readmes"), os.path.join(paths.REPO, "output", "blender_kit_v2")):
        if os.path.isfile(os.path.join(base, "README.md")):
            return base
    return None


OPT_KIT = "0 Optimized Kit"   # session 68 (user): the author's own Blender kit, first in the kits folder


def optimized_kit_src():
    """OptimizedKit.blend: engine\\data\\optimized_kit (release) or output\\kits_src (development)."""
    for p in (os.path.join(paths.TABLES, "data", "optimized_kit", "OptimizedKit.blend"),
              os.path.join(paths.REPO, "output", "kits_src", "OptimizedKit.blend")):
        if os.path.isfile(p):
            return p
    return None


def put_optimized_kit(kits_dir):
    """kits\\0 Optimized Kit\\ (OptimizedKit.blend + README.md), copied under a temporary name, size checked."""
    src = optimized_kit_src()
    if not src:
        return False
    d = os.path.join(kits_dir, OPT_KIT)
    os.makedirs(d, exist_ok=True)
    dst = os.path.join(d, "OptimizedKit.blend")
    if not (os.path.isfile(dst) and os.path.getsize(dst) == os.path.getsize(src)):
        shutil.copyfile(src, dst + ".tmp")
        if os.path.getsize(dst + ".tmp") != os.path.getsize(src):
            fsx.remove(dst + ".tmp")
            return False
        fsx.replace(dst + ".tmp", dst)
    rd = kit_readmes()
    if rd and os.path.isfile(os.path.join(rd, OPT_KIT, "README.md")):
        shutil.copyfile(os.path.join(rd, OPT_KIT, "README.md"), os.path.join(d, "README.md"))
    return True


def kits(a):
    """data\\kits\\<the five folders>, from data\\work (prepare's unpacked originals): blender_kit.py, mockbody.py,
    hairkit.py, facekit.py (the version-1 makers, into work\\blender_kit), kitv2.py, seamless.py. Made once; the stamp
    kits_ready.txt holds the originals' hashes: a kit set made from other files is made again."""
    known = known_hashes()
    stamp_text = "\n".join(f"{f} {known[f.upper()]}" for f in FILES)
    dst = os.path.join(a.data, "kits")
    stamp = os.path.join(dst, "kits_ready.txt")
    if os.path.isfile(stamp) and open(stamp).read() == stamp_text and all(os.path.isdir(os.path.join(dst, k)) for k in KIT_FOLDERS):
        put_optimized_kit(dst)   # kits made by an older tool: only the new kit is added
        return result(dict(ok=True, folder=dst, made=False))
    work = os.path.join(a.data, "work")
    if not os.path.isfile(os.path.join(work, "P_AMICIA_unpacked", "prepared.txt")):
        return result(dict(ok=False, error=dict(code="not_prepared", text="the game files are not read yet", advice="go back to the first page and start again")))
    v1 = os.path.join(work, "blender_kit")
    tmp = dst + ".tmp"
    for d in (v1, tmp):
        if os.path.isdir(d):
            rmtree(d)
    os.makedirs(tmp)
    py = [sys.executable, "-I"]
    s = lambda name: os.path.join(HERE, name)
    steps = [("the skeleton and the game's pieces", [s("blender_kit.py"), work]),
             ("the example body", [s("mockbody.py"), "--parts", "leather,fabric,skin", "--out", os.path.join(v1, "mock_body_m16.glb"),
                                   "--mask-prefix", "mock_body_m16_mask_", "--work", os.path.join(work, "import_work", "mockbody")]),
             ("the hair kit", [s("hairkit.py"), os.path.join(v1, "hair_kit")]),
             ("the head kit", [s("facekit.py"), os.path.join(v1, "face_kit")]),
             ("the kit files", [s("kitv2.py"), v1, tmp]),
             ("the seamless textures (the longest step)", [s("seamless.py"), os.path.join(tmp, "Amicia Seamless Textures")])]
    logs = os.path.join(a.data, "logs")
    os.makedirs(logs, exist_ok=True)
    for i, (text, args) in enumerate(steps, 1):
        progress(i, len(steps), f"making {text}")
        log = os.path.join(logs, f"kits_{i}.log")
        with open(log, "w", encoding="utf-8") as f:
            r = subprocess.run(py + args, stdout=f, stderr=subprocess.STDOUT, env=env(a.data), cwd=work)
        if r.returncode:
            tail = [x.rstrip() for x in open(log, encoding="utf-8", errors="replace")][-4:]
            return result(dict(ok=False, error=dict(code="kits_failed", text=f"making {text} stopped: " + " | ".join(tail)[-500:],
                                                    advice="see the log in the work folder (Advanced)"), log=log))
        # the version-1 makers write <name>.tmp: a .glb passes glbcheck, a .png must be a PNG; then the real name
        for t in [os.path.join(r, f) for r, _, fs in os.walk(v1) for f in fs if f.endswith(".tmp")]:
            if t.endswith(".glb.tmp"):
                c = subprocess.run(py + [s("glbcheck.py"), t], capture_output=True, text=True)
                bad = c.returncode or "FAIL" in c.stdout
            else:
                bad = t.endswith(".png.tmp") and open(t, "rb").read(8) != b"\x89PNG\r\n\x1a\n"
            if bad:
                return result(dict(ok=False, error=dict(code="kits_failed", text=f"{os.path.basename(t)}: the file check failed")))
            fsx.replace(t, t[:-4])
    rd = kit_readmes()
    if rd:
        shutil.copyfile(os.path.join(rd, "README.md"), os.path.join(tmp, "README.md"))
        for k in KIT_FOLDERS[:4]:   # the seamless library writes its own README (seamless.py)
            p = os.path.join(rd, k, "README.md")
            if os.path.isfile(p) and os.path.isdir(os.path.join(tmp, k)):
                shutil.copyfile(p, os.path.join(tmp, k, "README.md"))
    put_optimized_kit(tmp)
    missing = [k for k in KIT_FOLDERS if not os.path.isdir(os.path.join(tmp, k))]
    if missing:
        return result(dict(ok=False, error=dict(code="kits_failed", text=f"kit folders missing: {missing}")))
    slog = os.path.join(tmp, "Amicia Seamless Textures_log.txt")   # seamless.py's log: to the logs, not the kits
    if os.path.isfile(slog):
        fsx.replace(slog, os.path.join(logs, "kits_seamless.txt"))
    open(os.path.join(tmp, "kits_ready.txt"), "w").write(stamp_text)
    if os.path.isdir(dst):
        rmtree(dst)
    fsx.replace(tmp, dst)
    rmtree(v1)
    result(dict(ok=True, folder=dst, made=True))


# ---------- the pages' model summary (session 4) ----------
def inspect(a):
    import appinputs
    try:
        doc, blob = appinputs.load(a.model)
    except appinputs.InputError as e:
        return result(dict(ok=False, error={k: v for k, v in e.item.items() if k != "level"}))
    nodes = [n for n in doc.get("nodes", []) if "mesh" in n]
    verts, mats = 0, []
    for n in nodes:
        for pr in doc["meshes"][n["mesh"]]["primitives"]:
            verts += doc["accessors"][pr["attributes"]["POSITION"]]["count"]
            if pr.get("material") is not None and pr["material"] not in mats:
                mats.append(pr["material"])

    def image(ref):
        if ref is None:
            return None
        im = doc["images"][doc["textures"][ref["index"]]["source"]]
        data = appinputs.view_bytes(doc, blob, im["bufferView"]) if "bufferView" in im else b""
        wh = appinputs.png_info(data)
        return dict(found=True, size=list(wh) if wh else None, png=bool(wh))

    tex = {}
    if mats:
        m = doc["materials"][mats[0]]
        pbr = m.get("pbrMetallicRoughness", {})
        mr = image(pbr.get("metallicRoughnessTexture"))
        tex = dict(colour=image(pbr.get("baseColorTexture")), normal=image(m.get("normalTexture")), roughness=mr,
                   metallic=mr if mr and pbr.get("metallicFactor", 1) > 0 else None)
    result(dict(ok=True, parts=len(nodes), vertices=verts, materials=len(mats), skinned=all("skin" in n for n in nodes),
                textures=tex))


# ---------- the mod: make, install, restore (session 3) ----------
def mod_cmd(fn):
    import modinstall
    try:
        result(fn(modinstall))
    except modinstall.ModError as e:
        result(dict(ok=False, error=e.item))
    except (Exception, SystemExit) as e:   # the app always gets one JSON answer
        result(dict(ok=False, error=dict(code="internal", text=f"{type(e).__name__}: {str(e)[-400:]}",
                                         advice="nothing was changed if the game was not running; try again")))


def make_mod(a):
    mod_cmd(lambda m: dict(ok=True, **m.make_mod(os.path.join(a.data, "built"), os.path.join(a.data, "originals"), a.out,
                                                 a.name, a.version, a.description, progress, a.preview,
                                                 (a.changes or "").split(",") if a.changes else [],
                                                 (a.groups or "").split(",") if a.groups else [], a.tool_page)))


def install(a):
    os.makedirs(a.data, exist_ok=True)
    mod_cmd(lambda m: m.install(a.mod, a.game, a.data, known_hashes(), a.replace, progress=progress))


def restore(a):
    mod_cmd(lambda m: m.restore(a.game, a.data, known_hashes()))


def copy_in(a):
    mod_cmd(lambda m: m.copy_in(a.plan))


def prepare(a):
    known = known_hashes()
    orig, work = os.path.join(a.data, "originals"), os.path.join(a.data, "work")
    os.makedirs(orig, exist_ok=True)
    os.makedirs(work, exist_ok=True)
    n = 2 * len(FILES)
    for i, f in enumerate(FILES):
        progress(i + 1, n, f"checking and copying {f}")
        dst = os.path.join(orig, f)
        if os.path.isfile(dst) and sha256(dst) == known[f.upper()]:
            continue
        src = os.path.join(a.game, "DATAS", f)
        if sha256(src) != known.get(f.upper()):
            return result(dict(ok=False, error=dict(code="unsupported", text=f"{f} is not the Steam version's original",
                                                    advice="let Steam verify the game files, then try again")))
        shutil.copyfile(src, dst + ".tmp")
        if sha256(dst + ".tmp") != known[f.upper()]:
            fsx.remove(dst + ".tmp")
            return result(dict(ok=False, error=dict(code="copy_failed", text=f"the copy of {f} is damaged", advice="try again")))
        fsx.replace(dst + ".tmp", dst)
    for i, f in enumerate(FILES):
        progress(len(FILES) + i + 1, n, f"unpacking {f}")
        d = os.path.join(work, f[:-4] + "_unpacked")
        if os.path.isfile(os.path.join(d, "prepared.txt")):
            continue
        if os.path.isdir(d):
            rmtree(d)
        import dpcunpack
        try:
            dpcunpack.unpack(os.path.join(orig, f), d)
        except (SystemExit, Exception) as e:
            return result(dict(ok=False, error=dict(code="unpack_failed", text=f"{f} could not be unpacked: {str(e)[-300:]}")))
        open(os.path.join(d, "prepared.txt"), "w").write(known[f.upper()])
    result(dict(ok=True))


# ---------- inputs, checks, manifest ----------
def env(data):
    e = dict(os.environ)
    e["MODLAB_ROOT"] = os.path.join(data, "work")
    e["MODLAB_ORIGINALS"] = os.path.join(data, "originals")
    return e


def archives_for(ch):
    return [a for g in (ch.get("groups") or list(GROUPS)) for a in GROUPS[g]]


def prepare_inputs(ch, data):
    """appinputs.py: the working copies; returns (dict of prepared things, notes). Raises appinputs.InputError."""
    import appinputs
    inp = os.path.join(data, "inputs")
    if os.path.isdir(inp):
        rmtree(inp)
    os.makedirs(inp)
    got, notes = {}, []
    if ch.get("model"):
        m = ch["model"]
        imgs = {p: {k: v for k, v in m[p].items() if k in ("colour", "normal", "roughness", "metallic") and v}
                for p in ("cloth", "skin")}
        sizes, n = appinputs.model(m["cloth"]["file"], m["skin"]["file"], os.path.join(inp, "body.glb"),
                                   os.path.join(inp, "work"), imgs)
        got["model"] = dict(glb=os.path.join(inp, "body.glb"), sizes=sizes,
                            overlay={p: m[p].get("overlay") for p in ("cloth", "skin") if m[p].get("overlay")})
        notes += n
    if ch.get("hair"):
        h = ch["hair"]
        p, n = appinputs.hair(h["file"], os.path.join(inp, "hair"), h.get("colour"), h.get("alpha"), h.get("rgba"))
        got["hair"] = dict(p, normal=h.get("normal") or "flat")
        notes += n
    if ch.get("eyes"):
        appinputs.iris(ch["eyes"]["colour"])
        got["eyes"] = ch["eyes"]["colour"]
    return got, notes


def model_edit(got, arch_list):
    m = got["model"]
    parts = []
    for obj, kind in (("Skin", "skin"), ("Cloth", "cloth")):
        p = dict(object=obj, kind=kind, compensate=False, colour_limit=COLOUR_LIMIT)
        size = m["sizes"].get(kind)
        if size and size >= 2048:
            p["texture_size"] = size
        if m["overlay"].get(kind):
            p["mask"] = m["overlay"][kind]
        parts.append(p)
    per = {}
    for a in arch_list:
        per[a] = dict(hosts=dict(Skin=HOSTS[a][0], Cloth=HOSTS[a][1]))
        if a in EXTRA_HIDE:
            per[a]["hide"] = EXTRA_HIDE[a]
    return dict(type="full_body", source=m["glb"], archives=arch_list, parts=parts, keep=[], replace_head=True,
                replace_hands=True, keep_game_eyes=True, per_archive=per)


def manifest(got, ch):
    chosen = archives_for(ch)
    body_arch = [a for a in HOSTS if a in chosen]
    hair = got.get("hair")
    tex = dict(colour=hair["colour"], alpha=hair["alpha"], normal=hair["normal"]) if hair else None
    per = {}
    for a in HOSTS:   # the order of build_mod's base + variants
        eds = []
        if hair:   # hair: always every chapter (the game shares one hair texture)
            eds.append(dict(type="hair", source=hair["glb"], object="hair", texture=tex))
        if got.get("eyes") and a in chosen:
            eds.append(dict(type="texture", source=got["eyes"], label="B31", id=IRIS))
        per[a] = eds
    base = per["P_AMICIA"]
    if got.get("model") and body_arch:
        base.insert(0, model_edit(got, body_arch))
    man = dict(about="written by app_engine.py (M17)", archive="P_AMICIA.DPC", test_name="app_base", edits=base,
               variants=[dict(archive=a + ".DPC", test_name=f"app_{SHORT[a]}", edits=per[a]) for a in HOSTS if a != "P_AMICIA"])
    if hair:
        man["variants"].append(dict(archive="CUSTOMIZATION.DPC", test_name="app_custom",
                                    edits=[dict(type="hair_texture", texture=tex, options=["U39", "U24", "U42", "U21"])]))
    return man


def run_check(got, ch, data):
    """check-model on the model (with the app's hosts) and the hair check; returns (fatal, notes)."""
    fatal, notes = [], []
    tmp = os.path.join(data, "inputs", "checks")
    os.makedirs(tmp, exist_ok=True)
    jobs = []
    if got.get("model"):
        ed = model_edit(got, [a for a in HOSTS if a in archives_for(ch)])
        pa = os.path.join(tmp, "per_archive.json")
        json.dump(ed["per_archive"], open(pa, "w"), indent=1)
        jobs.append(("model", [got["model"]["glb"], "--parts", "Cloth=M38,Skin=M42", "--skin", "Skin", "--replace-head",
                               "--replace-hands", "--keep-game-eyes", "--per-archive", pa]))
    if got.get("hair"):
        jobs.append(("hair", [got["hair"]["glb"], "--hair", "--object", "hair"]))
    for name, args in jobs:
        js = os.path.join(tmp, f"{name}.json")
        r = subprocess.run([sys.executable, "-I", os.path.join(HERE, "checkmodel.py")] + args + ["--json", js],
                           capture_output=True, text=True, env=env(data))
        open(os.path.join(tmp, f"{name}.txt"), "w", encoding="utf-8").write(r.stdout + r.stderr)
        if not os.path.isfile(js):
            fatal.append(dict(code="check_failed", text=f"the {name} check stopped: {(r.stderr or r.stdout).strip()[-300:]}",
                              advice="see the full check report"))
            continue
        d = json.load(open(js))
        fatal += [dict(x, part=name) for x in d["fatal"]]
        notes += [dict(x, part=name) for x in d["notes"]]
    return fatal, notes


def load_choices(path):
    ch = json.load(open(path, encoding="utf-8"))
    bad = [g for g in ch.get("groups") or [] if g not in GROUPS]
    if bad:
        raise ValueError(f"unknown chapter groups {bad}")
    if ch.get("model") and not (ch["model"].get("cloth", {}).get("file") and ch["model"].get("skin", {}).get("file")):
        raise ValueError("the model needs both parts: Cloth and Skin")
    return ch


def check_all(a):
    import appinputs
    ch = load_choices(a.choices)
    try:
        got, notes = prepare_inputs(ch, a.data)
    except appinputs.InputError as e:
        return None, None, [dict(e.item, part="inputs")], []
    fatal, n2 = run_check(got, ch, a.data)
    strip = lambda xs: [{k: v for k, v in x.items() if k != "level"} for x in xs]
    return ch, got, strip(fatal), strip([dict(x, part="inputs") for x in notes] + n2)


def check(a):
    _, _, fatal, notes = check_all(a)
    result(dict(fatal=fatal, notes=notes))


def build(a):
    t0 = time.time()
    ch, got, fatal, notes = check_all(a)
    if fatal:
        return result(dict(ok=False, fatal=fatal, notes=notes))
    import bodyroute
    man = manifest(got, ch)
    mp = os.path.join(a.data, "inputs", "manifest.json")
    json.dump(man, open(mp, "w"), indent=1)
    todo = [s for s in bodyroute.expand(man) if s["edits"]]
    out = os.path.join(a.data, "built")
    logs = os.path.join(a.data, "logs")
    if os.path.isdir(out):   # a stopped build leaves its files: start clean
        rmtree(out)
    iw = os.path.join(a.data, "work", "import_work")
    for d in (os.listdir(iw) if os.path.isdir(iw) else []):
        if d.startswith("mod_"):
            rmtree(os.path.join(iw, d))
    os.makedirs(out, exist_ok=True)
    os.makedirs(logs, exist_ok=True)
    files = []
    for i, s in enumerate(todo, 1):
        progress(i, len(todo), f"building {s['archive']}")
        log = os.path.join(logs, f"build_{s['test_name']}.log")
        with open(log, "w", encoding="utf-8") as f:
            r = subprocess.run([sys.executable, "-I", os.path.join(HERE, "build_mod.py"), "--manifest", mp, "--build",
                                s["test_name"], "--dest-dir", out], stdout=f, stderr=subprocess.STDOUT, env=env(a.data))
        wd = os.path.join(a.data, "work", "import_work", f"mod_{s['test_name']}")
        if os.path.isdir(wd):
            rmtree(wd)
        if r.returncode or not os.path.isfile(os.path.join(out, s["archive"])):
            tail = [x.rstrip() for x in open(log, encoding="utf-8", errors="replace")][-6:]
            return result(dict(ok=False, error=dict(code="build_failed", text=f"{s['archive']}: " + " | ".join(tail)[-600:],
                                                    advice="see the build log"), log=log, notes=notes))
        p = os.path.join(out, s["archive"])
        files.append(dict(archive=s["archive"], path=p, bytes=os.path.getsize(p), sha256=sha256(p),
                          work_folder_freed=not os.path.exists(wd)))
    result(dict(ok=True, files=files, notes=notes, manifest=mp, minutes=round((time.time() - t0) / 60, 1)))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("detect-game")
    p.add_argument("--steam")
    p = sub.add_parser("state")
    p.add_argument("game")
    p.add_argument("--data")
    p = sub.add_parser("make-mod")
    p.add_argument("--data", required=True)
    for k in ("name", "version", "description", "out"):
        p.add_argument("--" + k, required=True)
    p.add_argument("--preview")
    p.add_argument("--changes", help="shown on the Install page: e.g. Model,Hair,Eyes")
    p.add_argument("--groups", help="the chapter groups, e.g. chp1,chp2,main,final")
    p.add_argument("--tool-page", help="the tool's page (from the app), named in the mod's README")
    p = sub.add_parser("inspect")
    p.add_argument("model")
    p = sub.add_parser("kits")
    p.add_argument("--data", required=True)
    p = sub.add_parser("install")
    p.add_argument("mod")
    p.add_argument("game")
    p.add_argument("--data", required=True)
    p.add_argument("--replace", action="store_true")
    p = sub.add_parser("restore")
    p.add_argument("game")
    p.add_argument("--data", required=True)
    p = sub.add_parser("copy-in")
    p.add_argument("plan")
    p = sub.add_parser("prepare")
    p.add_argument("game")
    p.add_argument("--data", required=True)
    for c in ("check", "build"):
        p = sub.add_parser(c)
        p.add_argument("choices")
        p.add_argument("--data", required=True)
    a = ap.parse_args()
    dict(**{"detect-game": detect_game, "state": state, "prepare": prepare, "check": check, "build": build,
            "make-mod": make_mod, "inspect": inspect, "kits": kits, "install": install, "restore": restore, "copy-in": copy_in})[a.cmd](a)


if __name__ == "__main__":
    main()
