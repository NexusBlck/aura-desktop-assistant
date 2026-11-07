# face_window.py
# Opens AURA's main HUD window - the animated command center: reactor core,
# live system graphs, conversation log, tasks, reminders, focus timer, notes
# and recent items, plus the command box.
import os
import re
import json
import time
import ctypes
import threading
import webbrowser
from ctypes import wintypes
import http.server
import socketserver
import webview

FACE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "face")
PORT = 8734

_window = None
_ready = threading.Event()


AURA_DIR = os.path.dirname(os.path.abspath(__file__))
STARTED_AT = time.time()


def _recent_items(limit=6):
    """Recent web briefings and file-search result folders, newest first."""
    items = []
    briefings = os.path.join(FACE_DIR, "briefings")
    if os.path.isdir(briefings):
        for name in os.listdir(briefings):
            if name.endswith(".html"):
                path = os.path.join(briefings, name)
                # "20261001-011159-city-library-overview.html" -> "City library overview"
                title = re.sub(r"^\d{8}-\d{4,6}-", "", name[:-5]).replace("-", " ").strip().capitalize()
                items.append({"kind": "briefing", "title": title or "Briefing", "target": name,
                              "time": os.path.getmtime(path)})
    results = os.path.join(AURA_DIR, "search_results")
    if os.path.isdir(results):
        for name in os.listdir(results):
            path = os.path.join(results, name)
            if os.path.isdir(path):
                items.append({"kind": "files", "title": name.replace(" (AURA search)", ""), "target": path,
                              "time": os.path.getmtime(path)})
    items.sort(key=lambda i: i["time"], reverse=True)
    return items[:limit]


def _module_status():
    """Which of AURA's abilities are actually connected right now (the lights on the HUD)."""
    import config
    return {
        "VOICE": True,
        "AI": bool(getattr(config, "LLM_API_KEY", "")),
        "WEB": bool(getattr(config, "TAVILY_API_KEY", "")),
        "CALENDAR": os.path.exists(os.path.join(AURA_DIR, "google_token.json")),
        "FILES": os.path.exists(os.path.join(AURA_DIR, "aura_file_index.json")),
        "SPOTIFY": bool(getattr(config, "SPOTIFY_CLIENT_ID", "")),
    }


class FaceAPI:
    """Exposed to the face window's JavaScript as window.pywebview.api -
    lets the GUI input box feed commands into the SAME queue voice and
    terminal typing already use, and gives the HUD real data and controls."""
    def __init__(self, command_queue):
        self.command_queue = command_queue

    def ui_skin_get(self):
        import ui_skins
        return ui_skins.get()

    def ui_skin_set(self, skin, theme=None):
        import ui_skins
        return ui_skins.set(skin, theme)

    def send_command(self, text):
        if text and text.strip() and self.command_queue is not None:
            self.command_queue.put(text.strip())
        return None

    def open_log_dialog(self, session_type):
        import log_dialog
        log_dialog.show_log_dialog(session_type)
        return None

    def get_face_data(self):
        """Everything the HUD shows - all real data, never fake gauges."""
        from tasks import get_pending_tasks
        from life_data import get_today_summary, focus_minutes_today
        import system_monitor
        import reminders
        import focus

        last_build = None
        build_state_file = os.path.join(AURA_DIR, "aura_last_build.json")
        if os.path.exists(build_state_file):
            try:
                with open(build_state_file, "r", encoding="utf-8") as f:
                    last_build = json.load(f)
            except Exception:
                pass

        today = get_today_summary()
        today["focus_minutes"] = focus_minutes_today()
        return json.dumps({
            "system": system_monitor.snapshot(),
            "tasks": [t["text"] for t in get_pending_tasks()[:12]],
            "today": today,
            "last_build": last_build,
            "reminders": reminders.pending()[:10],
            "focus": focus.status(),
            "recent": _recent_items(),
            "modules": _module_status(),
            "uptime": int(time.time() - STARTED_AT),
            "server_time": time.time(),
        })

    # ---- tasks
    def add_task(self, text):
        from tasks import add_task
        if text and text.strip():
            add_task(text.strip())
        return True

    def complete_task(self, text):
        from tasks import complete_task
        return bool(complete_task(text or ""))

    # ---- notes
    def get_notes(self):
        from notes import get_notes
        return get_notes()

    def save_notes(self, text):
        from notes import save_notes
        return save_notes(text)

    # ---- focus timer
    def focus_start(self, minutes, label=""):
        import focus
        return focus.start(minutes, label or "Focus")

    def focus_pause(self):
        import focus
        return focus.pause()

    def focus_resume(self):
        import focus
        return focus.resume()

    def focus_stop(self):
        import focus
        return focus.stop()

    # ---- reminders
    def reminder_add(self, text):
        import reminders
        return reminders.add_from_text(text or "")

    def reminder_cancel(self, reminder_id):
        import reminders
        return reminders.cancel(reminder_id)

    # ---- recent items
    def open_item(self, kind, target):
        if kind == "briefing":
            name = os.path.basename(target or "")  # only files inside the briefings folder
            if name.endswith(".html") and os.path.exists(os.path.join(FACE_DIR, "briefings", name)):
                webbrowser.open(f"http://localhost:{PORT}/briefings/{name}")
                return True
        elif kind == "files":
            results = os.path.realpath(os.path.join(AURA_DIR, "search_results"))
            path = os.path.realpath(target or "")
            if path.startswith(results + os.sep) and os.path.isdir(path):
                os.startfile(path)
                return True
        return False


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=FACE_DIR, **kwargs)

    def log_message(self, format, *args):
        pass  # don't spam the console with HTTP logs

    def do_GET(self):
        # Live webcam for the Vision window (only while the camera is on).
        if self.path.startswith("/camera/stream.mjpg"):
            import camera
            if not camera.is_on():
                self.send_error(503, "Camera is off")
                return
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                camera.mjpeg_stream(self.wfile.write)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass  # viewer closed the window
            return
        super().do_GET()


