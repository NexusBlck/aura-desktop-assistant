# focus.py
# Focus sessions (Pomodoro-style): "start a 25 minute focus session". The HUD
# shows a big countdown; when it ends AURA tells you, logs the focus time to
# your life data (so it shows on the dashboard), and starts a short break.
import time
import threading

DEFAULT_FOCUS_MINUTES = 25
DEFAULT_BREAK_MINUTES = 5

_lock = threading.Lock()
_state = {"mode": "idle", "label": "", "total": 0, "ends_at": 0.0, "remaining": 0.0, "paused_mode": None}


def status():
    """{'mode': idle|focus|break|paused, 'label', 'total' secs, 'remaining' secs}."""
    with _lock:
        s = dict(_state)
    if s["mode"] in ("focus", "break"):
        s["remaining"] = max(0.0, s["ends_at"] - time.time())
    return {"mode": s["mode"], "label": s["label"], "total": s["total"],
            "remaining": round(s["remaining"]), "paused_mode": s["paused_mode"]}


def _set(mode, seconds, label):
    _state.update({"mode": mode, "label": label, "total": int(seconds),
                   "ends_at": time.time() + seconds, "remaining": seconds, "paused_mode": None})


def start(minutes=DEFAULT_FOCUS_MINUTES, label="Focus"):
    minutes = max(1, min(int(minutes or DEFAULT_FOCUS_MINUTES), 240))
    with _lock:
        _set("focus", minutes * 60, label or "Focus")
    return f"Focus session started - {minutes} minutes{f' on {label}' if label and label != 'Focus' else ''}. I'll let you know when it's time for a break."


def pause():
    with _lock:
        if _state["mode"] not in ("focus", "break"):
            return "There's no timer running."
        _state["remaining"] = max(0.0, _state["ends_at"] - time.time())
        _state["paused_mode"] = _state["mode"]
        _state["mode"] = "paused"
    return "Paused."


def resume():
    with _lock:
        if _state["mode"] != "paused":
            return "Nothing's paused."
        _state["mode"] = _state["paused_mode"] or "focus"
        _state["ends_at"] = time.time() + _state["remaining"]
        _state["paused_mode"] = None
    return "Resumed."


def stop():
    with _lock:
        was = _state["mode"]
        _state.update({"mode": "idle", "label": "", "total": 0, "ends_at": 0.0, "remaining": 0.0, "paused_mode": None})
    return "Timer stopped." if was != "idle" else "There's no timer running."


def handle_request(text):
    """Voice/typed: 'start a 50 minute focus session on anatomy', 'pause the
    timer', 'stop focus'."""
    import re
    lowered = (text or "").lower()
    if re.search(r"\b(stop|end|cancel|quit)\b", lowered):
        return stop()
    if re.search(r"\bpause\b", lowered):
        return pause()
    if re.search(r"\b(resume|continue|unpause)\b", lowered):
        return resume()
    if re.search(r"\bbreak\b", lowered) and not re.search(r"\bfocus|study|work\b", lowered):
        m = re.search(r"(\d+)\s*(?:min|minute)", lowered)
        with _lock:
            _set("break", int(m.group(1)) * 60 if m else DEFAULT_BREAK_MINUTES * 60, "Break")
        return "Break started - relax."
    minutes = _minutes_in(lowered) or DEFAULT_FOCUS_MINUTES
    # "...on anatomy" / "...for my exam" - but not "for an hour" (that's the length)
    label = re.search(r"\b(?:on|for)\s+(?!\d|an?\s+hour|half|\w+\s+minutes?)([a-z][\w\s-]{2,30})$", lowered)
    return start(minutes, label.group(1).strip().title() if label else "Focus")


def _minutes_in(text):
    """'50 minutes' -> 50, 'an hour' -> 60, 'half an hour' -> 30,
    'an hour and a half' / '1.5 hours' -> 90, '2 hours' -> 120."""
    import re
    if re.search(r"\bhalf an? hour\b", text):
        return 30
    if re.search(r"\b(an?|one) hour and a half\b|\bhour and a half\b", text):
        return 90
    h = re.search(r"(\d+(?:\.\d+)?)\s*(?:hours?|hrs?)\b", text)
    m = re.search(r"(\d+)\s*(?:minutes?|mins?|m)\b", text)
    total = (float(h.group(1)) * 60 if h else 0) + (int(m.group(1)) if m else 0)
    if not h and re.search(r"\b(an?|one) hour\b", text):
        total += 60
    return int(total) or None


def start_checker(on_event):
    """Background thread: when a focus block ends -> log it, tell the user,
    start the break; when the break ends -> tell the user."""
    def loop():
        while True:
            event = None
            with _lock:
                if _state["mode"] in ("focus", "break") and time.time() >= _state["ends_at"]:
                    event = (_state["mode"], _state["total"], _state["label"])
                    if _state["mode"] == "focus":
                        _set("break", DEFAULT_BREAK_MINUTES * 60, "Break")
                    else:
                        _state.update({"mode": "idle", "label": "", "total": 0, "ends_at": 0.0, "remaining": 0.0})
            if event:
                mode, total, label = event
                if mode == "focus":
                    try:
                        from life_data import log_session
                        log_session("focus", minutes=round(total / 60))
                    except Exception as e:
                        print(f"(couldn't log focus session: {e})")
                try:
                    on_event(mode, round(total / 60), label)
                except Exception as e:
                    print(f"(focus callback failed: {e})")
            time.sleep(1)

    threading.Thread(target=loop, daemon=True).start()
