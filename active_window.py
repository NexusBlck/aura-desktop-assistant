# active_window.py
# Remembers the window AURA opened most recently (Word, Notepad, a website
# in the browser...) so follow-up commands - "write AURA on it", "save it",
# "press enter" - go into THAT window instead of opening a brand new one.
# This is what lets commands stack: open something, then keep working in it.
import re
import time
import ctypes
import threading
import pygetwindow as gw

TARGET_EXPIRY_SECONDS = 60 * 60  # forget the target after an hour of no use

_state = {"name": None, "kind": None, "hwnd": None, "hint": None, "before": set(), "opened_at": 0}
_ready = threading.Event()
_ready.set()
_lock = threading.Lock()

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
SW_RESTORE = 9


def is_own_window(title):
    """AURA's own windows ("AURA", "AURA - Schedule", "AURA Explain"...)
    are never a place to type into."""
    return (title or "").startswith("AURA")


def snapshot():
    """Handles of every window open right now - taken just before launching
    something, so the NEW window can be spotted afterwards."""
    return {w._hWnd for w in gw.getAllWindows()}


# Loading/splash windows (Word shows "Opening - Word" before the real
# document window) - never lock onto these.
_SPLASH_RE = re.compile(r"^(opening|starting|loading)\b", re.IGNORECASE)


def _new_windows(before, hint):
    candidates = [
        w for w in gw.getAllWindows()
        if w._hWnd not in before and w.title and w.visible
        and not is_own_window(w.title) and not _SPLASH_RE.match(w.title)
    ]
    if hint:
        hinted = [w for w in candidates if hint.lower() in w.title.lower()]
        candidates = hinted or candidates
    return candidates


def _find_new_window(before, hint, timeout):
    """Waits for a window that wasn't there before. Prefers one whose title
    contains the hint (e.g. " - Word") and that has come to the front."""
    deadline = time.time() + timeout
    best = None
    while time.time() < deadline:
        candidates = _new_windows(before, hint)
        for w in candidates:
            if w.isActive:
                return w
        if candidates:
            best = candidates[-1]
        time.sleep(0.3)
    return best


def _track(name, kind, before, hint, timeout, browser):
    try:
        if browser:
            # Websites open as a tab in an existing browser window, so there's
            # no new window - the browser coming to the front is the signal.
            time.sleep(2.5)
            win = gw.getActiveWindow()
            if win and is_own_window(win.title):
                win = None
        else:
            win = _find_new_window(before, hint, timeout)
        with _lock:
            _state.update({
                "name": name, "kind": kind,
                "hwnd": win._hWnd if win else None,
                "hint": hint or (win.title if win else None),
                "before": before,
                "opened_at": time.time(),
            })
    finally:
        _ready.set()


def track_opened(name, kind="app", before=None, hint=None, timeout=15, browser=False):
    """Call right after launching something. Finds its window in the
    background (Word can take several seconds) so AURA can reply instantly."""
    _ready.clear()
    with _lock:
        _state.update({"name": name, "kind": kind, "hwnd": None, "hint": hint, "before": before or set(), "opened_at": time.time()})
    threading.Thread(
        target=_track, args=(name, kind, before or set(), hint, timeout, browser), daemon=True
    ).start()


def wait_until_ready(timeout=15):
    """Blocks until the last launched window has been found (or given up on)."""
    return _ready.wait(timeout)


def _find_target_window():
    with _lock:
        state = dict(_state)
    if not state["name"] or time.time() - state["opened_at"] > TARGET_EXPIRY_SECONDS:
        return None

    windows = gw.getAllWindows()
    if state["hwnd"]:
        for w in windows:
            if w._hWnd == state["hwnd"] and w.title and not _SPLASH_RE.match(w.title):
                return w
    # The exact window is gone (closed, or it was a splash screen that got
    # replaced). Prefer a matching window that appeared AFTER AURA opened
    # the app - so it can't grab a different document you already had open.
    before = state.get("before") or set()
    newer = _new_windows(before, state["hint"]) if before else []
    if newer:
        with _lock:
            _state["hwnd"] = newer[-1]._hWnd
        return newer[-1]
    if state["hint"] and not before:
        for w in windows:
            if (w.title and state["hint"].lower() in w.title.lower()
                    and not is_own_window(w.title) and not _SPLASH_RE.match(w.title)):
                return w
    return None


def describe():
    """One line for the AI about what's currently open, or "" if nothing."""
    win = _find_target_window()
    with _lock:
        name, kind, opened_at = _state["name"], _state["kind"], _state["opened_at"]
    if not name or (not win and _ready.is_set()):
        return ""
    minutes = int((time.time() - opened_at) // 60)
    when = "just now" if minutes < 1 else f"{minutes} min ago"
    window = f', window titled "{win.title}"' if win else ""
    return f"{name} ({kind}), opened {when}{window}"


def current_name():
    return _state["name"] if describe() else None


def _force_foreground(hwnd):
    """Windows normally refuses to let a background program steal focus.
    Attaching to the current foreground window's input thread is the
    standard, legitimate way for an assistant to switch focus on request."""
    fg = user32.GetForegroundWindow()
    fg_thread = user32.GetWindowThreadProcessId(fg, None)
    this_thread = kernel32.GetCurrentThreadId()
    attached = fg_thread != this_thread and user32.AttachThreadInput(fg_thread, this_thread, True)
    try:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(fg_thread, this_thread, False)


def focus_target():
    """Brings the remembered window to the front. Returns True if it's now
    the active window."""
    wait_until_ready(timeout=15)
    win = _find_target_window()
    if not win:
        return False

    try:
        _force_foreground(win._hWnd)
    except Exception as e:
        print(f"(active_window: couldn't focus {win.title!r}: {e})")
    time.sleep(0.4)

    active = gw.getActiveWindow()
    if active and active._hWnd == win._hWnd:
        return True

    # Last resort: click the window's title bar (safe - never clicks content).
    try:
        import pyautogui
        pyautogui.click(win.left + win.width // 2, win.top + 12)
        time.sleep(0.3)
        active = gw.getActiveWindow()
        return bool(active and active._hWnd == win._hWnd)
    except Exception:
        return False


def clear():
    with _lock:
        _state.update({"name": None, "kind": None, "hwnd": None, "hint": None, "before": set(), "opened_at": 0})
