"""M17 session 3: make a mod, install it, restore the originals (plan §5.1-5.6). Stdlib only. Run by app_engine.py.
<data> is the app's data folder: originals\\ (prepare), backups\\, staging\\, incoming\\, state.json, lock.
  make_mod   built game files -> <out>\\<Mod name>\\ (mod.json, changes.bin, README.txt) + <Mod name>.zip;
             makepatch.py's self-test must pass.
  install    read and check the mod; each of Amicia's six files must be the Steam original or this tool's result;
             another mod installed -> "confirm_replace" (with replace=True: restored first); back up the originals
             (verified); rebuild the new files in staging (applypatch.py, each checked against its result SHA-256);
             copy them into DATAS (copy_in): directly when DATAS is writable, otherwise "needs_elevation" with a plan file
             that the app's exe runs elevated (app_engine copy-in <plan>).
  copy_in    only Amicia's six names, only into the checked DATAS folder, only staged files whose SHA-256 matches; each
             written as <name>.amtmp, flushed, checked and swapped in (MoveFileEx, write-through); on failure the files
             already swapped are put back from the verified backups. Then every target is checked and state.json written.
  restore    every file this tool changed back from its verified backup (the same copy_in).
state.json: {"status": "installing" | "installed", "mod": {name, version, character}, "files": {name: {original,
result, backup}}, "date"}. "installing" left behind = an interrupted install: restore is offered.
Test hook (fake game folders only): MODLAB_FAULT=stop_after:N ends the process after N files were swapped (a power
loss)."""
import ctypes
import hashlib
import json
import os
import fsx
import re
import shutil
import sys
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import applypatch  # noqa: E402
import dpcunpack  # noqa: E402  (rmtree with a retry)
import lz4hc  # noqa: E402

FILES = ["P_AMICIA.DPC", "P_AMICIA_CHP1.DPC", "P_AMICIA_CHP2.DPC", "P_AMICIA_END.DPC", "P_AMICIA_UNIVERSITY.DPC",
         "CUSTOMIZATION.DPC"]
TOOL_VERSION = "0.3"
MOD_FORMAT = 1
MOD_PARTS = {"mod.json", "changes.bin", "README.txt", "preview.png"}
DISCLAIMER = ("This is a fan-made mod. It is not made, approved or supported by the game's developers or publishers.\n"
              "Restore the original files with the tool before reporting any problem with the game.")


class ModError(Exception):
    def __init__(self, code, text, advice=None, **extra):
        super().__init__(text)
        self.item = dict(code=code, text=text, **({"advice": advice} if advice else {}), **extra)


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest().upper()


