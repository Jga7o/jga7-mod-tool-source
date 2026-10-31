"""The engine's folders (M17 session 1: the root switch). With no variable set, everything is today's C:\\modlab layout.
MODLAB_ROOT       the work root: the <ARCHIVE>_unpacked folders, import_work, test_<name> outputs (default output\\)
MODLAB_ORIGINALS  the original game files (default samples\\originals)
MODLAB_TABLES     our own tables: analysis\\, previews\\, previews_variants\\ (default output\\)
MODLAB_HASHES     the SHA-256 list of the originals (default notes\\datas_hashes.txt)"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
_OUT = os.path.dirname(HERE)
REPO = os.path.dirname(_OUT)   # C:\modlab: manifest paths are relative to it

OUT = os.environ.get("MODLAB_ROOT") or _OUT
ORIG = os.environ.get("MODLAB_ORIGINALS") or os.path.join(REPO, "samples", "originals")
TABLES = os.environ.get("MODLAB_TABLES") or _OUT
HASHES = os.environ.get("MODLAB_HASHES") or os.path.join(REPO, "notes", "datas_hashes.txt")
