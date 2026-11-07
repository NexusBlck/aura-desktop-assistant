# pc_control.py
import os
import re
import json
import time
import shutil
import subprocess
import platform
import webbrowser
import datetime
import requests
from relevance import find_relevant
from app_finder import find_app, launch_app, find_desktop_shortcut
import active_window

IS_WINDOWS = platform.system() == "Windows"

# Fast path ONLY for core Windows utilities that are guaranteed to be on the
# system PATH (they live in C:\Windows\System32). Everything else - Word,
# Excel, Chrome, Spotify, Steam, literally any installed app - goes through
# find_app() below, which searches the same index Windows' own Start Menu
# uses.
APP_PATHS = {
    "notepad": "notepad.exe",
    "calculator": "calc.exe",
    "calc": "calc.exe",
    "file explorer": "explorer.exe",
    "explorer": "explorer.exe",
    "paint": "mspaint.exe",
    "cmd": "cmd.exe",
    "command prompt": "cmd.exe",
    "task manager": "taskmgr.exe",
}

# Microsoft Office, launched by its real program name so "word" can never be
# confused with WordPad. Word gets /w = start straight on a blank document
# (Office's start screen can't be typed into).
# spoken name -> (program, extra arguments, window-title hint, display name)
OFFICE_APPS = {
    "word": ("winword.exe", ["/w"], " - Word", "Word"),
    "microsoft word": ("winword.exe", ["/w"], " - Word", "Word"),
    "ms word": ("winword.exe", ["/w"], " - Word", "Word"),
    "excel": ("excel.exe", [], " - Excel", "Excel"),
    "microsoft excel": ("excel.exe", [], " - Excel", "Excel"),
    "powerpoint": ("powerpnt.exe", [], " - PowerPoint", "PowerPoint"),
    "microsoft powerpoint": ("powerpnt.exe", [], " - PowerPoint", "PowerPoint"),
    "outlook": ("outlook.exe", [], "Outlook", "Outlook"),
    "onenote": ("onenote.exe", [], "OneNote", "OneNote"),
}

# Window-title hints for the built-in utilities, so AURA can find the window.
_TITLE_HINTS = {
    "notepad.exe": "Notepad", "calc.exe": "Calculator", "mspaint.exe": "Paint",
    "taskmgr.exe": "Task Manager", "cmd.exe": "cmd",
}


def _app_path_from_registry(exe):
    """Where Windows says a program lives (the 'App Paths' registry entry
    Office and most big apps register) - more reliable than guessing."""
    try:
        import winreg
    except ImportError:
        return None
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths" + "\\" + exe) as key:
                path = winreg.QueryValue(key, None)
                if path and os.path.exists(path):
                    return path
        except OSError:
            continue
    return None


def open_app(name):
    """Opens an app and remembers its window, so follow-up commands ("write
    X on it", "save it") go into it. Checks Office and built-in Windows
    utilities first, then searches your installed apps via the Start Menu
    index (covers Spotify, Steam, Discord, or anything else you can find by
    typing its name into the Windows Start menu)."""
    name_lower = (name or "").lower().strip()
    before = active_window.snapshot()

    office = OFFICE_APPS.get(name_lower)
    if office:
        exe, args, hint, display = office
        path = _app_path_from_registry(exe) or shutil.which(exe)
        if path:
            try:
                subprocess.Popen([path] + args)
                active_window.track_opened(display, "app", before, hint)
                return f"Opening {display}..."
            except Exception as e:
                return f"Couldn't open {display}: {e}"

    if name_lower in APP_PATHS:
        exe = APP_PATHS[name_lower]
        try:
            subprocess.Popen([exe])
            active_window.track_opened(name, "app", before, _TITLE_HINTS.get(exe))
            return f"Opening {name}..."
        except Exception as e:
            return f"Couldn't open {name}: {e}"

    app_id = find_app(name)
    if app_id:
        try:
            launch_app(app_id)
            active_window.track_opened(name, "app", before)
            return f"Opening {name}..."
        except Exception as e:
            return f"Couldn't open {name}: {e}"

    shortcut_path = find_desktop_shortcut(name)
    if shortcut_path:
        try:
            os.startfile(shortcut_path)
            active_window.track_opened(name, "app", before)
            return f"Opening {name}..."
        except Exception as e:
            return f"Couldn't open {name}: {e}"

    # Last resort: an executable on PATH with exactly that name. Resolved
    # with shutil.which and launched WITHOUT a shell, so text coming from the
    # LLM can never be interpreted as a shell command.
    exe_path = shutil.which(name_lower) if re.fullmatch(r"[\w.\- ]+", name_lower or "") else None
    if exe_path:
        try:
            subprocess.Popen([exe_path])
            active_window.track_opened(name, "app", before)
            return f"Opening {name}..."
        except Exception as e:
            return f"Couldn't open {name}: {e}"

    return f"I couldn't find an app called '{name}' installed on your PC."


