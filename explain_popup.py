# explain_popup.py
# Highlight ANY word, phrase or passage anywhere on your screen and press the
# hotkey: AURA's cat hops OUT of the AURA window, walks across your screen to
# where you're working, sits down next to the text and explains what it
# means - using a live web search, what AURA knows about you and the app
# you're in - in a speech bubble AND out loud. Ask follow-ups in the bubble.
# Click the cat (or the x, or Esc) and it walks back home into AURA.
#
# Two windows do this: the cat's transparent stage (always on top, never
# steals focus) and its speech bubble. The journey - walking along your
# taskbar, ladders, jetpacks, shoving windows out of the way and much more,
# different every time - is animated in Face/cat_stage.js; cat_director.py
# gives it the real screen geometry. Positions here are PHYSICAL pixels.
import ctypes
import json
import threading
import time
import webbrowser
from ctypes import wintypes

import keyboard
import pyperclip
import webview

import explainer
import face_window
from config import EXPLAIN_HOTKEY

user32 = ctypes.windll.user32
# Pointer-sized handles must be declared, or HWND_TOPMOST (-1) gets truncated
# to 32 bits on 64-bit Python and SetWindowPos silently fails.
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.SetWindowPos.restype = wintypes.BOOL
try:
    shcore = ctypes.windll.shcore
except OSError:
    shcore = None

WALKER_TITLE = "AURA Cat"
BUBBLE_TITLE = "AURA Explain"

# Logical (96-dpi) sizes. The cat box matches the HUD cat so the hand-over
# from the AURA window is seamless; the walker window has headroom above the
# cat for its thought bubble.
CAT_W, CAT_H = 112, 98
HEADROOM = 26
WALKER_W, WALKER_H = CAT_W, CAT_H + HEADROOM
BUBBLE_W, BUBBLE_H = 410, 330
OFFSCREEN = -4000          # where the windows wait while hidden

SW_HIDE, SW_SHOWNOACTIVATE = 0, 4
SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0004, 0x0010
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW, WS_EX_APPWINDOW, WS_EX_NOACTIVATE = 0x80, 0x40000, 0x08000000
HWND_TOPMOST = wintypes.HWND(-1)

_walker = None
_bubble = None
_walker_ready = threading.Event()
_bubble_ready = threading.Event()

_lock = threading.Lock()
_gen = 0                 # bumps on every new trip; stale trips stop themselves
_out = False             # is the cat currently out on the desktop?
_home_from_hud = False   # did it come out of the HUD (so it should walk back there)?
_current = None          # explainer.Explanation for the bubble on screen
_cat_speaking = False


# ------------------------------------------------------------------ win32 helpers
class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


def _hwnd(title):
    return user32.FindWindowW(None, title) or None


def _dpi_scale_for_window(hwnd):
    try:
        return (user32.GetDpiForWindow(hwnd) or 96) / 96
    except Exception:
        return 1.0


def _cursor():
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def _work_area(x, y):
    """(left, top, right, bottom) of the work area (no taskbar) on the monitor at x,y."""
    mon = user32.MonitorFromPoint(wintypes.POINT(x, y), 2)  # MONITOR_DEFAULTTONEAREST
    info = _MONITORINFO()
    info.cbSize = ctypes.sizeof(_MONITORINFO)
    user32.GetMonitorInfoW(mon, ctypes.byref(info))
    r = info.rcWork
    return r.left, r.top, r.right, r.bottom


def _monitor_scale(x, y):
    if shcore is None:
        return 1.0
    try:
        mon = user32.MonitorFromPoint(wintypes.POINT(x, y), 2)
        dx, dy = wintypes.UINT(), wintypes.UINT()
        shcore.GetDpiForMonitor(mon, 0, ctypes.byref(dx), ctypes.byref(dy))
        return (dx.value or 96) / 96
    except Exception:
        return 1.0


def _foreground_title():
    hwnd = user32.GetForegroundWindow()
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _make_tool_window(hwnd, no_activate):
    """Keep the cat and its bubble off the taskbar / Alt+Tab (and the cat from ever taking focus)."""
    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    style = (style | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
    if no_activate:
        style |= WS_EX_NOACTIVATE
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)


