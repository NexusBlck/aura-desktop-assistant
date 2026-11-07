# phone_bridge.py
# AURA Mobile's connection to the PC (see mobile/ARCHITECTURE.md).
#
# A small HTTP server on 127.0.0.1 (reached from the phone through Tailscale Serve, which adds
# HTTPS) that:
#   * serves the phone app (the mobile/ folder),
#   * pairs phones (a code shown on the PC -> a long random device token, stored hashed),
#   * feeds phone messages into the SAME command queue as voice / the HUD / Mini AURA,
#   * streams what AURA says and does back to the phone (Server-Sent Events),
#   * exposes the HUD's data and controls (tasks, reminders, notes, focus, emails, dashboard...),
#   * runs phone-only tools (voice upload -> Whisper, AURA's voice as audio, photo -> vision,
#     explain text, CDC WONDER jobs, file views/downloads/uploads) and Web Push notifications.
# Every /api/ request except pairing needs "Authorization: Bearer <device token>".
import base64
import collections
import datetime
import hashlib
import hmac
import http.server
import json
import mimetypes
import os
import re
import secrets
import socketserver
import threading
import time
import urllib.parse

import config

AURA_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.join(AURA_DIR, "mobile")
DEVICES_FILE = os.path.join(AURA_DIR, "aura_phone_devices.json")
PORT = getattr(config, "PHONE_BRIDGE_PORT", 8740)
UPLOAD_DIR = os.path.join(os.path.expanduser("~"), "Documents", "AURA Uploads")
MAX_BODY = 25 * 1024 * 1024          # voice clips, photos, PDFs

_queue = None
_lock = threading.RLock()


# ------------------------------------------------------------------ devices & pairing
def _load_devices():
    try:
        with open(DEVICES_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}
    data.setdefault("devices", [])
    return data


def _save_devices(data):
    tmp = DEVICES_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, DEVICES_FILE)


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


_pairing = {"code": None, "expires": 0, "tries": 0}


def new_pairing_code(show=True):
    """A 6-digit code, valid 5 minutes, 5 tries. Shown on the PC - typing it on the phone
    proves the person pairing is at this PC."""
    with _lock:
        _pairing.update(code=f"{secrets.randbelow(10**6):06d}", expires=time.time() + 300, tries=0)
        code = _pairing["code"]
    if show:
        _show_code(code)
    return code


def _show_code(code):
    pretty = f"{code[:3]} {code[3:]}"
    print(f"(AURA Mobile pairing code: {pretty} - valid for 5 minutes)")
    try:
        import face_window
        face_window.show_toast("Pair your phone", f"Code: {pretty}  (valid 5 min)", "info")
    except Exception:
        pass

    def box():
        import ctypes

        def to_front():
            from ctypes import wintypes
            u = ctypes.windll.user32
            u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
            for _ in range(20):
                time.sleep(0.15)
                hwnd = u.FindWindowW(None, "AURA - pair a phone")
                if hwnd:
                    u.ShowWindow(hwnd, 9)
                    u.SetWindowPos(hwnd, wintypes.HWND(-1), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0040)
                    u.SetForegroundWindow(hwnd)
                    u.FlashWindow(hwnd, True)
                    return
        threading.Thread(target=to_front, daemon=True).start()
        ctypes.windll.user32.MessageBoxW(
            0, f"Type this code into AURA on your phone:\n\n        {pretty}\n\nIt works for 5 minutes. "
               "If you didn't just try to pair a phone, ignore this.", "AURA - pair a phone", 0x40 | 0x1000 | 0x40000 | 0x10000)
    threading.Thread(target=box, daemon=True).start()


def _pair(code, name):
    code = re.sub(r"\D", "", str(code or ""))
    with _lock:
        ok = (_pairing["code"] and time.time() < _pairing["expires"] and _pairing["tries"] < 5
              and hmac.compare_digest(code, _pairing["code"]))
        if not ok:
            _pairing["tries"] += 1
            if _pairing["tries"] >= 5:
                _pairing["code"] = None
            return None
        _pairing["code"] = None
        token = secrets.token_urlsafe(32)
        device = {"id": secrets.token_hex(8), "name": (str(name or "Phone").strip() or "Phone")[:40],
                  "token_hash": _hash(token), "created": time.time(), "last_seen": time.time()}
        data = _load_devices()
        data["devices"].append(device)
        _save_devices(data)
    print(f"(AURA Mobile: paired '{device['name']}')")
    return token, device


def _device_for(token):
    if not token:
        return None
    h = _hash(token)
    with _lock:
        data = _load_devices()
        for d in data["devices"]:
            if hmac.compare_digest(d["token_hash"], h):
                if time.time() - d.get("last_seen", 0) > 60:
                    d["last_seen"] = time.time()
                    _save_devices(data)
                return d
    return None


def _revoke(device_id):
    with _lock:
        data = _load_devices()
        before = len(data["devices"])
        data["devices"] = [d for d in data["devices"] if d["id"] != device_id]
        data.get("push", {}).pop(device_id, None)
        _save_devices(data)
        return len(data["devices"]) < before


# ------------------------------------------------------------------ live events (SSE)
_events = collections.deque(maxlen=500)
_seq = 0
_cond = threading.Condition()
_status = {"thinking": False, "talking": False, "listening": False, "asking": False}
PC_KEY = secrets.token_urlsafe(24)          # this run's key for AURA's own PC windows (spatial_window.py) - never leaves the PC
PC_DEVICE = {"id": "pc-window", "name": "This PC", "pc": True}
_connected = collections.Counter()      # device id -> open event streams (≈ app is open)


def emit(kind, **data):
    """Called by face_window / voice / main for everything the phone should see live."""
    global _seq
    if kind == "status":
        _status.update({k: bool(v) for k, v in data.items() if k in _status})
        data = dict(_status)
    # Several devices (iPhone, iPad...): AURA's spoken answer and anything it opens go to the one that ASKED.
    if kind in ("log", "do") and "device" not in data and getattr(_local, "device", None) and getattr(_local, "phone", False):
        data["device"] = _local.device
    with _cond:
        _seq += 1
        _events.append((_seq, json.dumps({"type": kind, "seq": _seq, "t": time.time(), **data})))
        _cond.notify_all()
    if kind == "log" and data.get("role") == "aura" and data.get("device") in _watch_waiters:
        _watch_waiters[data["device"]].append(str(data.get("text") or ""))
    if kind == "toast":
        _push_all(data.get("title", "AURA"), data.get("body", ""), data.get("kind", "info"))
    elif kind == "log" and data.get("role") == "aura" and data.get("for_phone"):
        _push_all("AURA", data.get("text", ""), "reply", only_if_closed=True, only_device=data.get("device"))


def _events_after(last):
    with _cond:
        return [(s, e) for s, e in _events if s > last]


# ------------------------------------------------------------------ who asked: phone or PC?
class PhoneText(str):
    """A command typed/spoken on the phone. Still a plain str for everything that reads the
    command queue - the extra attributes only tell AURA to answer on the phone."""
    origin = "phone"
    device = None


_local = threading.local()