def close_app(name):
    name = (name or "").lower().strip()
    exe = OFFICE_APPS[name][0] if name in OFFICE_APPS else APP_PATHS.get(name, name)
    if not exe.endswith(".exe"):
        exe += ".exe"
    try:
        if IS_WINDOWS:
            result = subprocess.run(["taskkill", "/f", "/im", exe], capture_output=True, text=True)
        else:
            result = subprocess.run(["pkill", "-f", name], capture_output=True, text=True)
        if result.returncode != 0:
            return f"Couldn't close {name} - it doesn't look like it's running."
        return f"Closing {name}..."
    except Exception as e:
        return f"Couldn't close {name}: {e}"


# Sites so common there's no point searching for them.
KNOWN_SITES = {
    "chatgpt": "https://chatgpt.com",
    "google": "https://www.google.com",
    "youtube": "https://www.youtube.com",
    "gmail": "https://mail.google.com",
    "whatsapp": "https://web.whatsapp.com",
    "whatsapp web": "https://web.whatsapp.com",
}

_ADDRESS_RE = re.compile(r"^(https?://)?([a-z0-9-]+\.)+[a-z]{2,}(:\d+)?(/\S*)?$", re.IGNORECASE)

PICK_SITE_PROMPT = """
The user asked to open this website: "{name}"

Here are web search results:
{listing}

Which result IS that website itself - the official site or its login/portal
page - not a news article, Wikipedia page, social-media page, directory,
review or forum post ABOUT it?

Be strict: the result's title or domain must clearly BE the thing the user
named (same organization/product). Sharing just a number or one common word
does NOT count. If you're not sure, answer null - opening the wrong site is
worse than admitting you couldn't find it.

Respond ONLY with JSON: {{"index": <number or null>, "confident": true or false}}
"""

# Sites AURA has already found are remembered here (like bookmarks), so the
# same request always opens the same site and doesn't need a search again.
# To correct one, edit or delete its line in aura_sites.json.
SITES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aura_sites.json")