def _place(hwnd, x, y):
    user32.SetWindowPos(hwnd, HWND_TOPMOST, int(x), int(y), 0, 0, SWP_NOSIZE | SWP_NOACTIVATE)


def _js(win, code):
    try:
        return win.evaluate_js(code)
    except Exception:
        return None


# ------------------------------------------------------------------ windows
class CatAPI:
    """Called from the cat's stage page (Face/cat_stage.js)."""

    def clicked(self):
        threading.Thread(target=go_home, daemon=True).start()

    def arrived(self, x, y):
        if _stage:
            _stage.arrived_at = (float(x), float(y))
            _stage.arrived.set()

    def home_reached(self):
        if _stage:
            _stage.home.set()

    def push_window(self, oid):
        if _stage:
            _stage.push(str(oid))


class BubbleAPI:
    def ask(self, question):
        threading.Thread(target=_follow_up, args=(question,), daemon=True).start()

    def close(self):
        threading.Thread(target=go_home, daemon=True).start()

    def open_url(self, url):
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            webbrowser.open(url)


# pywebview force-shows transparent windows whenever their page starts
# loading (even hidden=True ones), so they're created off-screen and hidden
# again here once loaded - unless the cat is already out using them.
def _set_size(hwnd, w, h):
    """Exact size in logical px (pywebview's own sizing is off for frameless/transparent windows)."""
    s = _dpi_scale_for_window(hwnd)
    user32.SetWindowPos(hwnd, None, 0, 0, round(w * s), round(h * s),
                        SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE)


def _walker_loaded():
    hwnd = _hwnd(WALKER_TITLE)
    if hwnd:
        _make_tool_window(hwnd, no_activate=True)
        _set_size(hwnd, WALKER_W, WALKER_H)
        if not _out:
            user32.ShowWindow(hwnd, SW_HIDE)
    _walker_ready.set()


def _bubble_loaded():
    hwnd = _hwnd(BUBBLE_TITLE)
    if hwnd:
        _make_tool_window(hwnd, no_activate=False)
        _set_size(hwnd, BUBBLE_W, BUBBLE_H)
        if not _out:
            user32.ShowWindow(hwnd, SW_HIDE)
    _bubble_ready.set()


def create_popup_window():
    """Must be called BEFORE webview.start(), same as the other windows.
    Served from the same local server as the face window."""
    global _walker, _bubble
    base = f"http://localhost:{face_window.PORT}"
    _walker = webview.create_window(
        WALKER_TITLE, f"{base}/cat_walker.html",
        width=WALKER_W, height=WALKER_H, x=OFFSCREEN, y=OFFSCREEN, min_size=(40, 40), resizable=False, frameless=True, easy_drag=False,
        on_top=True, transparent=True, shadow=False, focus=False, hidden=True,
        background_color="#000000", js_api=CatAPI(),
    )
    _walker.events.loaded += _walker_loaded
    face_window.make_helper_window(_walker, WALKER_TITLE, no_activate=True,
                                   on_close=lambda: threading.Thread(target=go_home, daemon=True).start())
    _bubble = webview.create_window(
        BUBBLE_TITLE, f"{base}/explain_popup.html",
        width=BUBBLE_W, height=BUBBLE_H, x=OFFSCREEN, y=OFFSCREEN, min_size=(40, 40), resizable=False, frameless=True, easy_drag=False,
        on_top=True, transparent=True, shadow=False, hidden=True,
        background_color="#000000", js_api=BubbleAPI(),
    )
    _bubble.events.loaded += _bubble_loaded
    face_window.make_helper_window(_bubble, BUBBLE_TITLE,
                                   on_close=lambda: threading.Thread(target=go_home, daemon=True).start())


# ------------------------------------------------------------------ choreography
# The adventure itself (routes, props, randomness) lives in Face/cat_stage.js;
# cat_director.Stage feeds it the real screen and handles windows in the way.
_stage = None            # cat_director.Stage for the current outing
_travelling = False      # mid-trip (new requests wait until the cat has landed)


def _alive(gen):
    return gen == _gen


def _cat_state(state):
    _js(_walker, f"window.catState && window.catState({json.dumps(state)})")


def _home_feet():
    """Physical feet position of the HUD cat (bottom-centre of its box), or None if AURA isn't on screen."""
    anchor = face_window.cat_screen_anchor()
    if not anchor:
        return None
    x, y, w, h, _scale = anchor
    return x + w / 2, y + h


