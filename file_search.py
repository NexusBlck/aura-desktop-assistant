# file_search.py
# Keeps an index of EVERY document on your PC (all fixed drives, plus any
# EXTRA_SEARCH_FOLDERS in config.py) with a short snippet of each file's
# text, so AURA can find files by what they're actually about.
#
# - Program folders are skipped automatically: any folder containing an
#   .exe/.dll is an installed app (Steam, FL Studio, Python...), not your
#   documents - so it and everything under it is ignored.
# - The index is saved to aura_file_index.json. Rescanning the folders takes
#   a few seconds; only NEW or CHANGED files get their text re-read, so
#   searches stay fast.
import os
import re
import time
import json
import ctypes
import string
import threading
from config import EXTRA_SEARCH_FOLDERS

AURA_DIR = os.path.dirname(os.path.abspath(__file__))
INDEX_FILE = os.path.join(AURA_DIR, "aura_file_index.json")

DOC_EXTENSIONS = {
    ".pdf", ".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls",
    ".txt", ".md", ".rtf", ".odt", ".csv",
}
# Folder names that never hold your own documents.
SKIP_DIR_NAMES = {
    "windows", "program files", "program files (x86)", "programdata", "appdata",
    "$recycle.bin", "system volume information", "recovery", "perflogs", "msocache",
    "node_modules", "venv", "__pycache__", "site-packages", "dist-packages",
    "windowsapps", "steamapps", "xboxgames", "swsetup", "drivers", "intel",
    "documents and settings", "inetpub", "platform-tools",
}
# A folder holding .dll files is an installed program (Steam, FL Studio,
# Python...). Plain .exe files alone don't count - Downloads is full of
# installers, and that's still YOUR folder.
PROGRAM_MARKERS = (".dll",)
# A folder containing one of these is a code project (AURA's generated apps,
# downloaded GitHub repos...). Its .md/.txt files are code docs, not your
# documents, and there can be thousands - so the whole project is skipped.
CODE_PROJECT_MARKERS = {
    "package.json", "pyproject.toml", "setup.py", "requirements.txt",
    "cargo.toml", "go.mod", "pom.xml", "build.gradle", "composer.json", "gemfile",
}
MAX_SNIPPET_FILE_SIZE = 50 * 1024 * 1024  # bigger files are indexed by name only
SNIPPET_LENGTH = 1500
RESCAN_AFTER_SECONDS = 10 * 60             # re-walk the folders at most every 10 min

_lock = threading.Lock()
_index = None  # {"built_at": float, "files": {path: {...}}, "folders": {path: name}}


# ---------------------------------------------------------------- reading text
def _read_snippet(path, ext):
    try:
        if ext in (".txt", ".md", ".csv"):
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read(SNIPPET_LENGTH * 2)
        if ext == ".rtf":
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                raw = f.read(SNIPPET_LENGTH * 6)
            text = re.sub(r"\\[a-z]+-?\d* ?|[{}]", " ", raw)  # strip RTF control words
            return re.sub(r"\s+", " ", text)
        if ext == ".docx":
            import docx
            doc = docx.Document(path)
            return "\n".join(p.text for p in doc.paragraphs[:60])
        if ext == ".pdf":
            from pypdf import PdfReader
            reader = PdfReader(path)
            text = ""
            for page in reader.pages[:5]:
                text += (page.extract_text() or "") + "\n"
                if len(text) > SNIPPET_LENGTH * 2:
                    break
            return text
        if ext == ".pptx":
            from pptx import Presentation
            prs = Presentation(path)
            texts = []
            for slide in list(prs.slides)[:20]:
                for shape in slide.shapes:
                    if shape.has_text_frame:
                        texts.append(shape.text_frame.text)
            return "\n".join(texts)
        if ext == ".xlsx":
            import openpyxl
            wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
            texts = []
            for ws in wb.worksheets[:3]:
                for row in ws.iter_rows(max_row=50, values_only=True):
                    texts.append(" ".join(str(c) for c in row if c is not None))
            return "\n".join(texts)
    except Exception:
        return ""
    return ""  # .doc/.ppt/.xls/.odt: old/other formats - searchable by name and folder only


# ---------------------------------------------------------------- where to look
def _fixed_drives():
    """All local hard drives (C:, D:, ...) - not USB sticks, DVDs or network drives."""
    drives = []
    for letter in string.ascii_uppercase:
        root = f"{letter}:\\"
        try:
            if ctypes.windll.kernel32.GetDriveTypeW(root) == 3:  # DRIVE_FIXED
                drives.append(root)
        except Exception:
            if os.path.exists(root):
                drives.append(root)
    return drives