def _load_sites():
    try:
        with open(SITES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


# Words that don't identify WHICH site someone means, so "open the Riverside
# University student portal" and "riverside student portal" match the same
# bookmark.
_SITE_FILLER = {
    "the", "a", "an", "my", "our", "of", "for", "to", "open", "website",
    "site", "web", "page", "official", "link", "please", "university", "uni",
}


def _site_words(name):
    words = re.findall(r"[a-z0-9]+", (name or "").lower())
    return {w for w in words if w not in _SITE_FILLER}


def _find_saved_site(name):
    """Exact bookmark match first, then a looser one: a bookmark matches if
    all of its meaningful words appear in what was asked, or vice versa."""
    sites = _load_sites()
    key = (name or "").lower().strip()
    if key in sites:
        return sites[key]

    asked = _site_words(name)
    if not asked:
        return None
    best, best_overlap = None, 0
    for saved_name, entry in sites.items():
        saved_words = _site_words(saved_name)
        if saved_words and (saved_words <= asked or asked <= saved_words):
            overlap = len(saved_words & asked)
            if overlap > best_overlap:
                best, best_overlap = entry, overlap
    return best


def save_site(name, url):
    """Bookmarks a site the user explicitly told AURA about - e.g. correcting
    a wrong pick with "my uni portal is portal.example.edu/student".
    Returns a spoken-friendly result."""
    name = (name or "").strip()
    url = (url or "").strip()
    if not name or not url:
        return "Tell me both the site's name and its address, and I'll remember it."
    if not url.lower().startswith("http"):
        url = "https://" + url
    if not _is_reachable(url):
        return f"I couldn't reach {url} - double-check the address and tell me again."
    domain = re.sub(r"^https?://(www\.)?", "", url).split("/")[0]
    _remember_site(name, url, domain)
    return f"Got it - whenever you ask for {name}, I'll open {domain}."


def _remember_site(name, url, title):
    sites = _load_sites()
    sites[name.lower().strip()] = {"url": url, "title": title}
    try:
        with open(SITES_FILE, "w", encoding="utf-8") as f:
            json.dump(sites, f, ensure_ascii=False, indent=2)
    except OSError as e:
        print(f"(couldn't save site bookmark: {e})")


def _is_reachable(url):
    """True if the address actually exists (any HTTP response counts -
    some real sites answer bots with 403). False if it doesn't resolve or
    can't be connected to, which is what a made-up address does."""
    try:
        resp = requests.get(url, timeout=6, allow_redirects=True, stream=True,
                            headers={"User-Agent": "Mozilla/5.0"})
        resp.close()
        return True
    except requests.RequestException:
        return False


def resolve_website(name):
    """Finds the REAL address for a website the way a person would: if it's
    already a working address, use it; otherwise search the web, look at the
    results, and pick the one that's actually the site. Never guesses.
    Returns (url, title) or (None, None)."""
    from ai import ask_json
    from web_search import find_sites

    name = (name or "").strip()
    if not name:
        return None, None

    known = KNOWN_SITES.get(name.lower())
    if known:
        return known, name

    saved = _find_saved_site(name)
    if saved:
        return saved["url"], saved.get("title") or saved["url"]
    original_name = name

    if _ADDRESS_RE.match(name):
        url = name if name.lower().startswith("http") else "https://" + name
        if _is_reachable(url):
            return url, url
        print(f"  ({url} doesn't exist - searching for the real site instead)")
        # "https://studentportal.example.edu/" -> "studentportal example",
        # which searches like a normal name instead of a dead address.
        host = re.sub(r"^https?://", "", name, flags=re.IGNORECASE).split("/")[0]
        filler = {"www", "com", "org", "net", "edu", "gov", "ac", "co", "io", "pk", "uk", "in"}
        name = " ".join(p for p in host.split(".") if p.lower() not in filler) or host

    results = find_sites(f"{name} official website")
    if not results:
        return None, None

    listing = "\n".join(f"{i}: {r['title']} - {r['url']}\n   {r['snippet']}" for i, r in enumerate(results))
    picked = ask_json([{"role": "user", "content": PICK_SITE_PROMPT.format(name=name, listing=listing)}])

    if not isinstance(picked, dict):
        return None, None  # AI unavailable - don't gamble on a random result

    idx = picked.get("index")
    if isinstance(idx, int) and 0 <= idx < len(results) and picked.get("confident") is True:
        url, title = results[idx]["url"], results[idx]["title"]
        # A search can land on a side page like "forgot password" - open the
        # site's front door instead (it redirects to the login page itself).
        if re.search(r"forgot|reset|password|register|signup|sign-up|logout", url, re.IGNORECASE):
            url = re.match(r"^https?://[^/]+", url).group(0)
            title = re.sub(r"^https?://", "", url)
        _remember_site(original_name, url, title)
        return url, title
    return None, None  # the AI looked and none of them is clearly the real site


def open_website(target):
    url, title = resolve_website(target)
    if url:
        webbrowser.open(url)
        active_window.track_opened(target, "website", browser=True)
        domain = re.sub(r"^https?://(www\.)?", "", url).split("/")[0]
        if title and len(title) > 40:
            title = re.split(r"\s[-|·–—:]\s", title)[0][:40]  # "GitHub · Change is constant..." -> "GitHub"
        if not title or re.sub(r"^https?://(www\.)?", "", title).rstrip("/") in (domain, url):
            return f"Opening {domain}."
        return f"Opening {title} ({domain})."

    # Couldn't pin it down - do what a person would: show the search results.
    search_url = "https://www.google.com/search?q=" + requests.utils.quote(target or "")
    webbrowser.open(search_url)
    return f"I couldn't find the exact site for {target}, so I opened a Google search for it instead."


def play_youtube(query):
    """Searches YouTube for the query and opens the top result, which
    autoplays in the browser like a normal YouTube visit would."""
    try:
        search_url = "https://www.youtube.com/results?search_query=" + requests.utils.quote(query)
        resp = requests.get(search_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        match = re.search(r'"videoId":"(.*?)"', resp.text)
        if match:
            video_url = f"https://www.youtube.com/watch?v={match.group(1)}"
            webbrowser.open(video_url)
            return f"Playing {query} on YouTube."
        webbrowser.open(search_url)
        return f"Couldn't find an exact match, opened YouTube search results for {query} instead."
    except Exception as e:
        return f"Couldn't play that on YouTube: {e}"


def _paste_text(text):
    """Puts text into the focused window by pasting it. Faster and more
    reliable than simulated typing, and works for emoji / Urdu / accented
    letters, which pyautogui's typing silently skips. Your clipboard is put
    back the way it was afterwards."""
    import pyautogui
    import pyperclip
    try:
        previous = pyperclip.paste()
    except Exception:
        previous = None
    pyperclip.copy(text)
    time.sleep(0.05)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.4)  # let the app read the clipboard before restoring it
    if previous is not None:
        try:
            pyperclip.copy(previous)
        except Exception:
            pass


def _focus_for_typing():
    """Makes sure keystrokes go to the window AURA opened, not AURA's own
    window (where you probably just typed the command). Returns None if
    ready, or a message explaining why AURA won't type."""
    import pygetwindow as gw
    if active_window.focus_target():
        return None
    target = active_window.current_name()
    if target:
        # AURA knows where it SHOULD type but couldn't reach that window -
        # stop, rather than typing into whatever else happens to be focused.
        return f"I couldn't bring {target} to the front - click on it once, then tell me again."
    active = gw.getActiveWindow()
    if active is None or active_window.is_own_window(active.title):
        return ("I'm not sure which window to type into - open something first "
                "(like 'open word'), then tell me what to write.")
    return None  # something else is focused and it isn't AURA - type there


def write_note(text):
    """Opens Notepad and writes the given text into it."""
    result = open_and_type("notepad", text)
    if result.startswith("Opened notepad"):
        return "Noted it down in Notepad - remember to save it if you want to keep it."
    return result


def type_text(text):
    """Types into the window AURA last opened (so "open word" then "write
    AURA on it" lands in that same Word window), or the focused window."""
    try:
        problem = _focus_for_typing()
        if problem:
            return problem
        _paste_text(text)
        where = active_window.current_name()
        return f"Written in {where}." if where else "Typed it out."
    except Exception as e:
        return f"Couldn't type that: {e}"


def press_hotkey(combo):
    """Presses a keyboard shortcut, e.g. 'ctrl+s' - in the window AURA last
    opened, so "save it" saves the document you've been working on."""
    try:
        import pyautogui
        keys = [k.strip() for k in combo.lower().split("+") if k.strip()]
        if not keys:
            return "I didn't catch which keys to press."
        active_window.focus_target()
        pyautogui.hotkey(*keys)
        return f"Pressed {combo}."
    except Exception as e:
        return f"Couldn't press that combo: {e}"


def mouse_action(action):
    """Basic mouse control at the CURRENT cursor position - click, scroll, etc.
    AURA has no vision, so it can't click on a specific button by name."""
    try:
        import pyautogui
        action = (action or "").lower().strip()
        if action == "click":
            pyautogui.click()
            return "Clicked."
        elif action == "right_click":
            pyautogui.rightClick()
            return "Right-clicked."
        elif action == "double_click":
            pyautogui.doubleClick()
            return "Double-clicked."
        elif action == "scroll_up":
            pyautogui.scroll(400)
            return "Scrolled up."
        elif action == "scroll_down":
            pyautogui.scroll(-400)
            return "Scrolled down."
        return f"I don't know the mouse action '{action}'."
    except Exception as e:
        return f"Couldn't do that mouse action: {e}"


def open_and_type(app_or_site, text, submit=False):
    """Opens an app OR website, waits for its window to actually appear,
    then writes text into it - and optionally presses Enter (useful for chat
    sites like ChatGPT). Afterwards that window stays AURA's current target,
    so follow-up commands keep working in it."""
    import pyautogui

    lowered = (app_or_site or "").lower().strip()
    is_website = lowered in KNOWN_SITES or bool(_ADDRESS_RE.match(lowered))

    try:
        if not is_website:
            result = open_app(app_or_site)
            if result.startswith("I couldn't find"):
                is_website = True  # not an installed app - maybe it's a website

        if is_website:
            url, _ = resolve_website(app_or_site)
            if not url:
                # Don't type into whatever random window happens to be focused.
                return f"I couldn't find an app or website called {app_or_site}, so I didn't type anything."
            webbrowser.open(url)
            active_window.track_opened(app_or_site, "website", browser=True)

        active_window.wait_until_ready(timeout=20)
        time.sleep(3 if is_website else 1.5)  # let the page/document finish loading
        problem = _focus_for_typing()
        if problem:
            return f"Opened {app_or_site}, but I couldn't find its window to type into."

        _paste_text(text)
        if submit:
            pyautogui.press("enter")

        return f"Opened {app_or_site} and typed that in" + (", and sent it." if submit else ".")
    except Exception as e:
        return f"Couldn't do that: {e}"


RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "search_results")
OPEN_DIRECTLY_MAX = 3  # more results than this go into one shortcuts folder instead