class _ThreadedServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    # Each request gets its own thread, so the endless camera stream can't
    # block the HUD's other requests.
    daemon_threads = True
    allow_reuse_address = True


def _start_local_server():
    with _ThreadedServer(("localhost", PORT), _QuietHandler) as httpd:
        httpd.serve_forever()


def _on_loaded():
    _ready.set()
    try:
        _window.maximize()
    except Exception:
        pass
    _flush_pending_log()


def _on_closing():
    """Clicking the X doesn't quit AURA: the big HUD shrinks into Mini AURA (and the
    tray icon / Mini's expand button / Ctrl+Alt+Space bring it back)."""
    try:
        _window.hide()
    except Exception:
        pass
    _mini("show_now", expanded=True)
    return False  # cancel the actual close/destroy


def _on_minimized():
    """Minimising the HUD also shrinks it into Mini AURA (instead of a taskbar button)."""
    try:
        _window.hide()
        _window.restore()
    except Exception:
        pass
    _mini("show_now", expanded=True)


def show_face():
    if _window:
        try:
            _window.show()
        except Exception:
            pass
    try:
        _window.maximize()
    except Exception:
        pass


def launch_face_window(command_queue=None, start_hidden=False):
    """Creates the face HUD window + all sub-windows (schedule, dashboard,
    popup, log dialog). Does NOT start the webview event loop - main.py owns
    that single call, since only one webview.start() can exist per process
    (needed so the login window can gate everything else)."""
    global _window
    threading.Thread(target=_start_local_server, daemon=True).start()

    _window = webview.create_window(
        "AURA",
        f"http://localhost:{PORT}/aura_face.html",
        width=1400,
        height=850,
        resizable=True,
        background_color="#04070a",
        hidden=start_hidden,
        js_api=FaceAPI(command_queue),
    )
    _window.events.loaded += _on_loaded
    _window.events.closing += _on_closing
    _window.events.minimized += _on_minimized

    import schedule_window
    schedule_window.create_window()

    import dashboard_window
    dashboard_window.create_window()

    import explain_popup
    explain_popup.create_popup_window()

    import log_dialog
    log_dialog.create_log_dialog_window()

    import vision_window
    vision_window.create_window()

    import cdc_window
    cdc_window.create_window()


