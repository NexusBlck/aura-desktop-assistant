# reminders.py
# Timed reminders: "remind me in 20 minutes to call Ali", "remind me at 6pm
# to go to BJJ". Saved to aura_reminders.json (so they survive a restart);
# a background checker fires each one on time - AURA says it out loud and
# the HUD shows it.
import os
import re
import json
import time
import uuid
import datetime
import threading
from zoneinfo import ZoneInfo
from ai import ask_json
from config import LOCAL_TIMEZONE

REMINDERS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aura_reminders.json")
_lock = threading.Lock()

PARSE_PROMPT = """Real current date/time: {now}

The user wants a reminder: "{request}"

Work out WHAT to remind them about and WHEN, resolving relative times ("in
20 minutes", "at 6", "tonight", "tomorrow morning") from the real current
date/time above. "At 6" with no am/pm means the next 6 o'clock that's still
ahead. Respond ONLY with JSON:
{{"text": "short reminder text, e.g. 'Call Ali'", "due": "YYYY-MM-DDTHH:MM"}}
If no time is given at all, use {{"text": "...", "due": null}}."""

_UNITS = {"s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
          "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
          "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600}
_RELATIVE = re.compile(r"\bin\s+(\d+(?:\.\d+)?|an?|half an?)\s*(s|secs?|seconds?|m|mins?|minutes?|h|hrs?|hours?)\b", re.I)


def _now():
    return datetime.datetime.now(ZoneInfo(LOCAL_TIMEZONE))


def _load():
    try:
        with open(REMINDERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def _save(reminders):
    try:
        with open(REMINDERS_FILE, "w", encoding="utf-8") as f:
            json.dump(reminders, f, ensure_ascii=False, indent=2)
    except OSError as e:
        print(f"(couldn't save reminders: {e})")


def _clean_text(text):
    text = re.sub(r"^\s*(please\s+)?(aura\s+)?(remind me|set a reminder|reminder)\s*(to|that|about)?\s*", "", text, flags=re.I)
    text = _RELATIVE.sub("", text)
    text = re.sub(r"^\s*(to|that|about)\s+", "", text.strip(" ,.-"), flags=re.I)
    return text.strip(" ,.-") or "Reminder"


def parse(request):
    """'remind me in 20 minutes to call Ali' -> ('Call Ali', due datetime).
    Simple "in N minutes/hours" is worked out instantly; anything else
    ("at 6pm", "tomorrow morning") is resolved by the AI against the real
    current time. Returns (text, due) or (text, None) if no time was given."""
    m = _RELATIVE.search(request)
    if m:
        amount = m.group(1).lower()
        amount = 0.5 if amount.startswith("half") else 1 if amount in ("a", "an") else float(amount)
        seconds = amount * _UNITS[m.group(2).lower()]
        text = _clean_text(request)
        return text[:1].upper() + text[1:], _now() + datetime.timedelta(seconds=seconds)

    data = ask_json([{"role": "user", "content": PARSE_PROMPT.format(
        now=_now().strftime("%A %Y-%m-%d %H:%M"), request=request)}], default={}, reasoning_effort="low")
    text = (data.get("text") if isinstance(data, dict) else None) or _clean_text(request)
    due = None
    if isinstance(data, dict) and data.get("due"):
        try:
            due = datetime.datetime.fromisoformat(data["due"]).replace(tzinfo=ZoneInfo(LOCAL_TIMEZONE))
        except ValueError:
            due = None
    return text, due


def add(text, due):
    reminder = {"id": uuid.uuid4().hex[:8], "text": text, "due": due.isoformat(), "done": False,
                "created": _now().isoformat()}
    with _lock:
        reminders = _load()
        reminders.append(reminder)
        _save(reminders)
    return reminder


def _describe_due(due):
    now = _now()
    delta = due - now
    minutes = max(int(delta.total_seconds() // 60), 0)
    if minutes < 1:
        rel = "in less than a minute"
    elif minutes < 60:
        rel = f"in {minutes} minute{'s' if minutes != 1 else ''}"
    else:
        h, m = divmod(minutes, 60)
        rel = f"in {h} hour{'s' if h != 1 else ''}" + (f" {m} min" if m else "")
    day = "" if due.date() == now.date() else (" tomorrow" if due.date() == (now + datetime.timedelta(days=1)).date()
                                               else f" on {due.strftime('%A %d %B')}")
    return f"at {due.strftime('%I:%M %p').lstrip('0')}{day} ({rel})"


def add_from_text(request):
    """Parses and saves a reminder. Returns a spoken-friendly reply."""
    text, due = parse(request)
    if due is None:
        return "When should I remind you? Say something like 'in 20 minutes' or 'at 6pm'."
    if due <= _now():
        return "That time has already passed - tell me a time that's still ahead."
    add(text, due)
    return f"Okay - I'll remind you to {text[:1].lower() + text[1:]} {_describe_due(due)}."


def pending():
    with _lock:
        items = [r for r in _load() if not r.get("done")]
    return sorted(items, key=lambda r: r["due"])


def cancel(reminder_id):
    with _lock:
        reminders = _load()
        before = len(reminders)
        reminders = [r for r in reminders if r["id"] != reminder_id]
        _save(reminders)
    return len(reminders) < before


def start_checker(on_due):
    """Background thread: fires on_due(reminder) when each reminder is due.
    Reminders missed while AURA was closed fire as soon as it starts."""
    def loop():
        while True:
            now = _now()
            fired = []
            with _lock:
                reminders = _load()
                for r in reminders:
                    if not r.get("done") and datetime.datetime.fromisoformat(r["due"]) <= now:
                        r["done"] = True
                        fired.append(r)
                if fired:
                    # keep the file tidy: drop reminders finished more than a day ago
                    cutoff = now - datetime.timedelta(days=1)
                    reminders = [r for r in reminders
                                 if not r.get("done") or datetime.datetime.fromisoformat(r["due"]) > cutoff]
                    _save(reminders)
            for r in fired:
                try:
                    on_due(r)
                except Exception as e:
                    print(f"(reminder callback failed: {e})")
            time.sleep(2)

    threading.Thread(target=loop, daemon=True).start()
