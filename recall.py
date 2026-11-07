# recall.py - TOTAL RECALL: AURA remembers what was on your screen, so you can ask about anything you've seen.
# "What was I working on yesterday at 3?" · "Find that article about vaccines I read this morning" · "When did I last have
# the CDC spreadsheet open?" - and scrub back through your day like a film on the OVERWATCH timeline.
#
# How: while it's ON and you're at the PC, every few seconds AURA looks at the screen; when it changed (or you switched
# window) it keeps a small JPEG + the window title + the time. Questions are answered by AURA's AI reading the index and
# then LOOKING at the most relevant moments (a contact sheet of screenshots).
#
# Private by design: OFF until you turn it on, stays on this PC (Documents\AURA Recall), never captures windows whose title
# looks like passwords / banking / private browsing (plus your own exclude list), pauses while you're away or the PC is
# locked, deletes itself after `keep_days` and never grows past `max_gb`. "Pause recall" / "delete today's recall" work too.
import datetime
import io
import json
import os
import re
import shutil
import threading
import time

from ai import ask_llm, extract_json

AURA_DIR = os.path.dirname(os.path.abspath(__file__))
STORE = os.path.join(os.path.expanduser("~"), "Documents", "AURA Recall")
SETTINGS_FILE = os.path.join(AURA_DIR, "aura_recall.json")
DEFAULT_EXCLUDE = ["password", "passwort", "bitwarden", "1password", "keepass", "lastpass", "dashlane", "bank", "banking", "paypal",
                   "inprivate", "incognito", "private browsing", "credential", "sign in", "log in", "login", "otp", "2fa",
                   "verification code", "windows security", "aura recall"]

_lock = threading.RLock()
_settings = {"on": False, "interval": 8, "keep_days": 7, "max_gb": 3.0, "exclude": []}
_live = {"last_sig": None, "last_title": None, "last_save": 0.0, "count_today": 0, "paused_until": 0.0}


def _load():
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            _settings.update(json.load(f))
    except Exception:
        pass


def _save():
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(_settings, f)


_load()


def settings():
    with _lock:
        return dict(_settings)


def set_on(on):
    with _lock:
        _settings["on"] = bool(on)
        _save()
    return status()


def pause(minutes=30):
    _live["paused_until"] = time.time() + max(1, int(minutes)) * 60
    return status()


def resume():
    _live["paused_until"] = 0
    return status()


def set_exclude(words):
    with _lock:
        _settings["exclude"] = [str(w).strip().lower()[:60] for w in words if str(w).strip()][:50]
        _save()
    return status()


def _day_dir(d):
    return os.path.join(STORE, d.strftime("%Y-%m-%d"))


def status():
    size = 0
    days = []
    if os.path.isdir(STORE):
        for name in sorted(os.listdir(STORE)):
            p = os.path.join(STORE, name)
            if os.path.isdir(p) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", name):
                days.append(name)
                size += sum(os.path.getsize(os.path.join(p, f)) for f in os.listdir(p))
    paused = _live["paused_until"] > time.time()
    return {"on": _settings["on"], "paused": paused, "paused_until": _live["paused_until"] if paused else 0, "days": days,
            "size_mb": round(size / 1e6, 1), "keep_days": _settings["keep_days"], "interval": _settings["interval"],
            "exclude": _settings["exclude"], "today": _live["count_today"], "store": STORE}


# ------------------------------------------------------------------ capture
def _foreground():
    """(title, process name) of the window you're using."""
    import ctypes
    import ctypes.wintypes as wt
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    proc = ""
    try:
        import psutil
        proc = psutil.Process(pid.value).name()
    except Exception:
        pass
    return buf.value, proc


def excluded(title, proc=""):
    t = f"{title} {proc}".lower()
    return any(w in t for w in DEFAULT_EXCLUDE + _settings["exclude"])


def _signature(img):
    """A tiny grey thumbnail - two shots are 'the same screen' when these barely differ."""
    small = img.convert("L").resize((48, 27))
    return list(small.tobytes())


def _changed(a, b):
    if a is None or b is None:
        return True
    diff = sum(abs(x - y) for x, y in zip(a, b)) / len(a)
    return diff > 3.0