def _make_results_folder(topic, files, folders):
    """Puts a shortcut to every result into one folder, so you get a single
    tidy window instead of dozens. The originals are never moved or copied -
    these are just shortcuts. Returns the folder's path."""
    import comtypes.client

    safe = re.sub(r'[<>:"/\\|?*]', "", topic).strip()[:60] or "results"
    folder = os.path.join(RESULTS_DIR, f"{safe} (AURA search)")
    os.makedirs(folder, exist_ok=True)
    for old in os.listdir(folder):  # clear the previous search for this topic
        if old.lower().endswith(".lnk"):
            try:
                os.remove(os.path.join(folder, old))
            except OSError:
                pass

    shell = comtypes.client.CreateObject("WScript.Shell", dynamic=True)
    used = set()
    items = [(p, f"[Folder] {os.path.basename(p)}") for p in folders] + \
            [(p, os.path.basename(p)) for p in files]
    for target, label in items:
        label = re.sub(r'[<>:"/\\|?*]', "", label)
        name, n = label, 2
        while name.lower() in used:  # two files with the same name in different folders
            name, n = f"{label} ({n})", n + 1
        used.add(name.lower())
        shortcut = shell.CreateShortcut(os.path.join(folder, name + ".lnk"))
        shortcut.TargetPath = target
        shortcut.Save()
    return folder


