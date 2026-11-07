# presence.py
# Is the user actually here, and do they want to be disturbed?
#   * Do Not Disturb (moon button in Mini AURA, or "do not disturb on/off"):
#     AURA stops speaking UNPROMPTED - email announcements and reminders become
#     silent pop-ups. Answers to things you ask still get spoken.
#   * Away: no keyboard/mouse input for AWAY_AFTER seconds. AURA doesn't announce
#     to an empty room; when you're back it tells you what you missed.
import ctypes
import datetime
import json
import os
import threading
import time
from ctypes import wintypes

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aura_presence.json")
AWAY_AFTER = 10 * 60

_state = {"dnd": False, "last_briefing": ""}
_missed = []                 # things that happened while away / on DND (spoken on return)
_listeners = []
_lock = threading.Lock()


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


def _load():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            _state.update(json.load(f))
    except Exception:
        pass


def _save():
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(_state, f)
    except Exception:
        pass


_load()


def idle_seconds():
    info = _LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(info)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return 0
    return (ctypes.windll.kernel32.GetTickCount() - info.dwTime) / 1000.0


def is_away():
    return idle_seconds() > AWAY_AFTER


def dnd():
    return bool(_state.get("dnd"))


def set_dnd(on):
    _state["dnd"] = bool(on)
    _save()
    for fn in _listeners:
        try:
            fn()
        except Exception:
            pass
    return dnd()


def proactive_ok():
    """May AURA speak up on its own right now?"""
    return not dnd() and not is_away()


def note_missed(text):
    with _lock:
        _missed.append(text)
        del _missed[:-12]


def on_change(fn):
    _listeners.append(fn)


def first_start_today():
    """True once per day (used for the morning briefing)."""
    today = datetime.date.today().isoformat()
    if _state.get("last_briefing") == today:
        return False
    _state["last_briefing"] = today
    _save()
    return True


def start_watch(speak):
    """When you come back after being away, AURA tells you what you missed."""
    def loop():
        was_away = False
        while True:
            away = is_away()
            if was_away and not away and not dnd():
                with _lock:
                    missed, _missed[:] = list(_missed), []
                if missed:
                    speak("Welcome back. While you were away: " + " ".join(missed))
            was_away = away
            time.sleep(5)
    threading.Thread(target=loop, daemon=True, name="presence").start()
