# Jga7 Mod Tool

Make, install and restore Amicia mods for A Plague Tale: Innocence (Steam). Free Windows tool by Jga7.

**Download the ready tool:** [Gumroad](https://fdxskd.gumroad.com/l/ludzha) ·
[Nexus Mods](https://www.nexusmods.com/aplaguetaleinnocence/mods/48)

This repository holds the tool's source. It contains no game files: the engine reads each player's own game files
and a mod holds only the player's changes.

## Folders
| Folder | What |
|---|---|
| `app/src` | the Windows app (C#, WinForms, .NET Framework 4.8) |
| `app/icon` | the app icon |
| `engine/scripts` | the engine (Python 3.15, standard library only): checks, builds, installs and restores mods, makes the kits |
| `engine/analysis`, `engine/mod`, `engine/previews`, `engine/previews_variants`, `engine/pamicia_refs.tsv` | the engine's tables (offsets, slots and part lists; no game data) |
| `engine/data` | the SHA-256 list of the supported Steam game files and the kit READMEs |

## Build and run
1. Windows 10 or 11 and Python 3.15 (python.org; tick "Add python.exe to PATH").
2. `python build.py` compiles `JgaModTool.exe` in this folder with the C# compiler that comes with Windows.
3. Start `JgaModTool.exe`. Without a bundled Python next to it, the app uses `pythonw.exe` from PATH, or the one in
   the environment variable `JGA_PYTHON`.

The released tool is the same exe with a trimmed Python 3.15 in `engine/python/`, so players install nothing.

Made by Jga7. This tool was created with the help of AI.

*Unofficial fan-made tool. Not made, approved or supported by the game's developers or publishers. Use at your own
risk. Restore the original files before reporting any problem with the game.*