def search_and_open_files(topic, mode="topic"):
    """Searches EVERY document on your PC for the topic (by name, folder and
    actual content, confirmed by the AI) and brings the results up:
    - a specific file ("open PRISMA 2020"): opens that file.
    - a topic ("everything on medicine"): up to 3 results open directly;
      more than that are gathered as shortcuts in one folder, opened for you."""
    try:
        result = find_relevant(topic, mode=mode)
    except Exception as e:
        return f"File search failed: {e}"

    files, folders, scanned = result["files"], result["folders"], result["scanned"]
    if scanned == 0:
        return "I couldn't find any documents on your computer to search through."
    if not files and not folders:
        return f"I checked {scanned} documents on your computer but didn't find anything genuinely about {topic}."

    if mode == "specific":
        os.startfile(files[0])
        return f"Opening {os.path.basename(files[0])}."

    total = len(files) + len(folders)
    if total <= OPEN_DIRECTLY_MAX:
        for path in folders + files:
            try:
                os.startfile(path)
            except OSError:
                pass
        names = ", ".join(os.path.basename(p) for p in folders + files)
        return f"Found {total} thing{'s' if total > 1 else ''} about {topic}: {names}. Opening {'them' if total > 1 else 'it'} now."

    try:
        results_folder = _make_results_folder(topic, files, folders)
    except Exception as e:
        return f"Found {total} results about {topic}, but couldn't gather them into a folder: {e}"
    before = active_window.snapshot()
    os.startfile(results_folder)
    active_window.track_opened(f"search results for {topic}", "folder", before,
                               hint=os.path.basename(results_folder))

    parts = []
    if files:
        parts.append(f"{len(files)} file{'s' if len(files) > 1 else ''}")
    if folders:
        parts.append(f"{len(folders)} folder{'s' if len(folders) > 1 else ''} ({', '.join(os.path.basename(f) for f in folders[:3])})")
    return (f"Found {' and '.join(parts)} about {topic}, out of {scanned} documents on your PC. "
            f"I've put shortcuts to all of them in one folder and opened it for you.")


