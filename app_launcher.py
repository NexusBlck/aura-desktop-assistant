# app_launcher.py
# Finds installed apps the same way Windows itself does: by looking at your
# actual Start Menu shortcuts (which point to the real install location
# regardless of what's on your system PATH) plus common install folders.
# This is what lets AURA open things like Spotify, Steam, or the ChatGPT
# app - none of which are ever on PATH - by just their spoken name.
import os
import json
import time
import glob

from config import APP_INDEX_MAX_AGE_HOURS

APP_INDEX_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app_index.json")

START_MENU_DIRS = [
    os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"),
                 "Microsoft", "Windows", "Start Menu", "Programs"),
    os.path.join(os.environ.get("APPDATA", ""),
                 "Microsoft", "Windows", "Start Menu", "Programs"),
]

EXTRA_SEARCH_DIRS = [
    os.environ.get("PROGRAMFILES", r"C:\Program Files"),
    os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs"),
]


def _build_shortcut_index():
    index = {}
    for base in START_MENU_DIRS:
        if not base or not os.path.exists(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base, onerror=lambda e: None):
            for fn in filenames:
                if fn.lower().endswith((".lnk", ".url")):
                    display_name = os.path.splitext(fn)[0].lower()
                    index[display_name] = os.path.join(dirpath, fn)
    return index


def _build_exe_index():
    """Backup for apps (common with some Steam games/launchers) that don't
    register a Start Menu shortcut - a shallow scan of common install roots."""
    index = {}
    for base in EXTRA_SEARCH_DIRS:
        if not base or not os.path.exists(base):
            continue
        try:
            for entry in os.listdir(base):
                folder = os.path.join(base, entry)
                if not os.path.isdir(folder):
                    continue
                for depth_glob in (os.path.join(folder, "*.exe"), os.path.join(folder, "*", "*.exe")):
                    for exe_path in glob.glob(depth_glob):
                        exe_name = os.path.splitext(os.path.basename(exe_path))[0].lower()
                        index.setdefault(exe_name, exe_path)
                        index.setdefault(entry.lower(), exe_path)
        except Exception:
            continue
    return index


def _load_app_index(force=False):
    if not force and os.path.exists(APP_INDEX_FILE):
        try:
            with open(APP_INDEX_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            age_hours = (time.time() - data.get("built_at", 0)) / 3600
            if age_hours < APP_INDEX_MAX_AGE_HOURS:
                return data
        except Exception:
            pass

    data = {
        "built_at": time.time(),
        "shortcuts": _build_shortcut_index(),
        "exes": _build_exe_index(),
    }
    try:
        with open(APP_INDEX_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass
    return data


def _best_match(name, keys):
    name = name.lower().strip()
    if name in keys:
        return name

    candidates = [k for k in keys if name in k or k in name]
    if candidates:
        candidates.sort(key=len)  # prefer the closest-length match
        return candidates[0]

    name_tokens = set(name.split())
    best, best_score = None, 0
    for k in keys:
        score = len(name_tokens & set(k.split()))
        if score > best_score:
            best, best_score = k, score
    return best if best_score > 0 else None


def find_app_path(name):
    """Returns a path AURA can hand to os.startfile to launch this app, or
    None if nothing matching it was found anywhere."""
    data = _load_app_index()
    match = _best_match(name, data["shortcuts"].keys())
    if match:
        return data["shortcuts"][match]

    match = _best_match(name, data["exes"].keys())
    if match:
        return data["exes"][match]

    return None


def refresh_app_index():
    """Force a fresh rescan - call this after installing something new."""
    _load_app_index(force=True)