def _bubble_layout(cat_rect, cx, cy):
    """Bubble spot next to the resting cat: ((bx, by), side, tail_top). cat_rect = (x, y, w, h) physical."""
    left, top, right, bottom = _work_area(cx, cy)
    s = _monitor_scale(cx, cy)
    x, y, ww, wh = cat_rect
    bw, bh = BUBBLE_W * s, BUBBLE_H * s
    side = "left"  # which side of the bubble the tail is on
    bx = x + ww - 8 * s
    if bx + bw > right:
        bx = x - bw + 8 * s
        side = "right"
    bx = min(max(bx, left), right - bw)
    head_y = y + (HEADROOM + CAT_H * 0.4) * s
    by = min(max(head_y - bh * 0.42, top), bottom - bh)
    tail_top = (head_y - by) / s - 6 - 9   # page px inside the bubble's 6px margin, centred tail
    tail_top = min(max(tail_top, 22), BUBBLE_H - 50)
    return (bx, by), side, tail_top


def _show_bubble(spot, side, tail_top, phrase, app):
    hwnd = _hwnd(BUBBLE_TITLE)
    _js(_bubble, f"window.setTail({json.dumps(side)}, {tail_top:.0f})")
    _js(_bubble, f"window.resetBubble({json.dumps({'phrase': phrase, 'app': app})})")
    _place(hwnd, *spot)
    user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
    _place(hwnd, *spot)


def _hide_bubble():
    hwnd = _hwnd(BUBBLE_TITLE)
    if hwnd:
        user32.ShowWindow(hwnd, SW_HIDE)


def _say(gen, answer):
    """Shows the answer in the bubble and has the cat say it out loud."""
    global _cat_speaking
    if not _alive(gen):
        return
    _js(_bubble, f"window.showAnswer({json.dumps(answer)})")
    text = explainer.spoken_text(answer)
    if not text:
        _cat_state("idle")
        return
    from voice import speak
    _cat_state("talk")
    _cat_speaking = True
    try:
        speak(text)
    finally:
        _cat_speaking = False
        if _alive(gen):
            _cat_state("idle")


def _stop_speaking():
    if _cat_speaking:
        from voice import interrupt_event
        interrupt_event.set()


def _trip(gen, text, app, cursor, target_hwnd):
    """One whole visit: come out, travel across the desktop, explain."""
    global _out, _home_from_hud, _current, _stage, _travelling
    if not (_walker_ready.wait(5) and _bubble_ready.wait(5)):
        print("(explain cat: windows not ready yet)")
        return
    import cat_director

    # Start researching straight away - it runs while the cat travels.
    explanation = explainer.Explanation(text, app)
    _current = explanation
    result = {}
    stage_text = {"text": "Hmm, let me look into this…"}
    bubble_up = threading.Event()

    def on_stage(t):
        stage_text["text"] = t
        if bubble_up.is_set() and _alive(gen):
            _js(_bubble, f"window.setStage({json.dumps(t)})")

    def research():
        try:
            explanation.gather(on_stage)
            result["answer"] = explanation.explain()
        except Exception as e:
            print(f"(explain cat: research failed: {e!r})")
            result["error"] = "I couldn't work that one out just now - check your internet connection and try again?"
    worker = threading.Thread(target=research, daemon=True)
    worker.start()

    hwnd = _hwnd(WALKER_TITLE)
    if _out and _stage and _stage.feet:
        # Already out on the desktop: set off from where it's sitting.
        _stop_speaking()
        _hide_bubble()
        start, start_from = _stage.feet, "here"
        stage = _stage
    else:
        stage = cat_director.Stage(hwnd, lambda code: _js(_walker, code))
        home = _home_feet()
        _home_from_hud = home is not None
        if home:
            face_window.cat_leave()   # the HUD cat hops out as this one appears in its place
            start, start_from = home, "hud"
        else:
            start, start_from = None, "tray"
    _stage = stage

    # Rough spot for the bubble, so windows sitting there get shoved aside too.
    s = _monitor_scale(*cursor)
    guess_cat = (cursor[0] - WALKER_W * s / 2, cursor[1] + 14 * s, WALKER_W * s, WALKER_H * s)
    (gbx, gby), _side, _tail = _bubble_layout(guess_cat, *cursor)
    bubble_guess = (int(gbx), int(gby), int(gbx + BUBBLE_W * s), int(gby + BUBBLE_H * s))

    plan = stage.plan(start, start_from, cursor, target_hwnd, bubble_guess)
    _out, _travelling = True, True
    try:
        stage.run_trip(plan)
    finally:
        _travelling = False
    if not _alive(gen):
        return

    spot, side, tail_top = _bubble_layout(stage.rest_rect(), *cursor)
    _show_bubble(spot, side, tail_top, text, app)
    bubble_up.set()
    _js(_bubble, f"window.setStage({json.dumps(stage_text['text'])})")
    _cat_state("think")

    while worker.is_alive():
        if not _alive(gen):
            return
        worker.join(0.1)
    if not _alive(gen):
        return
    if "error" in result:
        _js(_bubble, f"window.showError({json.dumps(result['error'])})")
        _cat_state("idle")
        return
    _say(gen, result["answer"])


