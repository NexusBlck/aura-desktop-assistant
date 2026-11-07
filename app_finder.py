# app_finder.py
# Finds and launches installed apps the same way Windows' own Start Menu
# does internally (via Get-StartApps) - this covers traditional desktop
# programs, Microsoft Store apps, AND browser-installed PWAs (like a
# ChatGPT app installed from the browser) all in one place. As a second
# fallback layer, also checks Desktop shortcuts, since a few portable/older
# apps skip the Start Menu and only have a desktop icon.
import os
import glob
import json
import re
import difflib
import threading
import subprocess


def _build_index():
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "Get-StartApps | ConvertTo-Json -Compress"],
            capture_output=True, text=True, timeout=15
        )
        data = json.loads(result.stdout)
        if isinstance(data, dict):
            data = [data]
        return [
            {"name": app["Name"], "app_id": app["AppID"]}
            for app in data if "Name" in app and "AppID" in app
        ]
    except Exception as e:
        print(f"(app_finder: couldn't index Start Menu apps: {e})")
        return []


def _build_desktop_index():
    """Fallback index of Desktop shortcuts, for the rare app that skips the
    Start Menu entirely and only has a desktop icon."""
    dirs = [
        os.path.join(os.environ.get("PUBLIC", r"C:\Users\Public"), "Desktop"),
        os.path.join(os.path.expanduser("~"), "Desktop"),
    ]
    index = []
    for base in dirs:
        if not os.path.exists(base):
            continue
        for path in glob.glob(os.path.join(base, "*.lnk")):
            name = os.path.splitext(os.path.basename(path))[0]
            index.append({"name": name, "path": path})
    return index


# The Start Menu index takes a few seconds (it shells out to PowerShell), so
# it's built on a background thread at import instead of blocking AURA's
# startup. Lookups wait for it only if it isn't finished yet.
_INDEX = []
_DESKTOP_INDEX = []
_index_ready = threading.Event()


def _build_all():
    global _INDEX, _DESKTOP_INDEX
    try:
        _INDEX = _build_index()
        _DESKTOP_INDEX = _build_desktop_index()
        print(f"(app_finder: indexed {len(_INDEX)} Start Menu apps + {len(_DESKTOP_INDEX)} desktop shortcuts)")
    finally:
        _index_ready.set()


threading.Thread(target=_build_all, daemon=True).start()


def refresh_index():
    """Rebuild the index - call after installing something new."""
    _index_ready.clear()
    _build_all()


def _best_match(query, items, name_key):
    query_lower = (query or "").lower().strip()
    if not query_lower or not items:
        return None

    for item in items:
        if item[name_key].lower() == query_lower:
            return item

    # Whole-word matches first: "word" should find "Word 2016", not
    # "WordPad" (which only contains "word" as part of a longer word).
    word_re = re.compile(r"\b" + re.escape(query_lower) + r"\b")
    whole_word_matches = [item for item in items if word_re.search(item[name_key].lower())]
    if whole_word_matches:
        whole_word_matches.sort(key=lambda i: len(i[name_key]))
        return whole_word_matches[0]

    substring_matches = [
        item for item in items
        if query_lower in item[name_key].lower() or item[name_key].lower() in query_lower
    ]
    if substring_matches:
        substring_matches.sort(key=lambda i: len(i[name_key]))
        return substring_matches[0]

    names = [item[name_key] for item in items]
    close = difflib.get_close_matches(query, names, n=1, cutoff=0.6)
    if close:
        for item in items:
            if item[name_key] == close[0]:
                return item
    return None


def find_app(query):
    """Returns the AppID of the best-matching installed app, or None."""
    _index_ready.wait(timeout=20)
    match = _best_match(query, _INDEX, "name")
    return match["app_id"] if match else None


def find_desktop_shortcut(query):
    """Fallback: returns a Desktop shortcut path if Get-StartApps found nothing."""
    _index_ready.wait(timeout=20)
    match = _best_match(query, _DESKTOP_INDEX, "name")
    return match["path"] if match else None


def launch_app(app_id):
    """Launches an app by its AppID - works for traditional programs,
    Store apps, and PWAs alike."""
    subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app_id}"])