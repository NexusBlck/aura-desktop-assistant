# mini_window.py
# Mini AURA: a small always-on-top window that's on screen whenever the big HUD
# isn't. It shares the HUD's chat log (kept across restarts), has a chat box, a
# mic button (push-to-talk), Do Not Disturb, a "next up" line (next reminder,
# important emails waiting, focus timer) and a button to open the full HUD. It
# collapses to a small orb.
#
# Getting out of the way:
#   * drag it by its top bar - it remembers where you put it;
#   * hold Ctrl and point at it - it slides to another corner;
#   * start typing in something underneath it - it slides away by itself;
#   * a fullscreen game/video in front - it hides until that's gone;
#   * the full HUD is open - it hides (no point showing both).
import ctypes
import json
import os
import threading
import time
from ctypes import wintypes

import webview

import face_window

user32 = ctypes.windll.user32
dwmapi = ctypes.windll.dwmapi
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.SetWindowPos.restype = wintypes.BOOL

TITLE = "AURA Mini"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
POS_FILE = os.path.join(BASE_DIR, "aura_mini.json")
CHAT_FILE = os.path.join(BASE_DIR, "aura_chat_log.json")
FULL_W, FULL_H = 340, 470          # logical px
ORB_W, ORB_H = 84, 84
MARGIN = 14
HWND_TOPMOST = wintypes.HWND(-1)
SWP_NOSIZE, SWP_NOACTIVATE = 0x0001, 0x0010
VK_CONTROL = 0x11

_window = None
_ready = threading.Event()
_queue = None
_push_to_talk = None
_state = {"collapsed": False, "x": None, "y": None, "unread": 0}
_chat = []                       # [{"role", "text", "t"}]
_chat_lock = threading.Lock()
_hidden_reason = None


# ------------------------------------------------------------------ persistence
def _load():
    try:
        with open(POS_FILE, encoding="utf-8") as f:
            _state.update({k: v for k, v in json.load(f).items() if k in ("collapsed", "x", "y")})
    except Exception:
        pass
    try:
        with open(CHAT_FILE, encoding="utf-8") as f:
            _chat.extend(json.load(f)[-60:])
    except Exception:
        pass


def _save_pos():
    try:
        with open(POS_FILE, "w", encoding="utf-8") as f:
            json.dump({k: _state[k] for k in ("collapsed", "x", "y")}, f)
    except Exception:
        pass


