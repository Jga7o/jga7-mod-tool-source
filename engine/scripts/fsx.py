"""Renames with a short retry (session 65): Windows (the virus scanner, the search indexer) may hold a file for a moment
right after it was written, and a rename then fails with PermissionError (WinError 5 / 32). Seen in sessions 63-65 in
dpcunpack, applypatch and growdpc. Every engine rename goes through here; deletes too (remove, session 67)."""
import os
import time


def _retry(fn, src, dst, tries=10):
    for k in range(tries):
        try:
            return fn(src, dst)
        except PermissionError:
            if k == tries - 1:
                raise
            time.sleep(0.5 * (k + 1))


def replace(src, dst):
    _retry(os.replace, src, dst)


def rename(src, dst):
    _retry(os.rename, src, dst)


def remove(path):
    _retry(lambda p, _: os.remove(p), path, None)