def write_json(p, obj):
    with open(p + ".tmp", "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1)
        f.flush()
        os.fsync(f.fileno())
    applypatch.replace(p + ".tmp", p)


# ---------- make ----------
def safe_name(s):
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", s).strip().strip(".")
    return s[:60] or "Amicia mod"


def make_mod(built, originals, out, name, version, description, progress=None, preview=None, changes=None, groups=None,
             tool_page=None):
    import makepatch
    folder = os.path.join(out, safe_name(name))
    tmp = folder + ".tmp"
    if os.path.isdir(tmp):
        dpcunpack.rmtree(tmp)
    os.makedirs(tmp)
    t0 = time.time()
    files, stats = makepatch.make(built, originals, os.path.join(tmp, "changes.bin"),
                                  work=os.path.join(out, "_patch_work"), progress=progress)
    mod = dict(format=MOD_FORMAT, tool_version=TOOL_VERSION, lz4hc=lz4hc.VERSION, character="amicia", name=name,
               version=version, description=description, changes=changes or [], groups=groups or [], files=files)
    if preview:   # the app re-encodes the picture as PNG (at most 1280 px wide) before passing it
        data = open(preview, "rb").read()
        if data[:8] != b"\x89PNG\r\n\x1a\n" or len(data) > 8 << 20:
            raise ModError("bad_preview", "the preview picture must be a PNG of at most 8 MB")
        open(os.path.join(tmp, "preview.png"), "wb").write(data)
        mod["preview"] = "preview.png"
    write_json(os.path.join(tmp, "mod.json"), mod)
    open(os.path.join(tmp, "README.txt"), "w", encoding="utf-8").write(
        f"{name}\nVersion {version}\n\n{description}\n\nInstall it with Jga7 Mod Tool (Install a mod).\n"
        + (f"Get Jga7 Mod Tool: {tool_page}\n" if tool_page else "")
        + f"Game files changed: {', '.join(f['name'] for f in files)}\n\n{DISCLAIMER}\n")
    if os.path.isdir(folder):
        dpcunpack.rmtree(folder)
    applypatch.replace(tmp, folder)
    z = folder + ".zip"
    with zipfile.ZipFile(z + ".tmp", "w", zipfile.ZIP_STORED) as zf:   # changes.bin is already compressed
        for f in sorted(os.listdir(folder)):
            zf.write(os.path.join(folder, f), f"{os.path.basename(folder)}/{f}")
    applypatch.replace(z + ".tmp", z)
    return dict(folder=folder, zip=z, patch_mb=stats["_patch_mb"], stats=stats, minutes=round((time.time() - t0) / 60, 1))


# ---------- reading a mod ----------
def read_mod(path, data):
    """A mod folder or .zip -> (folder, mod dict); checks everything before anything is written to the game."""
    if path.lower().endswith(".zip"):
        inc = os.path.join(data, "incoming")
        if os.path.isdir(inc):
            dpcunpack.rmtree(inc)
        os.makedirs(inc)
        try:
            with zipfile.ZipFile(path) as zf:
                for zi in zf.infolist():
                    if zi.is_dir():
                        continue
                    base = zi.filename.replace("\\", "/").split("/")[-1]
                    if base not in MOD_PARTS:
                        raise ModError("bad_mod", f"the mod contains an unexpected file: {zi.filename}",
                                       "only install mods made with this tool")
                    with zf.open(zi) as s, open(os.path.join(inc, base), "wb") as d:
                        shutil.copyfileobj(s, d, 1 << 20)
        except zipfile.BadZipFile:
            raise ModError("bad_mod", "the mod's .zip file is damaged", "download the mod again")
        path = inc
    mj = os.path.join(path, "mod.json")
    if not (os.path.isfile(mj) and os.path.isfile(os.path.join(path, "changes.bin"))):
        raise ModError("bad_mod", "this is not a mod made with this tool (mod.json or changes.bin missing)")
    try:
        mod = json.load(open(mj, encoding="utf-8"))
    except ValueError:
        raise ModError("bad_mod", "the mod's mod.json is damaged", "download the mod again")
    if mod.get("format", 0) > MOD_FORMAT or mod.get("lz4hc", 0) > lz4hc.VERSION:
        raise ModError("needs_newer", "This mod needs a newer version of the tool.", "update the tool, then try again")
    if mod.get("character") != "amicia" or not mod.get("files"):
        raise ModError("bad_mod", "this mod is not a mod for Amicia")
    for f in mod["files"]:
        if f.get("name") not in FILES:
            raise ModError("bad_mod", f"the mod names a file that is not one of Amicia's: {f.get('name')!r}")
        for k in ("original_sha256", "result_sha256"):
            if not re.fullmatch(r"[0-9A-Fa-f]{64}", str(f.get(k, ""))):
                raise ModError("bad_mod", f"the mod's {k} for {f['name']} is missing or damaged")
    names = [f["name"] for f in mod["files"]]
    if len(set(names)) != len(names):
        raise ModError("bad_mod", "the mod names a file twice")
    return path, mod


# ---------- state ----------
def load_state(data):
    p = os.path.join(data, "state.json")
    return json.load(open(p, encoding="utf-8")) if os.path.isfile(p) else None


def file_states(game, known, st):
    """Each of the six files: original / ours / steam_updated / unsupported / missing; plus their hashes."""
    out, hashes = {}, {}
    for f in FILES:
        p = os.path.join(game, "DATAS", f)
        if not os.path.isfile(p):
            out[f] = "missing"
            continue
        h = hashes[f] = sha256(p)
        ours = st and f in st["files"] and h == st["files"][f]["result"]
        if h == known.get(f.upper()):
            out[f] = "original"
        elif ours:
            out[f] = "ours"
        elif st and f in st["files"]:
            out[f] = "steam_updated"   # we changed it, and now it is neither ours nor the original
        else:
            out[f] = "unsupported"
    return out, hashes


def status(game, data, known):
    st = load_state(data)
    fs, _ = file_states(game, known, st)
    r = dict(files=fs, supported=all(v in ("original", "ours") for v in fs.values()), installed=None, interrupted=False)
    if st:
        r["installed"] = st["mod"]
        r["date"] = st.get("date")
        r["interrupted"] = st["status"] != "installed"
        if any(v == "steam_updated" for v in fs.values()):
            r["steam_updated"] = True
        elif not r["interrupted"] and st["files"] and all(fs.get(n) == "original" for n in st["files"]):
            r["steam_verified"] = True   # Steam put every original back (Verify integrity): the mod is gone (session 68)
    return r


# ---------- lock ----------
def _alive(pid):
    k = ctypes.windll.kernel32
    h = k.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    code = ctypes.c_ulong()
    k.GetExitCodeProcess(h, ctypes.byref(code))
    k.CloseHandle(h)
    return code.value == 259   # STILL_ACTIVE


class Lock:
    def __init__(self, data):
        self.p = os.path.join(data, "lock")

    def __enter__(self):
        for _ in range(2):
            try:
                fd = os.open(self.p, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                return self
            except FileExistsError:
                try:
                    pid = int(open(self.p).read().strip() or 0)
                except (OSError, ValueError):
                    pid = 0
                if pid and _alive(pid):
                    raise ModError("busy", "The tool is already installing or restoring.", "wait for it to finish")
                os.remove(self.p)   # left by a stopped run
        raise ModError("busy", "the lock file could not be taken", "close the tool and try again")

    def __exit__(self, *a):
        try:
            os.remove(self.p)
        except OSError:
            pass


# ---------- copy into DATAS ----------
MOVEFILE_REPLACE_EXISTING, MOVEFILE_WRITE_THROUGH = 0x1, 0x8


def denied_why(k, path):
    """After error 5 on path: "read_only" (the attribute), "in_use" (an exclusive open fails with a sharing
    violation: some program has it open) or "denied" (neither: security software or permissions)."""
    k.GetFileAttributesW.argtypes = [ctypes.c_wchar_p]
    k.GetFileAttributesW.restype = ctypes.c_uint32
    a = k.GetFileAttributesW(path)
    if a != 0xFFFFFFFF and a & 0x1:   # FILE_ATTRIBUTE_READONLY
        return "read_only"
    k.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32,
                              ctypes.c_uint32, ctypes.c_void_p]
    k.CreateFileW.restype = ctypes.c_void_p
    h = k.CreateFileW(path, 0x80000000, 0, None, 3, 0, None)   # GENERIC_READ, no sharing, OPEN_EXISTING
    if h is None or h == ctypes.c_void_p(-1).value:
        return "in_use" if ctypes.get_last_error() in (32, 33) else "denied"
    k.CloseHandle(ctypes.c_void_p(h))
    return "denied"


def swap_in(src, dst):
    """MoveFileEx(src -> dst, replace, write-through); a sharing violation (the game running) -> ModError."""
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.MoveFileExW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
    err = 0
    for t in range(4):
        if k.MoveFileExW(src, dst, MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH):
            return
        err = ctypes.get_last_error()
        if t < 3:
            time.sleep(0.5 * (t + 1))
    name = os.path.basename(dst)
    why = denied_why(k, dst) if err == 5 else None
    # Error 5 is ambiguous (session 67): replacing a file another program holds open also gives 5, not 32; the user
    # saw "close the game" with the game closed. denied_why() tells "in use", "read-only" and "denied" apart.
    if err in (32, 33) or why == "in_use":
        raise ModError("game_running", f"{name} is in use (Windows error {err}): the game, Steam or another program "
                       "has it open.", "close the game and wait until Steam is idle, then try again", file=name, error=err)
    if why == "read_only":
        # user, 2026-10-31: name the folder, not the file (the fix is on the DATAS folder)
        datas = os.path.dirname(dst)
        while os.path.basename(datas).upper() != "DATAS" and os.path.dirname(datas) != datas:
            datas = os.path.dirname(datas)
        if os.path.basename(datas).upper() != "DATAS":
            datas = os.path.join(os.path.dirname(dst), "DATAS")
        raise ModError("read_only", f"The DATAS folder in {os.path.dirname(datas)} is marked Read-only "
                       f"(found on {name}), so Windows denied replacing its files (error 5).",
                       "right-click the DATAS folder > Properties, untick Read-only, choose \"Apply changes to this "
                       "folder, subfolders and files\", then try again", file=name, error=err)
    if err == 5:
        raise ModError("access_denied", f"Windows denied replacing {name} (error 5, access denied); the file is not "
                       "open and not Read-only.", "allow the tool in Windows Security (Ransomware protection > "
                       "Controlled folder access) or in your antivirus, then try again", file=name, error=err)
    raise ModError("copy_failed", f"Windows refused to replace {name} (error {err})",
                   "allow the tool in Windows Security (Controlled folder access), then try again")


def write_checked(src, dst_tmp, want):
    h = hashlib.sha256()
    with open(src, "rb") as s, open(dst_tmp, "wb") as d:
        for c in iter(lambda: s.read(1 << 20), b""):
            d.write(c)
            h.update(c)
        d.flush()
        os.fsync(d.fileno())
    if h.hexdigest().upper() != want or sha256(dst_tmp) != want:
        fsx.remove(dst_tmp)
        raise ModError("copy_failed", f"the copy of {os.path.basename(src)} in the game folder is damaged", "try again")


def copy_in(plan_path):
    """The only step that writes into the game folder (run elevated by the app when DATAS is not writable)."""
    plan = json.load(open(plan_path, encoding="utf-8"))
    datas = plan["datas"]
    if not os.path.isfile(os.path.join(datas, "P_AMICIA.DPC")):
        raise ModError("bad_plan", "the game folder in the plan is not the game's DATAS folder")
    for f in plan["files"]:
        if f["name"] not in FILES:
            raise ModError("bad_plan", f"the plan names a file that is not one of Amicia's: {f['name']!r}")
        if sha256(f["src"]) != f["sha256"]:
            raise ModError("copy_failed", f"the staged {f['name']} changed since it was checked", "try again")
    fault = os.environ.get("MODLAB_FAULT", "")
    stop_after = int(fault.split(":")[1]) if fault.startswith("stop_after:") else None
    swapped = []
    try:
        for f in plan["files"]:
            dst = os.path.join(datas, f["name"])
            tmp = dst + ".amtmp"
            write_checked(f["src"], tmp, f["sha256"])
            try:
                swap_in(tmp, dst)
            except ModError:
                fsx.remove(tmp)
                raise
            swapped.append(f)
            if stop_after is not None and len(swapped) >= stop_after:
                os._exit(3)   # test only: a power loss after N files
        bad = [f["name"] for f in plan["files"] if sha256(os.path.join(datas, f["name"])) != f["sha256"]]
        if bad:
            raise ModError("steam_busy", "Steam changed the game files while installing.", "try again when Steam is idle",
                           files=bad)
    except ModError:
        for f in swapped:   # put back what was replaced, from the verified backups
            dst = os.path.join(datas, f["name"])
            write_checked(f["undo"], dst + ".amtmp", f["undo_sha256"])
            swap_in(dst + ".amtmp", dst)
        raise
    if plan.get("state") is not None:
        write_json(plan["state_path"], plan["state"])
    elif plan.get("state_path") and os.path.isfile(plan["state_path"]):
        fsx.remove(plan["state_path"])
    if plan.get("staging") and os.path.isdir(plan["staging"]):
        shutil.rmtree(plan["staging"], ignore_errors=True)
    return dict(ok=True, files=[f["name"] for f in plan["files"]])


def writable(datas):
    p = os.path.join(datas, ".amicia_tool_test")
    try:
        open(p, "wb").close()
        fsx.remove(p)
        return True
    except OSError:
        return False


def run_plan(plan, data, elevate_ok=True):
    pp = os.path.join(data, "plan.json")
    write_json(pp, plan)
    if not writable(plan["datas"]):
        if not elevate_ok:
            raise ModError("needs_elevation", "the game folder is not writable")
        return dict(ok=False, needs_elevation=True, plan=pp)
    r = copy_in(pp)
    fsx.remove(pp)
    return r


# ---------- backups ----------
def backup(game, data, name, want):
    """A verified copy of the original as backups\\<name>.<sha8>.DPC (skipped when one exists)."""
    bd = os.path.join(data, "backups")
    os.makedirs(bd, exist_ok=True)
    p = os.path.join(bd, f"{name[:-4]}.{want[:8]}.DPC")
    if os.path.isfile(p) and os.path.getsize(p) > 0 and sha256(p) == want:
        return p
    src = os.path.join(game, "DATAS", name)
    if sha256(src) != want:
        raise ModError("unsupported", f"{name} is not the Steam original", "let Steam verify the game files")
    write_checked(src, p + ".tmp", want)
    applypatch.replace(p + ".tmp", p)
    return p


def need_space(path, nbytes):
    drive = os.path.splitdrive(os.path.abspath(path))[0] + os.sep   # the drive: a folder without write rights refuses
    try:
        free = shutil.disk_usage(drive).free
    except OSError:
        return   # not measurable: the copy's own checks still catch a full disk
    if free < nbytes:
        raise ModError("no_space", f"Not enough disk space: {nbytes // 2**20} MB needed on {os.path.splitdrive(path)[0]}, "
                                   f"{free // 2**20} MB free", "free some space, then try again")


# ---------- restore ----------
def restore_plan(game, data, known, st):
    datas = os.path.join(game, "DATAS")
    fs, hashes = file_states(game, known, st)
    files = []
    for name, info in (st or {}).get("files", {}).items():
        if fs.get(name) == "original":
            continue
        if fs.get(name) == "steam_updated":
            continue   # a newer game build: never written over (§5.4)
        b = info["backup"]
        if not (os.path.isfile(b) and sha256(b) == info["original"]):
            raise ModError("backup_missing", f"The backup of {name} is missing or damaged.",
                           "Let Steam put the original back: Steam > Library > the game > Properties > Installed files "
                           "> Verify integrity.")
        files.append(dict(name=name, src=b, sha256=info["original"], undo=os.path.join(datas, name),
                          undo_sha256=hashes[name]))
    return files


def restore(game, data, known, elevate_ok=True):
    with Lock(data):
        st = load_state(data)
        if not st:
            return dict(ok=True, restored=[], text="Nothing to restore: no mod installed by this tool.")
        datas = os.path.join(game, "DATAS")
        for f in FILES:   # leftovers of an interrupted copy
            t = os.path.join(datas, f + ".amtmp")
            if os.path.isfile(t):
                fsx.remove(t)
        fs, _ = file_states(game, known, st)
        # files Steam updated since the install: never written over; their old backups are out of date (session 68)
        stale = [n for n in st.get("files", {}) if fs.get(n) == "steam_updated"]
        done = "Original files restored and checked."
        if stale:
            done += f" Left as Steam updated them: {', '.join(stale)} (their old backups were cleared)."

        def clear_stale():
            for n in stale:
                b = st["files"][n].get("backup")
                if b and os.path.isfile(b):
                    fsx.remove(b)
        files = restore_plan(game, data, known, st)
        if not files:   # nothing of ours in the game folder (e.g. the permission prompt was cancelled)
            fsx.remove(os.path.join(data, "state.json"))
            clear_stale()
            return dict(ok=True, restored=[], text=done)
        # undo = the current (ours) file: copied aside first so a failed restore can be put back
        aside = os.path.join(data, "staging", "undo")
        os.makedirs(aside, exist_ok=True)
        for f in files:
            u = os.path.join(aside, f["name"])
            write_checked(f["undo"], u, f["undo_sha256"])
            f["undo"] = u
        r = run_plan(dict(datas=datas, files=files, state=None, state_path=os.path.join(data, "state.json"),
                          staging=os.path.join(data, "staging")), data, elevate_ok)
        if r.get("ok"):
            r["restored"] = r.pop("files")
            r["text"] = done
            clear_stale()
        return r


# ---------- install ----------
def install(mod_path, game, data, known, replace=False, elevate_ok=True, progress=None):
    folder, mod = read_mod(mod_path, data)
    datas = os.path.join(game, "DATAS")
    with Lock(data):
        st = load_state(data)
        fs, hashes = file_states(game, known, st)
        if any(v == "steam_updated" for v in fs.values()):
            # session 68: the state is kept, so Restore can still put back the files Steam did not update
            raise ModError("steam_updated", "Steam updated the game: the installed mod is no longer fully installed.",
                           "nothing was changed; press Restore first (it never writes over the files Steam updated)")
        if st and st["status"] == "installed" and st["files"] and all(fs.get(n) == "original" for n in st["files"]):
            fsx.remove(os.path.join(data, "state.json"))   # Steam already put the originals back: nothing to replace
            st = None
        bad =[f for f, v in fs.items() if v not in ("original", "ours")]
        if bad:
            raise ModError("unsupported", "Unsupported version of the game: only the Steam version is supported. "
                                          "Nothing was changed.", files=bad)
        for f in mod["files"]:
            if known.get(f["name"].upper()) != f["original_sha256"].upper():
                raise ModError("bad_mod", f"this mod was made for another version of {f['name']}")
        if st:
            if not replace:
                return dict(ok=False, confirm_replace=st["mod"],
                            text=f"This replaces {st['mod']['name']}: one Amicia mod at a time.")
    if st:   # the old mod's files all back to the originals first (outside the lock: restore takes it)
        r = restore(game, data, known, elevate_ok)
        if not r.get("ok"):
            return dict(r, step="restore_old")
    with Lock(data):
        st = load_state(data)
        if st:
            raise ModError("restore_failed", "the installed mod could not be removed first", "use Restore, then try again")
        fs, hashes = file_states(game, known, None)
        if any(v != "original" for v in fs.values()):
            raise ModError("unsupported", "Unsupported version of the game: only the Steam version is supported. "
                                          "Nothing was changed.", files=[f for f, v in fs.items() if v != "original"])
        sizes = sum(f["size"] for f in mod["files"])
        need_space(data, 2 * sizes + (256 << 20))
        need_space(datas, max(f["size"] for f in mod["files"]) + (64 << 20))
        backups = {}
        for i, f in enumerate(mod["files"], 1):
            if progress:
                progress(i, len(mod["files"]), f"backing up {f['name']}")
            backups[f["name"]] = backup(game, data, f["name"], f["original_sha256"].upper())
        stage = os.path.join(data, "staging")
        if os.path.isdir(stage):
            dpcunpack.rmtree(stage)
        try:
            applypatch.apply(os.path.join(folder, "changes.bin"), backups, stage, cache_dir=os.path.join(stage, "_cache"),
                             progress=progress)
        except applypatch.PatchError as e:
            shutil.rmtree(stage, ignore_errors=True)
            raise ModError("bad_mod", f"the mod could not be applied: {e}", "nothing was changed; download the mod again")
        files = {f["name"]: dict(original=f["original_sha256"].upper(), result=f["result_sha256"].upper(),
                                 backup=backups[f["name"]]) for f in mod["files"]}
        info = dict(name=mod["name"], version=mod["version"], character=mod["character"])
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        write_json(os.path.join(data, "state.json"), dict(status="installing", mod=info, files=files, date=stamp))
        plan = dict(datas=datas, staging=stage, state_path=os.path.join(data, "state.json"),
                    state=dict(status="installed", mod=info, files=files, date=stamp),
                    files=[dict(name=n, src=os.path.join(stage, n), sha256=v["result"], undo=v["backup"],
                                undo_sha256=v["original"]) for n, v in files.items()])
        try:
            r = run_plan(plan, data, elevate_ok)
        except ModError:
            fsx.remove(os.path.join(data, "state.json"))   # the copy put everything back
            shutil.rmtree(stage, ignore_errors=True)
            pp = os.path.join(data, "plan.json")
            if os.path.isfile(pp):
                fsx.remove(pp)
            raise
        if r.get("ok"):
            r["text"] = "Installed and checked."
        return r
