# aegis.py - AEGIS: AURA guards your PC while you're away.
# Arm it ("AEGIS on", "I'm leaving", the phone's OVERWATCH screen, or automatically after you've been idle a while) and the
# PC stays as it is - your download, render or open work keeps running - but the moment anyone touches the keyboard or
# mouse, AURA:
#   1. takes webcam photos of whoever is there (the camera only switches on for those few seconds),
#   2. LOCKS the PC instantly (your Windows password protects it from then on),
#   3. sends the alert + photo to your phone, and keeps a log.
# When you come back you unlock Windows as usual; AURA notices, stands down and tells you what happened while you were gone.
# Disarm from the phone (or by voice from the phone) before you sit down and nothing fires at all.
import ctypes
import json
import os
import threading
import time

AURA_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(AURA_DIR, "aura_aegis.json")
PHOTOS = os.path.join(os.path.expanduser("~"), "Documents", "AURA Aegis")
GRACE = 4.0              # seconds after arming before input counts (lets you step away from the keyboard)

_lock = threading.RLock()
_state = {"armed": False, "armed_at": 0, "reason": "", "auto_minutes": 0, "events": [], "lock_on_touch": True}
_hooks = {"speak": None}


def _load():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            _state.update(json.load(f))
    except Exception:
        pass
    _state["armed"] = False              # a restart never leaves the PC armed by surprise


def _save():
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({k: v for k, v in _state.items() if k != "armed"}, f)
    except Exception:
        pass


_load()


# ------------------------------------------------------------------ Windows bits
class _LII(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def last_input_tick():
    info = _LII()
    info.cbSize = ctypes.sizeof(info)
    ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info))
    return info.dwTime


def idle_seconds():
    return (ctypes.windll.kernel32.GetTickCount() - last_input_tick()) / 1000.0


def is_locked():
    """True while the Windows lock screen is up (the input desktop isn't the user's 'Default' desktop)."""
    user32 = ctypes.windll.user32
    h = user32.OpenInputDesktop(0, False, 0x0100)          # DESKTOP_SWITCHDESKTOP
    if not h:
        return True
    try:
        buf = ctypes.create_unicode_buffer(64)
        need = ctypes.c_uint(0)
        user32.GetUserObjectInformationW(h, 2, buf, ctypes.sizeof(buf), ctypes.byref(need))     # UOI_NAME
        return buf.value.lower() != "default"
    finally:
        user32.CloseDesktop(h)


def lock_pc():
    ctypes.windll.user32.LockWorkStation()


# ------------------------------------------------------------------ arming
def status():
    with _lock:
        return {"armed": _state["armed"], "armed_at": _state["armed_at"], "reason": _state["reason"], "auto_minutes": _state["auto_minutes"],
                "lock_on_touch": _state["lock_on_touch"], "events": _state["events"][-30:], "locked": is_locked()}


def arm(reason="manual"):
    with _lock:
        if _state["armed"]:
            return status()
        _state.update(armed=True, armed_at=time.time(), reason=reason, baseline=last_input_tick(), since=[])
    _emit()
    print(f"(AEGIS armed: {reason})")
    return status()


def disarm(by="you"):
    with _lock:
        was = _state["armed"]
        since = [e for e in _state["events"] if e["t"] >= _state.get("armed_at", 0)]
        _state.update(armed=False, reason="")
        _save()
    _emit()
    if was:
        print(f"(AEGIS disarmed by {by})")
    return {"armed": False, "while_away": since}


def set_auto(minutes):
    """Arm automatically after this many idle minutes (0 = never)."""
    with _lock:
        _state["auto_minutes"] = max(0, min(240, int(minutes or 0)))
        _save()
    return status()


def set_lock_on_touch(on):
    with _lock:
        _state["lock_on_touch"] = bool(on)
        _save()
    return status()


def summary_text(events):
    if not events:
        return "Nothing happened while you were away - nobody touched your PC."
    n = len(events)
    times = ", ".join(time.strftime("%H:%M", time.localtime(e["t"])) for e in events[:5])
    return f"Someone used your PC {n} time{'s' if n > 1 else ''} while you were away ({times}). I locked it and sent the photos to your phone."


# ------------------------------------------------------------------ the intrusion
def _photos(count=3):
    """A few webcam photos of whoever is at the PC (the camera turns on only for this)."""
    import camera
    saved = []
    was_on = camera.is_on()
    try:
        ok, _err = camera.start()
        if not ok:
            return saved
        os.makedirs(PHOTOS, exist_ok=True)
        last_id = -1
        for i in range(count):
            t_end = time.time() + 1.5
            while time.time() < t_end:
                jpg, fid = camera.latest_jpeg()
                if jpg and fid != last_id:
                    break
                time.sleep(0.05)
            if jpg and fid != last_id:
                last_id = fid
                path = os.path.join(PHOTOS, time.strftime("%Y%m%d-%H%M%S") + f"-{i + 1}.jpg")
                with open(path, "wb") as f:
                    f.write(jpg)
                saved.append(path)
            time.sleep(0.6)
    except Exception as e:
        print(f"(AEGIS camera: {e})")
    finally:
        if not was_on:
            try:
                camera.stop()
            except Exception:
                pass
    return saved


def _intrusion():
    t = time.time()
    lock = _state["lock_on_touch"]
    if lock:
        try:
            lock_pc()                                 # lock FIRST - nothing on screen is visible after this
        except Exception:
            pass
    photos = _photos()
    ev = {"t": t, "photos": [os.path.basename(p) for p in photos], "locked": lock}
    with _lock:
        _state["events"].append(ev)
        _state["events"] = _state["events"][-200:]
        _save()
    when = time.strftime("%H:%M:%S", time.localtime(t))
    try:
        import phone_bridge
        phone_bridge._push_all("⚠ AEGIS: someone is using your PC", f"Touched at {when}. {'Locked. ' if lock else ''}{len(photos)} photo(s) taken - open OVERWATCH.", "reminder")
        phone_bridge.emit("aegis", event="intrusion", t=t, photos=ev["photos"], locked=lock)
    except Exception as e:
        print(f"(AEGIS alert: {e})")
    print(f"(AEGIS: intrusion at {when}, {len(photos)} photos)")


def _emit():
    try:
        import phone_bridge
        phone_bridge.emit("aegis", event="state", armed=_state["armed"])
    except Exception:
        pass


def _watch():
    last_alert, was_locked = 0.0, False
    while True:
        time.sleep(0.4)
        try:
            locked = is_locked()
            with _lock:
                armed, armed_at, base = _state["armed"], _state["armed_at"], _state.get("baseline", 0)
                auto = _state["auto_minutes"]
            if armed:
                if was_locked and not locked:
                    # the owner unlocked Windows with their password: stand down and report
                    res = disarm("unlock")
                    speak = _hooks.get("speak")
                    if speak:
                        speak("Welcome back. " + summary_text(res["while_away"]))
                elif not locked and time.time() - armed_at > GRACE and last_input_tick() != base and time.time() - last_alert > 30:
                    last_alert = time.time()
                    with _lock:
                        _state["baseline"] = last_input_tick()
                    _intrusion()
                    with _lock:
                        _state["baseline"] = last_input_tick()
                elif locked:
                    with _lock:
                        _state["baseline"] = last_input_tick()
            elif auto and not locked and idle_seconds() > auto * 60:
                arm(f"idle {auto} min")
            was_locked = locked
        except Exception as e:
            print(f"(AEGIS watch: {e})")
            time.sleep(2)


def start(speak=None):
    _hooks["speak"] = speak
    threading.Thread(target=_watch, daemon=True, name="aegis").start()