def begin_command(text):
    """Marks who asked (this thread). From the phone, AURA acts ON THE PHONE unless the message says
    'on my PC' - returns the text with that phrase removed."""
    _local.phone = isinstance(text, PhoneText)
    _local.target = None
    _local.device = getattr(text, "device", None) if _local.phone else None
    if _local.phone:
        import phone_actions
        stripped, on_pc = phone_actions.split_target(str(text))
        _local.target = "pc" if on_pc or getattr(text, "force_pc", False) else "phone"
        if stripped != str(text):
            device, force_pc = text.device, getattr(text, "force_pc", False)
            text = PhoneText(stripped)
            text.device, text.force_pc = device, force_pc
    return text


def end_command():
    _local.phone = False
    _local.target = None
    _local.device = None


def phone_origin():
    """True while AURA is handling a command that came from the phone (that thread only)."""
    return getattr(_local, "phone", False)


def carry(fn):
    """Wraps fn so a helper thread started while handling a phone request still knows it came from
    the phone (answers and anything opened go to the phone)."""
    phone, target, device = getattr(_local, "phone", False), getattr(_local, "target", None), getattr(_local, "device", None)

    def run(*a, **k):
        _local.phone, _local.target, _local.device = phone, target, device
        return fn(*a, **k)
    return run


def current_device():
    """The paired device (phone / watch) behind the command this thread is handling, or None."""
    return getattr(_local, "device", None)


def phone_target():
    """True while AURA is handling a phone request that should happen ON THE PHONE (no 'on my PC')."""
    return getattr(_local, "target", None) == "phone"


# AURA asking a question back (journal, log a session, "say yes to shut down"):
# voice.listen() calls wait_reply() instead of using the PC microphone.
_reply_q = collections.deque()
_reply_cond = threading.Condition()


def wait_reply(timeout=None):
    deadline = time.time() + max(30, (timeout or 0) * 6)   # typing on a phone takes longer than talking
    with _reply_cond:
        _reply_q.clear()
    emit("status", asking=True)
    try:
        with _reply_cond:
            while not _reply_q:
                left = deadline - time.time()
                if left <= 0:
                    return None
                _reply_cond.wait(left)
            return _reply_q.popleft()
    finally:
        emit("status", asking=False)


def _submit(text, device, force_pc=False):
    text = (text or "").strip()[:4000]
    if not text:
        return "empty"
    if _status["asking"]:
        with _reply_cond:
            _reply_q.append(text)
            _reply_cond.notify_all()
        try:
            import face_window
            face_window.add_log("you", text)
        except Exception:
            pass
        return "answered"
    try:
        from voice import speaking_event, interrupt_event
        if speaking_event.is_set():
            interrupt_event.set()
    except Exception:
        pass
    cmd = PhoneText(text)
    cmd.device = device["id"]
    cmd.force_pc = force_pc
    _queue.put(cmd)
    return "queued"


# ------------------------------------------------------------------ Apple Watch ("Ask AURA" shortcut)
# The watch can't run the app or Tailscale: an iOS Shortcut on the watch sends what you said to /api/watch/ask
# (through Tailscale Funnel) and gets AURA's answer back in the SAME request, to show and speak on the wrist.
_watch_waiters = {}            # device id -> AURA's lines said for that request
WATCH_FILLER = re.compile(r"^(let me think that through( properly)?|one moment|on it)[.!]*$", re.I)


def watch_ask(text, device, timeout=26):
    replies = _watch_waiters[device["id"]] = []
    status = _submit(text, device, force_pc=True)
    if status == "empty":
        _watch_waiters.pop(device["id"], None)
        return {"reply": "I didn't catch anything."}
    t0, quiet_since = time.time(), None
    try:
        while time.time() - t0 < timeout:
            time.sleep(0.25)
            real = [r for r in replies if not WATCH_FILLER.match(r.strip())]
            if real and (not _status["thinking"] or _status["asking"]):
                quiet_since = quiet_since or time.time()
                if time.time() - quiet_since > 1.2:          # AURA finished talking
                    break
            else:
                quiet_since = None
    finally:
        _watch_waiters.pop(device["id"], None)
    real = [r for r in replies if not WATCH_FILLER.match(r.strip())]
    if not real:
        return {"reply": "Still working on that - the answer will show on your iPhone.", "pending": True}
    return {"reply": " ".join(real)[:1500], "asking": bool(_status["asking"])}


def make_watch_key(from_device):
    """A separate key for the Apple Watch shortcut (revocable in Settings like any device)."""
    token = secrets.token_urlsafe(32)
    with _lock:
        data = _load_devices()
        device = {"id": secrets.token_hex(8), "name": "Apple Watch", "token_hash": _hash(token), "created": time.time(),
                  "last_seen": time.time(), "watch": True, "made_by": from_device["id"]}
        data["devices"].append(device)
        _save_devices(data)
    return token, device


# ------------------------------------------------------------------ data the phone shows
def _chat(limit=80):
    try:
        import mini_window
        with mini_window._chat_lock:
            chat = list(mini_window._chat)
        if chat:
            return chat[-limit:]
        with open(mini_window.CHAT_FILE, encoding="utf-8") as f:
            return json.load(f)[-limit:]
    except Exception:
        return []


_last_briefing = {}


def set_briefing(payload):
    _last_briefing.clear()
    _last_briefing.update(payload, at=time.time())
    emit("briefing", **_last_briefing)


def _state():
    import face_window
    import presence
    data = json.loads(face_window.FaceAPI(None).get_face_data())
    try:
        import gmail_watch
        emails = gmail_watch.pending_list() if gmail_watch.connected() else []
    except Exception:
        emails = []
    try:
        import mini_window
        chips = mini_window._info_line()
    except Exception:
        chips = []
    data.update(chat=_chat(), dnd=presence.dnd(), emails=emails, chips=chips, status=dict(_status),
                briefing=dict(_last_briefing), seq=_seq, name=_user_name(), pc=os.environ.get("COMPUTERNAME", "PC"))
    return data


def _user_name():
    try:
        from memory_store import load_store
        for fact in load_store()["facts"]:
            m = re.search(r"name is (\w+)", fact, re.I)
            if m:
                return m.group(1)
    except Exception:
        pass
    return ""


def _dashboard():
    import dashboard_window
    api = dashboard_window.DashboardAPI()
    return {**json.loads(api.get_dashboard_data()), "profile": json.loads(api.get_full_profile_data())}


# ------------------------------------------------------------------ files: signed view links
# Briefing pages, screenshots, search results and CDC outputs are opened on the phone through
# short-lived links (an <iframe>/download can't send the auth header). A link only works for
# 15 minutes and only inside the one folder it was made for.
_tickets = {}


def _roots():
    return {
        "briefings": os.path.join(AURA_DIR, "face", "briefings"),
        "screenshots": os.path.join(os.path.expanduser("~"), "Pictures", "AURA_Screenshots"),
        "search": os.path.join(AURA_DIR, "search_results"),
        "cdc": os.path.join(os.path.expanduser("~"), "Documents", "AURA CDC WONDER"),
        "uploads": UPLOAD_DIR,
        "vision": os.path.join(os.path.expanduser("~"), "Documents", "AURA Vision"),
        "echo": os.path.join(os.path.expanduser("~"), "Documents", "AURA Echo"),
        "aura": AURA_DIR,      # only for the few single files listed in _AURA_FILES
    }