def capture_once(force=False):
    """Looks at the screen once; saves it if it's new. Returns the saved entry or None."""
    from PIL import ImageGrab
    title, proc = _foreground()
    if excluded(title, proc):
        return None
    img = ImageGrab.grab()
    sig = _signature(img)
    if not force and not _changed(sig, _live["last_sig"]) and title == _live["last_title"]:
        return None
    now = datetime.datetime.now()
    d = _day_dir(now)
    os.makedirs(d, exist_ok=True)
    W = 1280
    if img.width > W:
        img = img.resize((W, round(img.height * W / img.width)))
    name = now.strftime("%H%M%S") + ".jpg"
    img.convert("RGB").save(os.path.join(d, name), "JPEG", quality=58)
    entry = {"t": now.timestamp(), "file": name, "title": title[:300], "app": proc[:60]}
    with open(os.path.join(d, "index.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    _live.update(last_sig=sig, last_title=title, last_save=time.time())
    _live["count_today"] += 1
    return entry


def _cleanup():
    if not os.path.isdir(STORE):
        return
    cutoff = (datetime.date.today() - datetime.timedelta(days=int(_settings["keep_days"]))).strftime("%Y-%m-%d")
    days = sorted(n for n in os.listdir(STORE) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", n))
    for n in days:
        if n < cutoff:
            shutil.rmtree(os.path.join(STORE, n), ignore_errors=True)
    total = lambda: sum(os.path.getsize(os.path.join(r, f)) for r, _d, fs in os.walk(STORE) for f in fs)
    days = sorted(n for n in os.listdir(STORE) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", n))
    while days[:-1] and total() > _settings["max_gb"] * 1e9:
        shutil.rmtree(os.path.join(STORE, days.pop(0)), ignore_errors=True)


def delete(day=None):
    """Deletes one day ('YYYY-MM-DD' / 'today') or everything."""
    if day in (None, "all"):
        shutil.rmtree(STORE, ignore_errors=True)
    else:
        if day == "today":
            day = datetime.date.today().strftime("%Y-%m-%d")
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(day)):
            shutil.rmtree(os.path.join(STORE, day), ignore_errors=True)
    _live.update(last_sig=None, last_title=None, count_today=0 if day in (None, "all") or day == datetime.date.today().strftime("%Y-%m-%d") else _live["count_today"])
    return status()


def _loop():
    last_clean = 0
    while True:
        time.sleep(max(3, int(_settings.get("interval") or 8)))
        try:
            if not _settings["on"] or _live["paused_until"] > time.time():
                continue
            import aegis
            if aegis.is_locked() or aegis.idle_seconds() > 120 or aegis.status()["armed"]:
                continue                                   # you're not there: nothing to remember
            capture_once()
            if time.time() - last_clean > 3600:
                last_clean = time.time()
                _cleanup()
        except Exception as e:
            print(f"(recall: {e})")
            time.sleep(10)


def start():
    threading.Thread(target=_loop, daemon=True, name="recall").start()


# ------------------------------------------------------------------ reading it back
def entries(day):
    """Everything kept for one day ('YYYY-MM-DD'), oldest first."""
    if day == "today":
        day = datetime.date.today().strftime("%Y-%m-%d")
    path = os.path.join(STORE, str(day), "index.jsonl")
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    e = json.loads(line)
                    if os.path.isfile(os.path.join(STORE, day, e["file"])):
                        e["day"] = day
                        out.append(e)
                except Exception:
                    continue
    except FileNotFoundError:
        pass
    return out


def file_path(day, name):
    p = os.path.realpath(os.path.join(STORE, str(day), os.path.basename(str(name))))
    return p if p.startswith(os.path.realpath(STORE) + os.sep) and os.path.isfile(p) else None


def _plan(question):
    now = datetime.datetime.now()
    prompt = (f"Now is {now:%A %Y-%m-%d %H:%M}. The user asks their screen memory: \"{question}\"\n"
              "Work out the time window to look in and words likely to be in window titles. Respond ONLY with JSON: "
              '{"from": "YYYY-MM-DD HH:MM", "to": "YYYY-MM-DD HH:MM", "keywords": ["..."], "wants": "what|find|when|list"}. '
              "If no time is implied use the last 7 days. Keywords: 0-6 short words (app names, topics, site names).")
    try:
        p = extract_json(ask_llm([{"role": "user", "content": prompt}], temperature=0, reasoning_effort="low", max_tokens=400))
        f = datetime.datetime.strptime(p["from"], "%Y-%m-%d %H:%M")
        t = datetime.datetime.strptime(p["to"], "%Y-%m-%d %H:%M")
        return f, t, [str(k).lower() for k in p.get("keywords") or [] if str(k).strip()][:6], p.get("wants") or "what"
    except Exception:
        return now - datetime.timedelta(days=7), now, [w for w in re.findall(r"[a-z]{4,}", question.lower())][:5], "what"


def _sheet(picks):
    """Up to 6 moments as one labelled contact sheet for the vision AI."""
    from PIL import Image, ImageDraw
    cols, w, h = 3, 640, 380
    rows = (len(picks) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * w, rows * h), (12, 16, 22))
    d = ImageDraw.Draw(sheet)
    for i, e in enumerate(picks):
        try:
            im = Image.open(os.path.join(STORE, e["day"], e["file"])).convert("RGB")
            im.thumbnail((w, h - 28))
            x, y = (i % cols) * w, (i // cols) * h
            sheet.paste(im, (x, y + 28))
            d.rectangle([x, y, x + w, y + 27], fill=(255, 212, 0))
            d.text((x + 8, y + 7), f"#{i + 1}  {datetime.datetime.fromtimestamp(e['t']):%a %H:%M}  {e['title'][:60]}", fill=(0, 0, 0))
        except Exception:
            continue
    buf = io.BytesIO()
    sheet.save(buf, "JPEG", quality=80)
    import base64
    return base64.b64encode(buf.getvalue()).decode()


def search(question):
    """Answers a question about what was on screen. Returns {answer, moments:[entry...]}."""
    question = str(question or "").strip()[:400]
    f, t, words, wants = _plan(question)
    pool = []
    day = f.date()
    while day <= t.date():
        pool += [e for e in entries(day.strftime("%Y-%m-%d")) if f.timestamp() <= e["t"] <= t.timestamp()]
        day += datetime.timedelta(days=1)
    if not pool:
        return {"answer": "I don't have any screen memory for that time." + ("" if _settings["on"] else " Total Recall is off - turn it on and I'll start remembering."),
                "moments": []}
    scored = []
    for e in pool:
        title = f"{e['title']} {e['app']}".lower()
        s = sum(2 for w in words if w in title)
        scored.append((s, e))
    hits = [e for s, e in scored if s > 0] or pool
    hits.sort(key=lambda e: e["t"])
    # spread up to 6 picks across the matching stretch
    if len(hits) > 6:
        step = (len(hits) - 1) / 5
        picks = [hits[round(i * step)] for i in range(6)]
    else:
        picks = hits
    titles = "\n".join(f"{datetime.datetime.fromtimestamp(e['t']):%a %d %b %H:%M} - {e['title'][:120]} ({e['app']})" for e in hits[:80])
    import vision_lab
    prompt = (f"The user asks about their own screen history: \"{question}\".\nWindow titles in the matching time range (oldest first):\n{titles}\n\n"
              "The image is a contact sheet of screenshots #1-#{n} from those moments (time + title on each). Answer the question directly and "
              "specifically from what you can SEE and the titles (name the page / document / app, quote the key visible text, say when). "
              "Say which screenshot numbers matter. If it isn't there, say so. Respond ONLY with JSON: "
              '{"answer": "...", "moments": [numbers]}').replace("{n}", str(len(picks)))
    data = vision_lab._json(_sheet(picks), prompt, max_tokens=900)
    nums = [int(n) for n in (data.get("moments") or []) if str(n).isdigit() and 1 <= int(n) <= len(picks)] if isinstance(data, dict) else []
    chosen = [picks[n - 1] for n in nums] or picks[:3]
    return {"answer": str((data or {}).get("answer") or (data or {}).get("text") or "I looked but couldn't tell.")[:1200], "moments": chosen}
