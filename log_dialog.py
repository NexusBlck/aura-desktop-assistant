# log_dialog.py
# A themed popup for logging a session with a real calendar date picker,
# a time picker (with AM/PM), and a click-to-pick 1-10 rating - opens when
# you click a "Log X" button on the face HUD, instead of the voice Q&A.
import threading
import json
import webview
from life_data import log_session
from voice import speak

_window = None
_ready = threading.Event()
_session_type = "session"


class LogDialogAPI:
    def submit_log(self, iso_timestamp, rating):
        from life_data import get_session_type_summary
        log_session(_session_type, timestamp=iso_timestamp, rating=rating)
        hide_log_dialog()

        summary = get_session_type_summary(_session_type)
        reply = f"Logged. You've now logged {summary['count']} {_session_type} session(s)"
        reply += f", averaging {summary['avg_rating']} out of 10." if summary['avg_rating'] is not None else "."
        speak(reply)
        return None

    def cancel(self):
        hide_log_dialog()


def _on_loaded():
    _ready.set()


def create_log_dialog_window():
    """Must be called BEFORE webview.start(), same pattern as the other windows."""
    global _window
    _window = webview.create_window(
        "AURA - Log Session",
        "http://localhost:8734/log_dialog.html",
        width=380,
        height=540,
        resizable=False,
        frameless=True,
        on_top=True,
        background_color="#04070a",
        hidden=True,
        js_api=LogDialogAPI(),
    )
    _window.events.loaded += _on_loaded


def show_log_dialog(session_type):
    global _session_type
    _session_type = session_type
    if _window and _ready.is_set():
        try:
            _window.evaluate_js(f"window.setSessionType && window.setSessionType({json.dumps(session_type)})")
            _window.show()
        except Exception:
            pass


def hide_log_dialog():
    if _window:
        try:
            _window.hide()
        except Exception:
            pass