_AURA_FILES = {"prisma_flow.html"}


def _inside(root, path):
    root, path = os.path.realpath(root), os.path.realpath(path)
    return path == root or path.startswith(root + os.sep)


def _resolve(kind, rel):
    root = _roots().get(kind)
    if not root:
        return None
    path = os.path.join(root, rel or "")
    if kind == "aura" and os.path.basename(path) not in _AURA_FILES:
        return None
    if kind == "search":   # results are shortcuts (.lnk) - resolve to the real file (any folder)
        target = _lnk_target(path) if path.lower().endswith(".lnk") else None
        if target:
            return target if os.path.exists(target) else None
    return path if _inside(root, path) and os.path.exists(path) else None


def _lnk_target(path):
    try:
        import comtypes.client
        return comtypes.client.CreateObject("WScript.Shell", dynamic=True).CreateShortcut(path).TargetPath
    except Exception:
        return None


def ticket_for_path(path):
    """A 15-minute link to exactly one file on the PC (a file AURA found or made for a phone request)."""
    if not path or not os.path.isfile(path):
        return None
    t = secrets.token_urlsafe(18)
    with _lock:
        _tickets[t] = {"base": os.path.dirname(os.path.realpath(path)), "only": os.path.basename(path), "exp": time.time() + 900}
    return f"/v/{t}/{urllib.parse.quote(os.path.basename(path))}"


def _ticket(kind, rel):
    path = _resolve(kind, rel)
    if not path:
        return None
    t = secrets.token_urlsafe(18)
    base = path if os.path.isdir(path) else os.path.dirname(path)
    # A briefing page needs its pictures folder next to it; anything else unlocks just that one file.
    only = None if (kind == "briefings" or os.path.isdir(path)) else os.path.basename(path)
    with _lock:
        now = time.time()
        for k in [k for k, v in _tickets.items() if v["exp"] < now]:
            _tickets.pop(k, None)
        _tickets[t] = {"base": base, "only": only, "exp": now + 900}
    name = "" if os.path.isdir(path) else urllib.parse.quote(os.path.basename(path))
    return f"/v/{t}/{name}"


def _list_files(kind, rel=""):
    folder = _resolve(kind, rel)
    if not folder or not os.path.isdir(folder):
        return []
    out = []
    for name in os.listdir(folder):
        p = os.path.join(folder, name)
        if name.startswith(".") or name.endswith("_pictures"):
            continue
        real = _lnk_target(p) if name.lower().endswith(".lnk") else p
        try:
            st = os.stat(real or p)
        except OSError:
            continue
        out.append({"name": name[:-4] if name.lower().endswith(".lnk") else name,
                    "path": os.path.join(rel, name) if rel else name, "dir": os.path.isdir(real or p),
                    "size": st.st_size, "time": st.st_mtime})
    out.sort(key=lambda f: f["time"], reverse=True)
    return out[:200]


# ------------------------------------------------------------------ tools the phone uses
_explanations = {}


def _explain(text):
    from explainer import Explanation
    ex = Explanation(text[:4000], app_title="AURA Mobile")
    ex.gather()
    answer = ex.explain()
    eid = secrets.token_hex(6)
    with _lock:
        _explanations[eid] = ex
        while len(_explanations) > 20:
            _explanations.pop(next(iter(_explanations)))
    return {"id": eid, **(answer if isinstance(answer, dict) else {"text": str(answer)})}


def _follow_up(eid, question):
    ex = _explanations.get(eid)
    if not ex:
        return {"error": "That explanation has expired - explain it again."}
    answer = ex.follow_up(question[:1000])
    return answer if isinstance(answer, dict) else {"text": str(answer)}


