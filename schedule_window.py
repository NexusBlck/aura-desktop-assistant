# schedule_window.py
# A small HUD-style window showing a condensed visual summary of your
# schedule/tasks/weather whenever AURA gives a briefing. AURA still SAYS the
# full thing out loud - this window is just the minimalist visual companion.
import os
import json
import threading
import http.server
import socketserver
import webview

GUI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schedule_gui")
PORT = 8735

_window = None
_ready = threading.Event()


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=GUI_DIR, **kwargs)

    def log_message(self, format, *args):
        pass


def _start_local_server():
    with socketserver.TCPServer(("localhost", PORT), _QuietHandler) as httpd:
        httpd.serve_forever()


def _on_loaded():
    _ready.set()


def create_window():
    """Creates (but doesn't show) the schedule HUD window. Must be called
    BEFORE webview.start() - pywebview runs all created windows together in
    one event loop on the main thread. Stays hidden until the first real
    briefing (update_schedule) actually happens."""
    global _window
    threading.Thread(target=_start_local_server, daemon=True).start()
    _window = webview.create_window(
        "AURA - Schedule",
        f"http://localhost:{PORT}/schedule.html",
        width=420,
        height=580,
        resizable=True,
        background_color="#05070a",
        hidden=True,
    )
    _window.events.loaded += _on_loaded


def show_schedule():
    if _window:
        try:
            _window.show()
        except Exception:
            pass


def update_schedule(name, schedule_lines, weather_summary, task_lines, time_label):
    """Pushes fresh data into the HUD and reveals it if this is the first
    briefing. Safe to call even if the window isn't ready yet."""
    try:
        import phone_bridge
        phone_bridge.set_briefing({"name": name, "schedule": schedule_lines, "weather": weather_summary,
                                   "tasks": task_lines, "time_label": time_label})
    except Exception:
        pass
    try:
        import phone_bridge
        if phone_bridge.phone_target():          # asked on the phone: the briefing card shows there
            return
    except Exception:
        pass
    if not (_window and _ready.is_set()):
        return
    payload = json.dumps({
        "name": name,
        "schedule": schedule_lines,
        "weather": weather_summary,
        "tasks": task_lines,
        "time_label": time_label,
    })
    try:
        _window.evaluate_js(f"window.updateBriefing && window.updateBriefing({payload})")
        _window.show()
    except Exception:
        pass