def make_helper_window(window, title, no_activate=False, on_close=None):
    """For AURA's helper windows (the cat, its bubble, Mini AURA): turns them into tool
    windows BEFORE they're first shown, so they never get their own taskbar button (or
    Alt+Tab entry) - and makes closing them harmless: `on_close` runs instead (e.g. hide)."""
    def before_show():
        # (Don't touch the form's ShowInTaskbar property here - that rebuilds the window and
        # aborts the WebView2 engine inside it. A brief taskbar button during startup is gone
        # once the window has loaded and been hidden/re-shown as a tool window.)
        hwnd = ctypes.windll.user32.FindWindowW(None, title)
        if hwnd:
            style = ctypes.windll.user32.GetWindowLongW(hwnd, -20)
            style = (style | 0x80) & ~0x40000            # WS_EX_TOOLWINDOW, not WS_EX_APPWINDOW
            if no_activate:
                style |= 0x08000000                      # WS_EX_NOACTIVATE
            ctypes.windll.user32.SetWindowLongW(hwnd, -20, style)

    def closing():
        if on_close:
            try:
                on_close()
            except Exception:
                pass
        return False   # cancel the actual close - helper windows are only ever hidden

    window.events.before_show += before_show
    window.events.closing += closing
    # pywebview force-shows transparent windows the moment they start loading (as normal windows
    # with taskbar buttons, for ~15 s of startup). Until this one has loaded, keep it hidden.
    _helpers[title] = {"loaded": False, "no_activate": no_activate}
    window.events.loaded += lambda: _helpers[title].update(loaded=True)
    _start_helper_guard()


def set_backdrop(window, rgb):
    """pywebview's transparent=True only makes the web page transparent - the window underneath
    keeps Windows' default grey-white background, which tints anything semi-transparent on the
    page. Painting that background `rgb` instead (pair it with a window region - see
    mini_window._shape - so nothing outside the drawn shape exists). Don't colour-KEY it out:
    a colour-keyed window lets every click fall through to whatever is behind it."""
    try:
        from System import Action
        from System.Drawing import Color
        form = window.native
        color = Color.FromArgb(255, *rgb)

        def apply():
            form.BackColor = color
            view = getattr(form, "webview", None)    # the WebView2 control has its own (white) background
            if view is not None:
                view.BackColor = color
        form.Invoke(Action(apply))
    except Exception as e:
        print(f"(couldn't set window backdrop: {e})")


_helpers = {}
_guard_started = threading.Event()


def _start_helper_guard():
    if _guard_started.is_set():
        return
    _guard_started.set()

    def guard():
        user32 = ctypes.windll.user32
        until = time.time() + 120
        while time.time() < until and not all(h["loaded"] for h in _helpers.values()):
            for title, h in list(_helpers.items()):
                if h["loaded"]:
                    continue
                hwnd = user32.FindWindowW(None, title)
                if hwnd and user32.IsWindowVisible(hwnd):
                    style = user32.GetWindowLongW(hwnd, -20)
                    style = (style | 0x80) & ~0x40000
                    if h["no_activate"]:
                        style |= 0x08000000
                    user32.SetWindowLongW(hwnd, -20, style)
                    user32.ShowWindow(hwnd, 0)       # hidden + tool window -> no taskbar button
            time.sleep(0.05)
    threading.Thread(target=guard, daemon=True, name="helper-window-guard").start()


def _phone(kind, **data):
    """Mirrors HUD events to AURA Mobile (phone_bridge), if it's running."""
    try:
        import phone_bridge
        phone_bridge.emit(kind, **data)
    except Exception:
        pass


def _mini(fn, *args, **kwargs):
    """Mirrors HUD updates into Mini AURA (if it exists)."""
    try:
        import mini_window
        getattr(mini_window, fn)(*args, **kwargs)
    except Exception:
        pass


def start_talking():
    """Tell the face to start its talking animation."""
    _mini("set_status", talking=True)
    _phone("status", talking=True)
    if _window and _ready.is_set():
        try:
            _window.evaluate_js("window.startTalking && window.startTalking()")
        except Exception:
            pass