def take_screenshot():
    try:
        import pyautogui
        folder = os.path.join(os.path.expanduser("~"), "Pictures", "AURA_Screenshots")
        os.makedirs(folder, exist_ok=True)
        filename = f"screenshot_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        path = os.path.join(folder, filename)
        pyautogui.screenshot(path)
        return f"Screenshot saved to {path}"
    except Exception as e:
        return f"Couldn't take screenshot: {e}"


def set_volume(target):
    try:
        from ctypes import cast, POINTER
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        volume = cast(interface, POINTER(IAudioEndpointVolume))

        target = str(target).lower().strip()

        if target == "mute":
            volume.SetMute(1, None)
            return "Muted."
        elif target == "up":
            current = volume.GetMasterVolumeLevelScalar()
            volume.SetMasterVolumeLevelScalar(min(current + 0.1, 1.0), None)
            return "Volume increased."
        elif target == "down":
            current = volume.GetMasterVolumeLevelScalar()
            volume.SetMasterVolumeLevelScalar(max(current - 0.1, 0.0), None)
            return "Volume decreased."
        else:
            level = int(target) / 100
            volume.SetMasterVolumeLevelScalar(max(0.0, min(level, 1.0)), None)
            return f"Volume set to {target}%."
    except Exception as e:
        return f"Couldn't change volume: {e}"


def shutdown_pc():
    if IS_WINDOWS:
        os.system("shutdown /s /t 5")
    else:
        os.system("shutdown now")
    return "Shutting down in 5 seconds..."


def restart_pc():
    if IS_WINDOWS:
        os.system("shutdown /r /t 5")
    else:
        os.system("reboot")
    return "Restarting in 5 seconds..."