def _save_chat():
    try:
        with _chat_lock:
            data = _chat[-60:]
        with open(CHAT_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass


# ------------------------------------------------------------------ win32 helpers
def _hwnd():
    return user32.FindWindowW(None, TITLE) or None


def _scale(hwnd=None):
    try:
        return (user32.GetDpiForWindow(hwnd or _hwnd()) or 96) / 96
    except Exception:
        return 1.0


def _rect(hwnd):
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


def _monitor(x, y):
    mon = user32.MonitorFromPoint(wintypes.POINT(int(x), int(y)), 2)
    info = _MONITORINFO()
    info.cbSize = ctypes.sizeof(info)
    user32.GetMonitorInfoW(mon, ctypes.byref(info))
    m, w = info.rcMonitor, info.rcWork
    return (m.left, m.top, m.right, m.bottom), (w.left, w.top, w.right, w.bottom)


def _size():
    s = _scale()
    w, h = (ORB_W, ORB_H) if _state["collapsed"] else (FULL_W, FULL_H)
    return int(w * s), int(h * s)


def _corners(work):
    """Top-left positions of the 4 corner spots for the current size."""
    w, h = _size()
    m = int(MARGIN * _scale())
    l, t, r, b = work
    return [(r - w - m, b - h - m), (l + m, b - h - m), (r - w - m, t + m), (l + m, t + m)]


def _slide(x1, y1, ms=260):
    hwnd = _hwnd()
    if not hwnd:
        return
    l, t, _r, _b = _rect(hwnd)
    t0 = time.perf_counter()
    while True:
        p = min(1.0, (time.perf_counter() - t0) / (ms / 1000))
        e = 1 - (1 - p) ** 3
        user32.SetWindowPos(hwnd, HWND_TOPMOST, int(l + (x1 - l) * e), int(t + (y1 - t) * e), 0, 0,
                            SWP_NOSIZE | SWP_NOACTIVATE)
        if p >= 1:
            break
        try:
            dwmapi.DwmFlush()
        except Exception:
            time.sleep(1 / 60)
    _state["x"], _state["y"] = int(x1), int(y1)
    _save_pos()


def _place_initial():
    hwnd = _hwnd()
    w, h = _size()
    if _state["x"] is None:
        _mon, work = _monitor(user32.GetSystemMetrics(0) // 2, user32.GetSystemMetrics(1) // 2)
        _state["x"], _state["y"] = _corners(work)[0]
    # keep it on a screen (monitors can change between sessions)
    _mon, work = _monitor(_state["x"] + w // 2, _state["y"] + h // 2)
    x = min(max(_state["x"], work[0]), work[2] - w)
    y = min(max(_state["y"], work[1]), work[3] - h)
    _state["x"], _state["y"] = x, y
    user32.SetWindowPos(hwnd, HWND_TOPMOST, x, y, w, h, SWP_NOACTIVATE)


# ------------------------------------------------------------------ the JS bridge
class MiniAPI:
    def ui_skin_get(self):
        import ui_skins
        return ui_skins.get()

    def send(self, text):
        text = (text or "").strip()
        if text and _queue is not None:
            from voice import speaking_event, interrupt_event
            if speaking_event.is_set():
                interrupt_event.set()
            _queue.put(text)

    def init(self):
        import presence
        with _chat_lock:
            chat = list(_chat[-60:])
        return {"chat": chat, "collapsed": _state["collapsed"], "dnd": presence.dnd()}

    def expand_hud(self):
        face_window.show_face()

    def set_collapsed(self, collapsed):
        threading.Thread(target=_set_collapsed, args=(bool(collapsed),), daemon=True).start()

    def talk(self):
        if _push_to_talk:
            _push_to_talk()

    def toggle_dnd(self):
        import presence
        on = presence.set_dnd(not presence.dnd())
        return on

    def emails(self):
        import gmail_watch
        return gmail_watch.pending_list()

    def dismiss_email(self, email_id):
        import gmail_watch
        gmail_watch.dismiss([email_id])
        return gmail_watch.pending_list()

    def dismiss_all_emails(self):
        import gmail_watch
        gmail_watch.dismiss([p["id"] for p in gmail_watch.pending_list()])
        return []

    def move_aside(self):
        threading.Thread(target=_dodge, daemon=True).start()


def _set_collapsed(collapsed):
    hwnd = _hwnd()
    if not hwnd or collapsed == _state["collapsed"]:
        return
    l, t, r, b = _rect(hwnd)
    _state["collapsed"] = collapsed
    w, h = _size()
    # keep the corner nearest the screen edge where it is (so it shrinks/grows "into" its corner)
    _mon, work = _monitor((l + r) // 2, (t + b) // 2)
    right = (work[2] - r) < (l - work[0])
    bottom = (work[3] - b) < (t - work[1])
    x = r - w if right else l
    y = b - h if bottom else t
    user32.SetWindowPos(hwnd, HWND_TOPMOST, x, y, w, h, SWP_NOACTIVATE)
    _state["x"], _state["y"] = x, y
    _save_pos()
    _shape(hwnd)
    _js(f"window.setCollapsed({json.dumps(collapsed)})")


def _js(code):
    if _window and _ready.is_set():
        try:
            _window.evaluate_js(code)
        except Exception:
            pass


def _on_loaded():
    face_window.set_backdrop(_window, (5, 12, 19))   # same as the panel, so nothing pale shows
    hwnd = _hwnd()
    if hwnd:
        style = user32.GetWindowLongW(hwnd, -20)
        user32.SetWindowLongW(hwnd, -20, (style | 0x80) & ~0x40000)   # tool window: not on the taskbar
        _place_initial()
        _shape(hwnd)
        # pywebview shows transparent windows as they load, and that first showing comes out opaque
        # (a grey box). Hiding it now and letting the watcher show it again gives true transparency.
        user32.ShowWindow(hwnd, 0)
    _ready.set()


def create_window(command_queue, push_to_talk=None):
    """Must be called BEFORE webview.start(), like AURA's other windows."""
    global _window, _queue, _push_to_talk
    _queue = command_queue
    _push_to_talk = push_to_talk
    _load()
    _window = webview.create_window(
        TITLE, f"http://localhost:{face_window.PORT}/mini.html",
        width=ORB_W if _state["collapsed"] else FULL_W, height=ORB_H if _state["collapsed"] else FULL_H,
        x=-4000, y=-4000, min_size=(40, 40), resizable=False, frameless=True, easy_drag=False,
        on_top=True, transparent=True, shadow=False, hidden=True, background_color="#000000", js_api=MiniAPI())
    _window.events.loaded += _on_loaded
    # No taskbar button, and closing it (e.g. Alt+F4) just shrinks it to the orb instead of destroying it.
    face_window.make_helper_window(_window, TITLE, on_close=lambda: threading.Thread(
        target=_set_collapsed, args=(True,), daemon=True).start())


def show_now(expanded=True):
    """The big HUD was closed/minimised: Mini AURA appears right away (expanded)."""
    def go():
        if not _ready.wait(10):
            return
        if expanded and _state["collapsed"]:
            _set_collapsed(False)
        _show(True)
    threading.Thread(target=go, daemon=True).start()


def set_push_to_talk(fn):
    global _push_to_talk
    _push_to_talk = fn


# ------------------------------------------------------------------ mirrored from the HUD
def add_log(role, text):
    entry = {"role": role, "text": text, "t": time.time()}
    with _chat_lock:
        _chat.append(entry)
        del _chat[:-60]
    _save_chat()
    _js(f"window.addLine && window.addLine({json.dumps(entry)})")


def set_status(talking=None, thinking=None, listening=None):
    payload = {k: v for k, v in (("talking", talking), ("thinking", thinking), ("listening", listening)) if v is not None}
    _js(f"window.setStatus && window.setStatus({json.dumps(payload)})")


# ------------------------------------------------------------------ the watcher
def _hud_showing():
    try:
        hwnd = face_window._window.native.Handle.ToInt32()
    except Exception:
        return False
    if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
        return False
    # covered by another maximised window? then it isn't really "showing"
    fg = user32.GetForegroundWindow()
    return fg == hwnd or user32.GetAncestor(fg, 2) == hwnd


def _fullscreen_app():
    """A game/video/presentation filling the whole monitor in front?"""
    fg = user32.GetForegroundWindow()
    if not fg or fg == _hwnd():
        return False
    buf = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(fg, buf, 64)
    if buf.value in ("Progman", "WorkerW", "Shell_TrayWnd"):
        return False
    l, t, r, b = _rect(fg)
    mon, _work = _monitor((l + r) // 2, (t + b) // 2)
    return l <= mon[0] and t <= mon[1] and r >= mon[2] and b >= mon[3]


def _caret_point():
    """Screen position of the text cursor in the window you're typing in (None if none)."""
    class GUITHREADINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD), ("hwndActive", wintypes.HWND),
                    ("hwndFocus", wintypes.HWND), ("hwndCapture", wintypes.HWND), ("hwndMenuOwner", wintypes.HWND),
                    ("hwndMoveSize", wintypes.HWND), ("hwndCaret", wintypes.HWND), ("rcCaret", wintypes.RECT)]
    info = GUITHREADINFO()
    info.cbSize = ctypes.sizeof(info)
    if not user32.GetGUIThreadInfo(0, ctypes.byref(info)) or not info.hwndCaret:
        return None
    pt = wintypes.POINT(info.rcCaret.left, info.rcCaret.bottom)
    user32.ClientToScreen(info.hwndCaret, ctypes.byref(pt))
    return pt.x, pt.y


def _cursor():
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def _inside(pt, rect, pad=0):
    return pt and rect[0] - pad <= pt[0] <= rect[2] + pad and rect[1] - pad <= pt[1] <= rect[3] + pad


def _dodge(avoid=None):
    """Slides to the corner farthest from `avoid` (the cursor/caret)."""
    hwnd = _hwnd()
    if not hwnd:
        return
    l, t, r, b = _rect(hwnd)
    avoid = avoid or _cursor()
    _mon, work = _monitor((l + r) // 2, (t + b) // 2)
    w, h = _size()
    best = max(_corners(work), key=lambda c: (c[0] + w / 2 - avoid[0]) ** 2 + (c[1] + h / 2 - avoid[1]) ** 2)
    _slide(*best)


def _shape(hwnd):
    """Cuts the window to exactly what's drawn - the rounded panel, or the round orb (its unread
    badge sits inside the circle) - so there's no square around it, and clicks inside work normally."""
    gdi = ctypes.windll.gdi32
    l, t, r, b = _rect(hwnd)
    w, h, s = r - l, b - t, _scale(hwnd)
    if _state["collapsed"]:
        i = round(12 * s)                                         # .bubble { inset: 12px }
        rgn = gdi.CreateEllipticRgn(i, i, w - i + 1, h - i + 1)
    else:
        i, d = round(10 * s), round(36 * s)                       # .panel { inset: 10px; border-radius: 18px }
        rgn = gdi.CreateRoundRectRgn(i, i, w - i + 1, h - i + 1, d, d)
    if not user32.SetWindowRgn(hwnd, rgn, True):                  # (Windows owns the region after this)
        gdi.DeleteObject(rgn)


def _show(on):
    hwnd = _hwnd()
    if not hwnd:
        return
    visible = bool(user32.IsWindowVisible(hwnd))
    if on and not visible:
        user32.ShowWindow(hwnd, 4)   # SW_SHOWNOACTIVATE
        user32.SetWindowPos(hwnd, HWND_TOPMOST, _state["x"], _state["y"], 0, 0, SWP_NOSIZE | SWP_NOACTIVATE)
        _shape(hwnd)
    elif not on and visible:
        user32.ShowWindow(hwnd, 0)


def _info_line():
    """'Next up' chips: next reminder, important emails waiting, focus timer."""
    chips = []
    try:
        import reminders
        nxt = reminders.pending()[:1]
        if nxt:
            import datetime
            due = datetime.datetime.fromisoformat(nxt[0]["due"])
            chips.append({"icon": "⏰", "text": f"{due.strftime('%I:%M %p').lstrip('0')} · {nxt[0]['text'][:28]}"})
    except Exception:
        pass
    try:
        import gmail_watch
        n = len(gmail_watch._st()["pending"])
        if n:
            chips.append({"icon": "✉", "text": f"{n} important email{'s' if n != 1 else ''}", "action": "emails"})
    except Exception:
        pass
    try:
        import focus
        st = focus.status()
        if st and st.get("mode") in ("focus", "break", "paused"):
            label = "Break" if st["mode"] == "break" else (st.get("label") or "Focus")
            mins = max(1, int(st.get("remaining", 0)) // 60)
            chips.append({"icon": "◎", "text": f"{label} · {mins} min left" + (" (paused)" if st["mode"] == "paused" else "")})
    except Exception:
        pass
    return chips


def _watch():
    last_drag_check = 0
    last_info = 0
    while True:
        time.sleep(0.15)
        if not _ready.is_set():
            continue
        hwnd = _hwnd()
        if not hwnd:
            continue
        try:
            hidden_for = "hud" if _hud_showing() else ("fullscreen" if _fullscreen_app() else None)
            _show(hidden_for is None)
            if hidden_for:
                continue
            rect = _rect(hwnd)
            fg = user32.GetForegroundWindow()
            mini_active = fg == hwnd
            # Hold Ctrl + point at it -> slide to another corner
            if (user32.GetAsyncKeyState(VK_CONTROL) & 0x8000) and _inside(_cursor(), rect):
                _dodge(_cursor())
                continue
            # Typing in something underneath it -> slide away
            if not mini_active:
                caret = _caret_point()
                if caret and _inside(caret, rect, pad=int(20 * _scale())):
                    _dodge(caret)
                    continue
            # Remember where it was dragged to
            if time.time() - last_drag_check > 1.0:
                last_drag_check = time.time()
                if (rect[0], rect[1]) != (_state["x"], _state["y"]) and not (user32.GetAsyncKeyState(0x01) & 0x8000):
                    _state["x"], _state["y"] = rect[0], rect[1]
                    _save_pos()
            if time.time() - last_info > 15:
                last_info = time.time()
                _js(f"window.setInfo && window.setInfo({json.dumps(_info_line())})")
        except Exception as e:
            print(f"(mini watcher: {e})")
            time.sleep(1)


def start():
    threading.Thread(target=_watch, daemon=True, name="mini-watch").start()


def refresh_info():
    _js(f"window.setInfo && window.setInfo({json.dumps(_info_line())})")