def stop_talking():
    """Tell the face to stop its talking animation."""
    _mini("set_status", talking=False)
    _phone("status", talking=False)
    if _window and _ready.is_set():
        try:
            _window.evaluate_js("window.stopTalking && window.stopTalking()")
        except Exception:
            pass


# Lines said before the window has finished loading (e.g. "AURA is online")
# are held here and shown as soon as it's ready, so nothing goes missing.
_pending_log = []
_pending_lock = threading.Lock()


def _send_log_line(role, text):
    payload = json.dumps({"role": role, "text": text})
    try:
        _window.evaluate_js(f"window.addLogLine && window.addLogLine({payload})")
    except Exception:
        pass


def _flush_pending_log():
    with _pending_lock:
        lines = list(_pending_log)
        _pending_log.clear()
    for role, text in lines:
        _send_log_line(role, text)


def add_log(role, text):
    """Adds one line to the Conversation Log panel. role = "you" or "aura"."""
    text = (text or "").strip()
    if not text:
        return
    _mini("add_log", role, text)   # Mini AURA shows (and keeps) the same conversation
    try:
        import phone_bridge
        for_phone = phone_bridge.phone_origin()
    except Exception:
        for_phone = False
    _phone("log", role=role, text=text, for_phone=for_phone)
    if _window and _ready.is_set():
        _send_log_line(role, text)
    else:
        with _pending_lock:
            _pending_log.append((role, text))


def set_thinking(is_thinking):
    """Shows/hides the "AURA is thinking..." indicator in the log and HUD."""
    _mini("set_status", thinking=bool(is_thinking))
    _phone("status", thinking=bool(is_thinking))
    if _window and _ready.is_set():
        try:
            _window.evaluate_js(f"window.setThinking && window.setThinking({'true' if is_thinking else 'false'})")
        except Exception:
            pass

def set_listening(is_listening):
    """Ears up on the HUD cat while AURA waits for your command after the wake word."""
    _mini("set_status", listening=bool(is_listening))
    _phone("status", listening=bool(is_listening))
    if _window and _ready.is_set():
        try:
            _window.evaluate_js(f"window.setListening && window.setListening({'true' if is_listening else 'false'})")
        except Exception:
            pass


# ---- the cat that lives on the HUD (explain_popup.py walks it around the desktop)
def cat_screen_anchor():
    """Where the HUD cat is on the screen right now, as (x, y, w, h, scale) in
    PHYSICAL pixels plus the window's DPI scale - or None if the AURA window
    isn't showing (hidden to the tray / minimised)."""
    if not (_window and _ready.is_set()):
        return None
    try:
        hwnd = _window.native.Handle.ToInt32()
    except Exception:
        return None
    user32 = ctypes.windll.user32
    if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
        return None
    try:
        rect = _window.evaluate_js("window.catRect ? window.catRect() : null")
    except Exception:
        return None
    if not rect:
        return None
    origin = wintypes.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(origin))
    scale = (user32.GetDpiForWindow(hwnd) or 96) / 96
    return (origin.x + rect["x"] * scale, origin.y + rect["y"] * scale,
            rect["w"] * scale, rect["h"] * scale, scale)


def cat_leave():
    """The HUD cat hops out (the desktop walker takes over from the same spot)."""
    if _window and _ready.is_set():
        try:
            _window.evaluate_js("window.catLeave && window.catLeave()")
        except Exception:
            pass


def cat_return():
    if _window and _ready.is_set():
        try:
            _window.evaluate_js("window.catReturn && window.catReturn()")
        except Exception:
            pass


def show_toast(title, body="", kind="info"):
    """Pops a notification card on the HUD (reminders, focus timer...) - and on AURA Mobile."""
    _phone("toast", title=title, body=body, kind=kind)
    if _window and _ready.is_set():
        payload = json.dumps({"title": title, "body": body, "kind": kind})
        try:
            _window.evaluate_js(f"window.showToast && window.showToast({payload})")
        except Exception:
            pass


def summon():
    """Global hotkey: bring AURA to the front with the command box ready."""
    show_face()
    if _window and _ready.is_set():
        try:
            _window.restore()
        except Exception:
            pass
        try:
            _window.evaluate_js("window.focusInput && window.focusInput()")
        except Exception:
            pass