def _follow_up(question):
    gen = _gen
    explanation = _current
    if not (explanation and _out and question and question.strip()):
        return
    _stop_speaking()
    _cat_state("think")
    try:
        answer = explanation.follow_up(question.strip())
    except Exception as e:
        print(f"(explain cat: follow-up failed: {e!r})")
        if _alive(gen):
            _js(_bubble, "window.showError('Sorry, I lost my train of thought - ask me again?')")
            _cat_state("idle")
        return
    _say(gen, answer)


def explain_at(text, app, cursor, target_hwnd=None):
    """Send the cat to explain `text` near `cursor` (physical px)."""
    global _gen
    if _travelling:
        print("(explain cat: still on its way - try again once it has landed)")
        return
    with _lock:
        _gen += 1
        gen = _gen
    threading.Thread(target=_trip, args=(gen, text, app, cursor, target_hwnd), daemon=True).start()


def go_home():
    """Close the bubble; the cat makes its way back into the AURA window (or off the screen)."""
    global _gen, _out
    if _travelling or not _out:
        _hide_bubble()
        return
    with _lock:
        _gen += 1
    _stop_speaking()
    _hide_bubble()
    home = _home_feet() if _home_from_hud else None
    try:
        _stage.run_home(home)
    finally:
        face_window.cat_return()   # the HUD cat is back in its spot (or waits there if AURA is hidden)
        _out = False


# kept for anything that still calls the old API
def hide_popup():
    threading.Thread(target=go_home, daemon=True).start()


# ------------------------------------------------------------------ hotkey
def _capture_selected_text():
    """Copies whatever's currently highlighted, system-wide, and returns it.
    Releases the hotkey's own modifier keys first (Ctrl+Alt can otherwise
    still look 'held' to the OS right after the hotkey fires, corrupting
    the Ctrl+C we send next) and verifies the clipboard actually changed -
    if it didn't, we assume the copy failed rather than trusting stale
    leftover clipboard content from something unrelated. Puts back whatever
    was on the clipboard before."""
    try:
        previous = pyperclip.paste()
    except Exception:
        previous = None

    new_text = ""
    try:
        for key in EXPLAIN_HOTKEY.split("+"):
            keyboard.release(key.strip())
        time.sleep(0.05)
        # Clear the clipboard to a sentinel so we can tell if copy actually happened.
        pyperclip.copy("")
        time.sleep(0.05)
        keyboard.send("ctrl+c")
        time.sleep(0.25)
        new_text = pyperclip.paste() or ""
    except Exception:
        new_text = ""
    finally:
        if previous is not None:
            try:
                pyperclip.copy(previous)
            except Exception:
                pass

    return new_text if new_text.strip() else ""  # nothing captured - don't guess with stale content


def _hotkey_handler():
    target_hwnd = user32.GetForegroundWindow()
    app = _foreground_title()
    if app.startswith("AURA"):
        app = ""  # highlighted inside AURA itself - no outside context
    text = _capture_selected_text()
    if not text:
        return
    explain_at(" ".join(text.split()), app, _cursor(), target_hwnd)


def start_hotkey_listener():
    keyboard.add_hotkey(EXPLAIN_HOTKEY, _hotkey_handler)
    print(f"(Explain cat: highlight any word or phrase and press {EXPLAIN_HOTKEY})")