def _vision(body, op, hint=""):
    import numpy as np
    import cv2
    import object_vision
    frame = cv2.imdecode(np.frombuffer(body, np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        return {"error": "That photo couldn't be read."}
    if op == "scan":
        return object_vision.deep_scan(frame=frame)
    return object_vision.identify({"x": 0, "y": 0, "w": 1, "h": 1}, hint, frame=frame, margin=0)


def _vision_lab(op, b):
    """AURA Mobile's Vision modes (vision_lab.py). Photos arrive as base64 JPEG in the JSON body."""
    import vision_lab
    img = str(b.get("image") or "")
    if op in ("ask", "read", "study", "save") and not img:
        return {"error": "No photo arrived."}
    if op == "ask":
        return vision_lab.ask(img, str(b.get("question") or ""))
    if op == "read":
        return vision_lab.read(img, str(b.get("mode") or "read"), str(b.get("lang") or "English")[:30])
    if op == "study":
        return vision_lab.study(img, str(b.get("mode") or "explain"))
    if op == "save":
        return vision_lab.save_capture(img, str(b.get("title") or "capture"), str(b.get("text") or "")[:20000])
    if op == "memory/add":
        return vision_lab.memory_add(b.get("name"), b.get("note"), str(b.get("thumb") or ""), b.get("embeddings") or [], str(b.get("model") or ""))
    if op == "memory/views":
        return vision_lab.memory_views(b.get("id"), b.get("embeddings") or [])
    if op == "memory/seen":
        return {"ok": vision_lab.memory_seen(b.get("id"), str(b.get("scene") or ""))}
    if op == "observe":
        r = vision_lab.observe(img, str(b.get("focus") or "")[:200]) if img else {"error": "No photo arrived."}
        if isinstance(r, dict) and r.get("summary") and b.get("log"):
            vision_lab.timeline_add(r["summary"], str(b.get("thumb") or ""), "observe")
        return r
    if op == "detective":
        return vision_lab.detective(img, str(b.get("question") or "")[:300]) if img else {"error": "No photo arrived."}
    if op == "watch":
        r = vision_lab.watch(img, b.get("conditions") or []) if img else {"error": "No photo arrived."}
        if isinstance(r, dict) and r.get("triggered"):
            vision_lab.timeline_add("ALERT: " + "; ".join(t.get("condition", "") for t in r["triggered"]), str(b.get("thumb") or ""), "alert")
            try:
                phone_bridge_alert = "; ".join(f"{t.get('condition')}: {t.get('evidence')}" for t in r["triggered"])
                _push_all("SENTINEL ALERT", phone_bridge_alert, "reminder")      # also reaches you if the app is in the background
            except Exception:
                pass
        return r
    if op == "med":
        return vision_lab.med(img, str(b.get("kind") or "drug")) if img else {"error": "No photo arrived."}
    if op == "compare":
        a, c = str(b.get("a") or ""), str(b.get("b") or "")
        return vision_lab.compare(a, c, str(b.get("question") or "")[:300]) if a and c else {"error": "Two photos are needed."}
    if op == "doc":
        return vision_lab.scan_doc([str(x) for x in (b.get("pages") or [])], str(b.get("title") or "Scanned document"))
    if op == "timeline":
        return {"items": vision_lab.timeline_list(int(b.get("limit") or 80))}
    if op == "recall":
        return vision_lab.recall(str(b.get("question") or "")[:300])
    if op == "memory/delete":
        return {"ok": vision_lab.memory_delete(b.get("id"))}
    if op == "memory/rename":
        return vision_lab.memory_rename(b.get("id"), b.get("name"), b.get("note"))
    return {"error": "unknown"}


def _nexus(method, op, query, b, device):
    """AURA Mobile's NEXUS modules (nexus.py). Returns a dict, or (bytes, content type, headers) for the screen."""
    import nexus
    if op in ("screen", "input", "clipboard") and not nexus.remote_allowed():
        return {"error": "Remote control is switched off on the PC (PHONE_REMOTE_CONTROL in config.py)."}
    if method == "GET":
        if op == "screen":
            jpg, w, h = nexus.screen_jpeg(query.get("w", ["900"])[0], query.get("q", ["55"])[0])
            return jpg, "image/jpeg", {"X-Screen-Size": f"{w}x{h}"}
        if op == "clipboard":
            return nexus.clipboard({})
        if op == "echo":
            eid = query.get("id", [""])[0]
            return nexus.echo_get(eid) if eid else {"items": nexus.echo_list()}
        if op == "bio":
            return {"items": nexus.bio_list()[-300:]}
        if op == "protocols":
            return {"items": nexus.protocols_list(), "running": nexus._running["id"]}
        return {"error": "not found"}
    if op == "live":
        return nexus.live_turn(b)
    if op == "babel":
        return nexus.interpret(b)
    if op == "echo/start":
        return nexus.echo_start(b)
    if op == "echo/chunk":
        return nexus.echo_chunk(b)
    if op == "echo/finish":
        return nexus.echo_finish(b)
    if op == "echo/ask":
        return nexus.echo_ask(b)
    if op == "echo/delete":
        return {"ok": nexus.echo_delete(b.get("id"))}
    if op == "places":
        return nexus.places(b)
    if op == "where":
        return nexus.where(b)
    if op == "around":
        return nexus.around_ask(b)
    if op == "bio":
        return nexus.bio_save(b)
    if op == "input":
        events = b.get("events") if isinstance(b.get("events"), list) else [b]
        res = {"ok": True}
        for ev in events[:40]:
            res = nexus.remote_input(ev if isinstance(ev, dict) else {})
        return res
    if op == "clipboard":
        return nexus.clipboard(b)
    if op == "protocols/save":
        return nexus.protocol_save(b)
    if op == "protocols/delete":
        return nexus.protocol_delete(b.get("id"))
    if op == "protocols/run":
        if b.get("steps"):
            return nexus.protocol_run(str(b.get("name") or "Mission")[:60], [str(x)[:300] for x in b["steps"]], device["id"])
        p = next((x for x in nexus.protocols_list() if x["id"] == b.get("id")), None)
        return nexus.protocol_run(p["name"], p["steps"], device["id"]) if p else {"error": "That protocol wasn't found."}
    if op == "protocols/stop":
        return nexus.protocol_stop()
    if op == "mission":
        return nexus.mission_plan(b)
    return {"error": "not found"}


def ticket_for_dir(path):
    """A 15-minute link to one folder (a 3D model with its textures next to it)."""
    if not path or not os.path.isdir(path):
        return None
    t = secrets.token_urlsafe(18)
    with _lock:
        _tickets[t] = {"base": os.path.realpath(path), "only": None, "exp": time.time() + 900}
    return f"/v/{t}/"


def _scene_links(scene):
    """Turns a scene's folder on the PC into a link the viewer can load (the PC path itself never goes to the phone)."""
    s = dict(scene)
    d = s.pop("dir", None)
    if d and s.get("entry"):
        base = ticket_for_dir(d)
        s["url"] = base + "/".join(urllib.parse.quote(p) for p in s["entry"].split("/")) if base else None
    s["alternatives"] = [{k: v for k, v in a.items() if k != "dir"} for a in s.get("alternatives") or []]
    return s


def _overdrive(method, op, query, b, device):
    """AURA OVERDRIVE: AEGIS guard, TOTAL RECALL, AIR CONTROL, PC power (overdrive.py / aegis.py / recall.py)."""
    import aegis
    import overdrive
    import recall
    link = lambda p: ticket_for_path(p) if p and os.path.isfile(p) else None
    if method == "GET":
        if op == "status":
            st = aegis.status()
            for ev in st["events"]:
                ev["urls"] = [link(os.path.join(aegis.PHOTOS, n)) for n in ev.get("photos", [])][:3]
            return {"aegis": st, "recall": recall.status()}
        if op == "recall/day":
            day = query.get("d", ["today"])[0]
            es = recall.entries(day)
            base = ticket_for_dir(os.path.join(recall.STORE, es[0]["day"])) if es else None
            return {"day": es[0]["day"] if es else day, "entries": [{**e, "url": (base + urllib.parse.quote(e["file"])) if base else None} for e in es]}
        return {"error": "not found"}
    if op == "aegis":
        a = str(b.get("action") or "")
        if a == "arm":
            return aegis.arm("phone")
        if a == "disarm":
            r = aegis.disarm("phone")
            return {**aegis.status(), "summary": aegis.summary_text(r["while_away"])}
        if a == "auto":
            return aegis.set_auto(b.get("minutes") or 0)
        if a == "lock_on_touch":
            return aegis.set_lock_on_touch(b.get("on", True))
        return {"error": "unknown"}
    if op == "recall":
        a = str(b.get("action") or "")
        if a in ("on", "off"):
            return recall.set_on(a == "on")
        if a == "pause":
            return recall.pause(b.get("minutes") or 30)
        if a == "resume":
            return recall.resume()
        if a == "delete":
            return recall.delete(b.get("day") or "today")
        if a == "exclude":
            return recall.set_exclude(b.get("words") or [])
        if a == "search":
            r = recall.search(str(b.get("q") or ""))
            out = []
            for e in r["moments"]:
                p = recall.file_path(e["day"], e["file"])
                out.append({**e, "url": link(p)})
            return {"answer": r["answer"], "moments": out}
        return {"error": "unknown"}
    if op == "pc":
        r = overdrive.pc(b.get("action"))
        if r.get("file"):
            r["url"] = link(r.pop("file"))
        return r
    if op == "air/open":
        return {"ok": True, "say": overdrive.open_air()}
    if op == "air":
        if not device.get("pc"):
            return {"error": "Air Control runs in AURA's own window on the PC."}
        return overdrive.air_events(b.get("events") or [])
    return {"error": "not found"}


def _spatial(method, op, query, b, device):
    """AURA SPATIAL: bring up anything in 3D (spatial.py)."""
    import spatial
    if method == "GET":
        if op == "job":
            j = spatial.job_status(query.get("id", [""])[0])
            if j.get("state") == "done" and j.get("scene"):
                j = {**j, "scene": _scene_links(j["scene"])}
            return j
        if op == "status":
            return spatial.status()
        if op == "history":
            return {"items": spatial.history()}
        return {"error": "not found"}
    try:
        if op == "resolve":
            return {"job": spatial.start(b.get("query"), device["id"], b.get("context"), b.get("choice"), b.get("prefer"))}
        if op == "cancel":
            return {"ok": spatial.cancel(str(b.get("job") or ""))}
        if op == "command":
            return spatial.command(b.get("text"), b.get("context"))
        if op == "explain":
            return spatial.explain(b.get("context"), b.get("target"))
        if op == "dissect":
            img = str(b.get("image") or "")
            if not img or len(img) > 6_000_000:
                return {"error": "No picture to dissect."}
            return spatial.dissect(img, str(b.get("subject") or ""), b.get("context"))
        if op == "identify":
            img = str(b.get("image") or "")
            if not img or len(img) > 6_000_000:
                return {"error": "No picture to scan."}
            return spatial.identify(img, b.get("items") or [], b.get("context"))
        if op == "settings":
            t = str(b.get("sketchfab_token") or "").strip()
            if t and not re.fullmatch(r"[A-Za-z0-9]{20,64}", t):
                return {"error": "That doesn't look like a Sketchfab API token (Settings → Password & API on sketchfab.com)."}
            spatial.set_setting("sketchfab_token", t)
            return {"sketchfab": bool(t)}
    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        print(f"(spatial {op}: {e!r})")
        return {"error": "Couldn't do that: " + str(e)[:200]}
    return {"error": "not found"}


def _studio(method, op, query, b, device):
    """AURA STUDIO: make anything / change it with words (studio.py). Files open through 15-minute links."""
    import studio
    link = lambda p: ticket_for_path(p) if p and os.path.isfile(p) else None
    if method == "GET":
        if op == "projects":
            return {"items": [{**{k: v for k, v in p.items() if k not in ("file", "thumb")}, "thumb": link(p["thumb"]), "link": link(p["file"])}
                              for p in studio.projects()]}
        if op == "project":
            proj = studio.load(query.get("id", [""])[0])
            if not proj:
                return {"error": "That creation wasn't found."}
            d = studio._pdir(proj["id"])
            return {"id": proj["id"], "title": proj["title"], "versions": [
                {**{k: v for k, v in ver.items() if k not in ("spec",)}, "link": link(os.path.join(d, ver["file"])),
                 "thumb": link(os.path.join(d, ver["thumb"])) if ver.get("thumb") else None} for ver in proj["versions"]]}
        if op == "job":
            return studio.job(query.get("id", [""])[0])
        if op == "hf":
            import hf
            return {"ready": hf.ready()}
        return {"error": "not found"}
    if op == "create":
        try:
            return {"job": studio.start(str(b.get("prompt") or ""), b.get("project") or None, b.get("attachments") or [], device["id"])}
        except ValueError as e:
            return {"error": str(e)}
    if op == "delete":
        return {"ok": studio.delete(b.get("id"))}
    if op == "hf":
        import hf
        try:
            return {"ready": hf.set_token(b.get("token"))}
        except ValueError as e:
            return {"error": str(e)}
    return {"error": "not found"}


def _cdc_rel(status):
    """Workbook paths relative to the CDC WONDER folder, so the phone can open them."""
    if isinstance(status, dict):
        root = _roots()["cdc"]
        status["outputs_rel"] = [os.path.relpath(p, root) for p in status.get("outputs") or []
                                 if _inside(root, p) and os.path.exists(p)]
    return status


def _cdc(op, body):
    import cdc_window
    api = cdc_window.CdcAPI()
    if op == "setup":
        data = json.loads(api.get_setup())
        _cdc_rel(data.get("status"))
        return data
    if op == "check":
        return json.loads(api.check_codes(body.get("text", "")))
    if op == "start":
        data = json.loads(api.start(json.dumps(body)))
        _cdc_rel(data.get("status"))
        return data
    if op == "status":
        return _cdc_rel(json.loads(api.status()))
    if op == "stop":
        api.stop()
        return _cdc_rel(json.loads(api.status()))
    if op == "build":
        outputs = cdc_window._build(open_files=False)
        return {"outputs": outputs, "status": _cdc_rel(json.loads(api.status()))}
    return {"error": "unknown"}


def _upload(name, body):
    name = re.sub(r'[\\/:*?"<>|]+', "_", os.path.basename(name or "upload"))[:120] or "upload"
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    base, ext = os.path.splitext(name)
    path = os.path.join(UPLOAD_DIR, name)
    n = 2
    while os.path.exists(path):
        path = os.path.join(UPLOAD_DIR, f"{base} ({n}){ext}")
        n += 1
    with open(path, "wb") as f:
        f.write(body)
    try:
        import file_search
        file_search.refresh_in_background()     # so "analyse <name>" finds it
    except Exception:
        pass
    return {"ok": True, "name": os.path.basename(path), "folder": UPLOAD_DIR}


def _focus(body):
    import focus
    act = body.get("action")
    if act == "start":
        return focus.start(int(body.get("minutes") or 25), body.get("label") or "Focus")
    if act in ("pause", "resume", "stop"):
        return getattr(focus, act)()
    return None


def _journal(body):
    from life_data import log_journal_entry
    mood = body.get("mood")
    log_journal_entry(int(mood) if str(mood or "").isdigit() else None,
                      str(body.get("good") or "")[:1000], str(body.get("mind") or "")[:1000])
    return {"ok": True}


def _log_session(body):
    from life_data import log_session, get_session_type_summary
    kind = re.sub(r"[^\w \-]", "", str(body.get("type") or "session"))[:30].strip().lower() or "session"
    rating = body.get("rating")
    log_session(kind, timestamp=body.get("timestamp") or None,
                rating=int(rating) if str(rating or "").isdigit() else None)
    s = get_session_type_summary(kind)
    msg = f"Logged. You've now logged {s['count']} {kind} session(s)"
    msg += f", averaging {s['avg_rating']} out of 10." if s["avg_rating"] is not None else "."
    return {"ok": True, "message": msg}


def _pc_action(name):
    import face_window
    if name == "show_hud":
        face_window.show_face()
    elif name == "dashboard":
        import dashboard_window
        dashboard_window.show_dashboard()
    elif name == "schedule":
        import schedule_window
        schedule_window.show_schedule()
    elif name == "stop_speaking":
        from voice import interrupt_event
        interrupt_event.set()
    elif name == "quit":
        threading.Timer(1.0, lambda: os._exit(0)).start()
    else:
        return False
    return True


# ------------------------------------------------------------------ Web Push
def _vapid():
    """The PC's push identity (made once, kept in the devices file)."""
    with _lock:
        data = _load_devices()
        if not data.get("vapid"):
            from cryptography.hazmat.primitives.asymmetric import ec
            from cryptography.hazmat.primitives import serialization
            key = ec.generate_private_key(ec.SECP256R1())
            pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption()).decode()
            pub = key.public_key().public_bytes(serialization.Encoding.X962,
                                                serialization.PublicFormat.UncompressedPoint)
            data["vapid"] = {"private_pem": pem, "public": base64.urlsafe_b64encode(pub).decode().rstrip("=")}
            _save_devices(data)
        return data["vapid"]


def _push_all(title, body, kind, only_if_closed=False, only_device=None):
    data = _load_devices()
    subs = data.get("push", {})
    if not subs:
        return
    for device_id, sub in list(subs.items()):
        if only_device and device_id != only_device:
            continue      # an answer goes to the device that asked
        if only_if_closed and _connected.get(device_id):
            continue      # the app is open - it already shows (and speaks) the reply
        threading.Thread(target=_push_one, args=(device_id, sub, title, body, kind), daemon=True).start()


def _push_one(device_id, sub, title, body, kind):
    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        print("(AURA Mobile: install pywebpush for phone notifications - pip install pywebpush)")
        return
    payload = json.dumps({"title": title[:80], "body": (body or "")[:300], "kind": kind})
    try:
        from py_vapid import Vapid01
        key = Vapid01.from_pem(_vapid()["private_pem"].encode())
        webpush(sub, payload, vapid_private_key=key, vapid_claims={"sub": "mailto:aura-mobile@example.com"}, ttl=3600)
    except WebPushException as e:
        status = getattr(getattr(e, "response", None), "status_code", None)
        if status in (404, 410):       # the phone unsubscribed / reinstalled
            with _lock:
                d = _load_devices()
                d.get("push", {}).pop(device_id, None)
                _save_devices(d)
        else:
            print(f"(push failed: {e})")
    except Exception as e:
        print(f"(push failed: {e})")


# ------------------------------------------------------------------ voice & speech
def _transcribe(body, content_type):
    import voice
    ext = {"audio/mp4": "m4a", "audio/x-m4a": "m4a", "audio/aac": "m4a", "audio/webm": "webm",
           "audio/ogg": "ogg", "audio/wav": "wav", "audio/mpeg": "mp3"}.get(content_type.split(";")[0].strip(), "m4a")
    return voice.transcribe_bytes(body, f"speech.{ext}", content_type)


def _tts(text):
    import voice
    return voice.synth_bytes(text[:1200])


# ------------------------------------------------------------------ HTTP
class _Handler(http.server.BaseHTTPRequestHandler):
    server_version = "AURA"
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    # ---- helpers
    def _trace(self, code, size):
        """Diagnostics (temporary): what the iPhone asked for and how it went -> logs/phone_requests.log."""
        if "iPhone" not in self.headers.get("User-Agent", ""):
            return
        try:
            with open(os.path.join(AURA_DIR, "logs", "phone_requests.log"), "a", encoding="utf-8") as f:
                f.write(f"{datetime.datetime.now():%H:%M:%S} {self.command} {self.path.split('?')[0][:80]} -> {code} {size}b "
                        f"dest={self.headers.get('Sec-Fetch-Dest')} sw={self.headers.get('Service-Worker')}\n")
        except OSError:
            pass

    def _send(self, code, body=b"", ctype="application/json", headers=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self._trace(code, len(body))
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, obj)

    def _body(self):
        return self._raw

    def _read_body(self):
        """Always read the whole body first - on a kept-alive connection, unread bytes would be
        taken as the start of the next request."""
        n = int(self.headers.get("Content-Length") or 0)
        limit = 400 * 1024 * 1024 if self.path.startswith("/api/studio/upload") else MAX_BODY      # Studio: your videos
        if n > limit:
            self.close_connection = True
            raise ValueError("too large")
        self._raw = self.rfile.read(n) if n else b""

    def _jbody(self):
        raw = self._body()
        try:
            return json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return {}

    def _auth(self):
        h = self.headers.get("Authorization", "")
        if h.startswith("Bearer pc.") and self.client_address[0] in ("127.0.0.1", "::1") and not self._proxied():
            # AURA's own window on this PC (never reachable through Tailscale: those requests are proxied)
            return PC_DEVICE if hmac.compare_digest(h[10:].strip(), PC_KEY) else None
        if not h.startswith("Bearer ") and self.path.startswith("/api/watch/ask?"):
            # the Apple Watch link carries its key (HTTPS keeps it private) - and only a watch key works here
            key = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("key", [""])[0]
            d = _device_for(key)
            return d if d and d.get("watch") else None
        return _device_for(h[7:].strip() if h.startswith("Bearer ") else "")

    # ---- routing
    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def do_HEAD(self):
        self._route("GET")

    def _proxied(self):
        hd = self.headers
        return bool(hd.get("X-Forwarded-For") or hd.get("Tailscale-User-Login") or hd.get("X-Forwarded-Proto") or hd.get("Tailscale-Funnel-Request"))

    def _public(self):
        """True for a request from the open internet (Tailscale Funnel) rather than one of your own devices.
        Your devices come through Tailscale Serve with your login attached; Funnel requests carry no identity."""
        h = self.headers
        if h.get("Tailscale-Funnel-Request"):
            return True
        proxied = h.get("X-Forwarded-For") or h.get("Tailscale-User-Login") or h.get("X-Forwarded-Proto")
        return bool(proxied) and not h.get("Tailscale-User-Login")

    def _route(self, method):
        url = urllib.parse.urlparse(self.path)
        path, query = url.path, urllib.parse.parse_qs(url.query)
        self._raw = b""
        try:
            self._read_body()
            if self._public() and path not in ("/api/watch/ask", "/api/hello", "/api/net"):
                return self._send(404, "Not found", "text/plain")       # from the internet: only the watch, nothing else
            if self._public() and not _public_rate_ok(self.headers.get("X-Forwarded-For", "")):
                return self._json({"error": "slow down"}, 429)
            if path.startswith("/api/"):
                return self._api(method, path[5:], query)
            if path.startswith("/v/"):
                return self._view(path)
            return self._static(path)
        except ValueError as e:
            self._json({"error": str(e)}, 413 if "large" in str(e) else 400)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception as e:
            print(f"(phone bridge error on {path}: {e!r})")
            try:
                self._json({"error": "Something went wrong on the PC."}, 500)
            except Exception:
                pass

    def _static(self, path):
        if path in ("/app-pack.json", "/app-version"):        # the whole phone app in one download (app_bundle.py)
            import app_bundle
            pack = app_bundle.get()
            return self._send(200, pack if path == "/app-pack.json" else {"version": pack["version"]})
        if path == "/shared/cat.js":         # the HUD's cat - one source for PC and phone
            with open(os.path.join(AURA_DIR, "Face", "cat.js"), "rb") as f:
                return self._send(200, f.read(), "text/javascript; charset=utf-8")
        rel = urllib.parse.unquote(path).lstrip("/") or "index.html"
        full = os.path.realpath(os.path.join(APP_DIR, rel))
        if not _inside(APP_DIR, full) or os.path.isdir(full):
            full = os.path.join(APP_DIR, "index.html")
        if not os.path.exists(full):
            return self._send(404, "Not found", "text/plain")
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if full.endswith(".webmanifest"):
            ctype = "application/manifest+json"
        if full.endswith((".js", ".mjs")):
            ctype = "text/javascript"
        with open(full, "rb") as f:
            body = f.read()
        headers = {"Content-Security-Policy": "default-src 'self'; img-src 'self' blob: data: https://media.sketchfab.com; media-src 'self' blob:; "
                                              "script-src 'self' blob: https://cdn.jsdelivr.net 'unsafe-eval' 'wasm-unsafe-eval'; "
                                              "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                                              "font-src https://fonts.gstatic.com; frame-src 'self' https://sketchfab.com; worker-src 'self' blob:; "
                                              "connect-src 'self' blob: data: https://cdn.jsdelivr.net https://storage.googleapis.com https://tfhub.dev https://www.kaggle.com "
                                              # the phone brain (works while this PC is off): AI, speech-to-text, maps, weather, Wikipedia
                                              "https://api.groq.com https://generativelanguage.googleapis.com https://overpass-api.de "
                                              "https://overpass.private.coffee https://overpass.kumi.systems https://nominatim.openstreetmap.org "
                                              "https://api.open-meteo.com https://geocoding-api.open-meteo.com https://en.wikipedia.org"}
        if full.endswith("sw.js"):
            headers["Service-Worker-Allowed"] = "/"
        self._send(200, body, ctype + ("; charset=utf-8" if ctype.startswith("text/") or "json" in ctype else ""), headers)

    def _view(self, path):
        parts = path.split("/", 3)          # ["", "v", ticket, rest]
        t = _tickets.get(parts[2] if len(parts) > 2 else "")
        if not t or t["exp"] < time.time():
            return self._send(410, "This link has expired - open it again from AURA.", "text/plain")
        rel = urllib.parse.unquote(parts[3] if len(parts) > 3 else "")
        full = os.path.realpath(os.path.join(t["base"], rel))
        if not _inside(t["base"], full) or not os.path.isfile(full) or (t.get("only") and os.path.basename(full) != t["only"]):
            return self._send(404, "Not found", "text/plain")
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if full.lower().endswith((".md", ".txt", ".csv", ".log")):
            ctype = "text/plain; charset=utf-8"          # readable on the phone (PBL drafts are .md)
        with open(full, "rb") as f:
            body = f.read()
        headers = {"Content-Security-Policy": "script-src 'none'; frame-ancestors 'self'"}
        if not ctype.startswith(("text/html", "image/", "application/pdf", "text/plain")):
            headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{urllib.parse.quote(os.path.basename(full))}"
        self._send(200, body, ctype, headers)

    def _api(self, method, route, query):
        # ---- no token needed
        if route == "net":                     # how this request was classified (no secrets): tailnet device vs internet
            return self._json({"public": self._public(), "identity": bool(self.headers.get("Tailscale-User-Login")),
                               "proxied": bool(self.headers.get("X-Forwarded-For")), "funnel_header": bool(self.headers.get("Tailscale-Funnel-Request"))})
        if route == "hello":
            me = self._auth()
            return self._json({"app": "AURA", "paired": bool(me), "pc": os.environ.get("COMPUTERNAME", "PC"),
                               "device": me["id"] if me else None})        # each phone / iPad knows which replies are its own
        if route == "client-log" and method == "POST":
            # the phone app couldn't start / hit an error: keep it in logs/phone_errors.log (size-capped)
            b = self._jbody()
            line = f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S}  {str(b.get('error'))[:800]}  |  {str(b.get('ua'))[:200]}"
            print(f"(AURA Mobile error: {line})")
            try:
                os.makedirs(os.path.join(AURA_DIR, "logs"), exist_ok=True)
                path = os.path.join(AURA_DIR, "logs", "phone_errors.log")
                if not os.path.exists(path) or os.path.getsize(path) < 200_000:
                    with open(path, "a", encoding="utf-8") as f:
                        f.write(line + "\n")
            except OSError:
                pass
            return self._json({"ok": True})
        if route == "pair/request" and method == "POST":
            # anyone who can reach the bridge may ask - so at most one new code (and pop-up) per 20 s
            if time.time() - _pairing.get("asked", 0) < 20:
                return self._json({"error": "A code was just shown on your PC - use that one (or wait a few seconds)."}, 429)
            _pairing["asked"] = time.time()
            new_pairing_code()
            return self._json({"ok": True})
        if route == "pair" and method == "POST":
            b = self._jbody()
            res = _pair(b.get("code"), b.get("name"))
            if not res:
                return self._json({"error": "That code isn't right (or it expired). Ask the PC for a new one."}, 403)
            token, device = res
            return self._json({"token": token, "device": {"id": device["id"], "name": device["name"]}})

        device = self._auth()
        if not device:
            return self._json({"error": "not paired"}, 401)

        # ---- live stream
        if route == "events":
            return self._sse(device, int(self.headers.get("Last-Event-ID") or query.get("after", ["0"])[0] or 0))

        if method == "GET":
            if route.startswith("studio/"):     # AURA STUDIO (studio.py)
                return self._json(_studio("GET", route[7:], query, {}, device))
            if route.startswith("spatial/"):    # AURA SPATIAL (spatial.py)
                return self._json(_spatial("GET", route[8:], query, {}, device))
            if route.startswith("overdrive/"):  # AURA OVERDRIVE (overdrive.py)
                return self._json(_overdrive("GET", route[10:], query, {}, device))
            if route == "watch/ask":           # the simple watch link: plain-text answer to ?text=
                r = watch_ask(query.get("text", [""])[0], device)
                return self._send(200, r["reply"], "text/plain; charset=utf-8")
            if route == "state":
                return self._json(_state())
            if route == "notes":
                from notes import get_notes
                return self._json({"text": get_notes()})
            if route == "emails":
                import gmail_watch
                return self._json({"emails": gmail_watch.pending_list()})
            if route == "dashboard":
                return self._json(_dashboard())
            if route == "files":
                return self._json({"files": _list_files(query.get("kind", [""])[0], query.get("path", [""])[0])})
            if route == "devices":
                return self._json({"devices": [{"id": d["id"], "name": d["name"], "created": d["created"],
                                                "last_seen": d.get("last_seen"), "me": d["id"] == device["id"]}
                                               for d in _load_devices()["devices"]]})
            if route == "push/key":
                return self._json({"key": _vapid()["public"]})
            if route.startswith("cdc/"):
                return self._json(_cdc(route[4:], {}))
            if route == "vision/memory":
                import vision_lab
                return self._json({"items": vision_lab.memory_list()})
            if route.startswith("nexus/"):
                return self._nexus_reply(_nexus("GET", route[6:], query, {}, device))
            if route == "brain/kit":           # the phone's own brain, for when this PC is off (phone_sync.py)
                import phone_sync
                return self._json(phone_sync.kit())
            if route == "brain/knowledge":     # your study material, searchable on the phone too
                import phone_sync
                return self._json(phone_sync.knowledge_pack())
            return self._json({"error": "not found"}, 404)

        # ---- POST
        if route == "voice":
            body = self._body()
            text = _transcribe(body, self.headers.get("Content-Type", "audio/mp4"))
            if not text:
                return self._json({"text": "", "status": "nothing"})
            return self._json({"text": text, "status": _submit(text, device)})
        if route == "transcribe":      # speech -> text only (Vision's ASK mode), not sent to AURA as a command
            return self._json({"text": _transcribe(self._body(), self.headers.get("Content-Type", "audio/mp4")) or ""})
        if route == "tts":
            audio, ctype = _tts(self._jbody().get("text", ""))
            return self._send(200, audio, ctype) if audio else self._json({"error": "no audio"}, 503)
        if route in ("vision/identify", "vision/scan"):
            return self._json(_vision(self._body(), route[7:], query.get("hint", [""])[0][:60]))
        if route == "upload":
            return self._json(_upload(query.get("name", ["upload"])[0], self._body()))
        if route == "studio/upload":           # a photo / video to change in Studio
            import studio
            return self._json(studio.save_upload(query.get("name", ["upload"])[0], self._body()))

        b = self._jbody()
        if route == "command":
            return self._json({"status": _submit(b.get("text"), device)})
        if route == "tasks/add":
            from tasks import add_task
            if (b.get("text") or "").strip():
                add_task(b["text"].strip()[:200])
            return self._json({"ok": True})
        if route == "tasks/complete":
            from tasks import complete_task
            return self._json({"ok": bool(complete_task(b.get("text") or ""))})
        if route == "notes":
            from notes import save_notes
            return self._json({"ok": save_notes(str(b.get("text") or ""))})
        if route == "reminders/add":
            import reminders
            return self._json({"message": reminders.add_from_text(str(b.get("text") or ""))})
        if route == "reminders/cancel":
            import reminders
            return self._json({"ok": bool(reminders.cancel(b.get("id")))})
        if route == "focus":
            return self._json({"result": _focus(b)})
        if route == "dnd":
            import presence
            on = presence.set_dnd(bool(b.get("on")))
            try:
                import mini_window
                mini_window.refresh_info()
            except Exception:
                pass
            return self._json({"dnd": on})
        if route == "emails/dismiss":
            import gmail_watch
            ids = [p["id"] for p in gmail_watch.pending_list()] if b.get("all") else list(b.get("ids") or [])
            gmail_watch.dismiss(ids)
            return self._json({"emails": gmail_watch.pending_list()})
        if route == "log-session":
            return self._json(_log_session(b))
        if route == "journal":
            return self._json(_journal(b))
        if route == "explain":
            return self._json(_explain(str(b.get("text") or "")))
        if route == "explain/follow":
            return self._json(_follow_up(b.get("id"), str(b.get("question") or "")))
        if route == "view":
            link = _ticket(b.get("kind"), b.get("path"))
            return self._json({"url": link} if link else {"error": "not found"}, 200 if link else 404)
        if route.startswith("cdc/"):
            return self._json(_cdc(route[4:], b))
        if route.startswith("vision/"):
            return self._json(_vision_lab(route[7:], b))
        if route.startswith("nexus/"):
            return self._nexus_reply(_nexus("POST", route[6:], query, b, device))
        if route.startswith("studio/"):
            return self._json(_studio("POST", route[7:], query, b, device))
        if route.startswith("spatial/"):
            return self._json(_spatial("POST", route[8:], query, b, device))
        if route.startswith("overdrive/"):
            try:
                return self._json(_overdrive("POST", route[10:], query, b, device))
            except Exception as e:
                print(f"(overdrive: {e!r})")
                return self._json({"error": str(e)[:200]})
        if route == "watch/ask":               # the Apple Watch shortcut: answer in the same request
            return self._json(watch_ask(str(b.get("text") or ""), device))
        if route == "watch/key":               # Settings -> "Set up Apple Watch": a key just for the watch
            if device.get("watch"):
                return self._json({"error": "not from the watch"}, 403)
            token, watch = make_watch_key(device)
            base = "https://" + (self.headers.get("X-Forwarded-Host") or self.headers.get("Host") or "") + "/api/watch/ask"
            return self._json({"key": token, "url": base, "link": f"{base}?key={token}&text=", "device": watch["id"]})
        if route == "brain/sync":              # what happened on the phone while this PC was off
            import phone_sync
            return self._json(phone_sync.apply(b.get("ops") or [], device))
        if route == "notes/append":
            import vision_lab
            return self._json({"ok": vision_lab.add_to_notes(str(b.get("title") or "Note")[:80], str(b.get("text") or "")[:20000])})
        if route == "calendar/add":
            import vision_lab
            return self._json(vision_lab.add_to_calendar(str(b.get("text") or "")[:300]))
        if route == "pc":
            return self._json({"ok": _pc_action(b.get("action"))})
        if route == "push/subscribe":
            sub = b.get("subscription")
            if not (isinstance(sub, dict) and str(sub.get("endpoint", "")).startswith("https://")):
                return self._json({"error": "bad subscription"}, 400)
            with _lock:
                data = _load_devices()
                data.setdefault("push", {})[device["id"]] = sub
                _save_devices(data)
            return self._json({"ok": True})
        if route == "push/test":
            threading.Thread(target=_push_one, args=(device["id"], _load_devices().get("push", {}).get(device["id"]),
                                                     "AURA", "Notifications are working.", "info"), daemon=True).start()
            return self._json({"ok": True})
        if route == "devices/revoke":
            return self._json({"ok": _revoke(b.get("id") or device["id"])})
        return self._json({"error": "not found"}, 404)

    def _nexus_reply(self, res):
        if isinstance(res, tuple):
            return self._send(200, res[0], res[1], res[2])
        return self._json(res, 404 if res.get("error") == "not found" else 200)

    def _sse(self, device, last):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        self.close_connection = True
        _connected[device["id"]] += 1
        try:
            if last and _seq - last > len(_events):          # missed more than we keep -> resync
                self.wfile.write(b'data: {"type":"resync"}\n\n')
            self.wfile.write(f'data: {json.dumps({"type": "hello", "status": _status, "seq": _seq})}\n\n'.encode())
            self.wfile.flush()
            while True:
                with _cond:
                    _cond.wait_for(lambda: _seq > last, timeout=15)
                batch = _events_after(last)
                if batch:
                    for s, e in batch:
                        self.wfile.write(f"id: {s}\ndata: {e}\n\n".encode())
                    last = batch[-1][0]
                else:
                    self.wfile.write(b": ping\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass
        finally:
            _connected[device["id"]] -= 1


_public_hits = collections.defaultdict(collections.deque)


def _public_rate_ok(ip, per_minute=30):
    q, now = _public_hits[ip.split(",")[0].strip()], time.time()
    while q and now - q[0] > 60:
        q.popleft()
    q.append(now)
    return len(q) <= per_minute


class _Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def start(command_queue):
    """Starts the bridge (once). Only listens on this PC - Tailscale Serve brings it to the phone."""
    global _queue
    if _queue is not None:
        return
    _queue = command_queue

    def run():
        try:
            with _Server(("127.0.0.1", PORT), _Handler) as httpd:
                print(f"(AURA Mobile bridge on http://127.0.0.1:{PORT})")
                httpd.serve_forever()
        except OSError as e:
            print(f"(AURA Mobile bridge couldn't start: {e})")
    threading.Thread(target=run, daemon=True, name="phone-bridge").start()
