# dashboard_window.py
# The Life Dashboard window - shows real trends from data AURA already
# tracks (sessions, tasks, papers, journal check-ins). Stays hidden until
# you actually ask for it.
import os
import json
import threading
import http.server
import socketserver
import webview
import psutil
from life_data import get_weekly_summary, get_trend_data

GUI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard_gui")
PORT = 8736

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
    try:
        _window.maximize()
    except Exception:
        pass


class DashboardAPI:
    def get_dashboard_data(self):
        try:
            cpu = psutil.cpu_percent(interval=0.2)
            ram = psutil.virtual_memory().percent
        except Exception:
            cpu, ram = None, None

        return json.dumps({
            "summary": get_weekly_summary(),
            "trend": get_trend_data(),
            "vitals": {"cpu": cpu, "ram": ram},
        })

    def get_full_profile_data(self):
        from memory_store import load_store
        from tasks import load_tasks
        from paper_library import load_library
        from life_data import load_data as load_life_data

        mem = load_store()
        tasks = load_tasks()
        papers = load_library()
        life = load_life_data()

        type_totals = {}
        for s in life["sessions"]:
            type_totals[s["type"]] = type_totals.get(s["type"], 0) + 1

        sessions_sorted = sorted(life["sessions"], key=lambda s: s["timestamp"], reverse=True)

        return json.dumps({
            "facts": mem.get("facts", []),
            "projects": mem.get("projects", []),
            "preferences": mem.get("preferences", []),
            "tasks": tasks,
            "papers": papers,
            "sessions": sessions_sorted,
            "session_type_totals_alltime": type_totals,
        })


def create_window():
    """Must be called BEFORE webview.start() - same pattern as the other
    windows, all managed together in one event loop on the main thread."""
    global _window
    threading.Thread(target=_start_local_server, daemon=True).start()
    _window = webview.create_window(
        "AURA - Life Dashboard",
        f"http://localhost:{PORT}/dashboard.html",
        width=1280,
        height=800,
        resizable=True,
        background_color="#05070a",
        hidden=True,
        js_api=DashboardAPI(),
    )
    _window.events.loaded += _on_loaded


def show_dashboard():
    try:
        import phone_bridge
        if phone_bridge.phone_target():          # asked on the phone -> the phone's dashboard
            phone_bridge.emit("do", action="tool", tool="dashboard")
            return
    except Exception:
        pass
    if _window:
        try:
            _window.show()
            _window.maximize()
            _window.evaluate_js("window.refreshDashboard && window.refreshDashboard()")
        except Exception:
            pass