def _roots():
    roots = _fixed_drives() + [r for r in EXTRA_SEARCH_FOLDERS if os.path.isdir(r)]
    # OneDrive "files on demand" live under the user folder, so they're covered.
    return roots


def _protected_folders():
    """Folders that are ALWAYS searched, even if they happen to contain a
    program file: the top of each drive and your personal folders."""
    home = os.path.expanduser("~")
    protected = {os.path.normcase(r) for r in _roots()}
    protected.add(os.path.normcase(home))
    for sub in ("Desktop", "Documents", "Downloads", "Pictures", "Videos", "Music"):
        protected.add(os.path.normcase(os.path.join(home, sub)))
    try:
        for entry in os.listdir(home):
            if entry.lower().startswith("onedrive"):
                protected.add(os.path.normcase(os.path.join(home, entry)))
    except OSError:
        pass
    return protected


def _skip_dir(name, full_path):
    lower = name.lower()
    if lower in SKIP_DIR_NAMES or name.startswith((".", "$", "~")):
        return True
    # AURA's own code, backups and search-results shortcuts aren't "your files".
    if os.path.normcase(full_path) == os.path.normcase(AURA_DIR) or lower.startswith("aura_backup"):
        return True
    return False


# ---------------------------------------------------------------- the index
def _load():
    try:
        with open(INDEX_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data.get("files"), dict):
            data.setdefault("folders", {})
            return data
    except (OSError, ValueError):
        pass
    return {"built_at": 0, "files": {}, "folders": {}}


def _save(index):
    try:
        tmp = INDEX_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False)
        os.replace(tmp, INDEX_FILE)
    except OSError as e:
        print(f"(file index: couldn't save: {e})")


def _scan(old):
    """Walks every root. Reuses the saved snippet for files whose size and
    modified-time haven't changed; reads text only for new/changed files."""
    files, folders = {}, {}
    new_or_changed = 0
    protected = _protected_folders()
    for root in _roots():
        for dirpath, dirnames, filenames in os.walk(root, onerror=lambda e: None):
            if os.path.normcase(dirpath) not in protected and (
                    any(f.lower().endswith(PROGRAM_MARKERS) for f in filenames)
                    or any(f.lower() in CODE_PROJECT_MARKERS for f in filenames)):
                dirnames[:] = []  # an installed program or code project - skip it entirely
                continue
            dirnames[:] = [d for d in dirnames if not _skip_dir(d, os.path.join(dirpath, d))]

            has_docs = False
            for filename in filenames:
                ext = os.path.splitext(filename)[1].lower()
                if ext not in DOC_EXTENSIONS or filename.startswith("~$"):
                    continue  # "~$..." = Word/Excel's temporary lock files
                path = os.path.join(dirpath, filename)
                try:
                    st = os.stat(path)
                except OSError:
                    continue
                has_docs = True
                prev = old.get(path)
                if prev and prev.get("mtime") == st.st_mtime and prev.get("size") == st.st_size:
                    files[path] = prev
                    continue
                snippet = _read_snippet(path, ext) if st.st_size <= MAX_SNIPPET_FILE_SIZE else ""
                files[path] = {
                    "name": filename, "mtime": st.st_mtime, "size": st.st_size,
                    "snippet": re.sub(r"\s+", " ", snippet or "")[:SNIPPET_LENGTH],
                }
                new_or_changed += 1
            if has_docs:
                folders[dirpath] = os.path.basename(dirpath) or dirpath
    return files, folders, new_or_changed


def refresh_index(force=False):
    """Brings the index up to date (at most every RESCAN_AFTER_SECONDS unless
    forced). Safe to call from several threads."""
    global _index
    with _lock:
        if _index is None:
            _index = _load()
        if not force and time.time() - _index.get("built_at", 0) < RESCAN_AFTER_SECONDS:
            return _index
        started = time.time()
        files, folders, changed = _scan(_index["files"])
        _index = {"built_at": time.time(), "files": files, "folders": folders}
        _save(_index)
        print(f"(file index: {len(files)} documents in {len(folders)} folders, "
              f"{changed} new/changed, {time.time() - started:.1f}s)")
        return _index


def refresh_in_background():
    """Called at startup so the first search doesn't have to wait."""
    threading.Thread(target=refresh_index, daemon=True).start()


def get_index():
    return refresh_index()
