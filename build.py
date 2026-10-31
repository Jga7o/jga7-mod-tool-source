r"""Compile JgaModTool.exe with Windows' own C# compiler (.NET Framework 4.8; nothing to install).
Writes JgaModTool.exe next to this repository's engine\ folder. Usage: python build.py"""
import glob
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
CSC = r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
REFS = ["System.dll", "System.Core.dll", "System.Drawing.dll", "System.Windows.Forms.dll", "System.Web.Extensions.dll",
        "System.IO.Compression.dll", "System.IO.Compression.FileSystem.dll"]


def main():
    exe = os.path.join(ROOT, "JgaModTool.exe")
    tmp = os.path.join(ROOT, "JgaModTool.tmp.exe")
    src = sorted(glob.glob(os.path.join(ROOT, "app", "src", "*.cs")))
    ico = os.path.join(ROOT, "app", "icon", "JgaModTool.ico")
    cmd = [CSC, "/nologo", "/target:winexe", "/optimize+", "/platform:anycpu", f"/out:{tmp}", f"/win32icon:{ico}"] + \
          [f"/reference:{r}" for r in REFS] + src
    r = subprocess.run(cmd, capture_output=True, text=True)
    errs = [l for l in (r.stdout + r.stderr).splitlines() if ": error " in l]
    for l in errs[:30]:
        print(l)
    if r.returncode != 0 or errs:
        if os.path.exists(tmp):
            os.remove(tmp)
        sys.exit(f"csc failed ({len(errs)} errors)")
    os.replace(tmp, exe)
    print(f"built {exe}")


if __name__ == "__main__":
    main()
