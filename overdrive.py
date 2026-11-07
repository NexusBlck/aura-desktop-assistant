# overdrive.py - AURA OVERDRIVE: the control layer that ties together
#   AIR CONTROL  - drive the whole PC with your hands through the webcam (mobile/src/screens/tools/air.js in a PC window)
#   TOTAL RECALL - AURA remembers your screen and answers questions about it (recall.py)
#   AEGIS        - guard mode: anyone touches the PC while you're away -> photo, lock, alert on your phone (aegis.py)
#   PC POWER     - lock / sleep / volume / media / screenshot from anywhere (OVERWATCH on the phone)
# handle(text) is the voice / chat entry point; the bridge exposes the same things at /api/overdrive/*.
import os
import re
import time

import aegis
import recall

AURA_DIR = os.path.dirname(os.path.abspath(__file__))
SHOTS = os.path.join(os.path.expanduser("~"), "Documents", "AURA Screenshots")


# ------------------------------------------------------------------ PC power
def pc(action):
    """Lock / sleep / mute / volume / media / screenshot. Returns {ok, ...}."""
    import ctypes
    import pyautogui
    a = str(action or "").lower()
    keys = {"mute": "volumemute", "volup": "volumeup", "voldown": "volumedown", "play": "playpause", "next": "nexttrack", "prev": "prevtrack"}
    if a == "lock":
        aegis.lock_pc()
    elif a == "sleep":
        ctypes.windll.powrprof.SetSuspendState(0, 1, 0)
    elif a in keys:
        for _ in range(5 if a in ("volup", "voldown") else 1):
            pyautogui.press(keys[a], _pause=False)
    elif a == "screenshot":
        from PIL import ImageGrab
        os.makedirs(SHOTS, exist_ok=True)
        path = os.path.join(SHOTS, time.strftime("Screenshot %Y-%m-%d %H%M%S.png"))
        ImageGrab.grab().save(path)
        return {"ok": True, "file": path}
    elif a == "show_desktop":
        pyautogui.hotkey("win", "d", _pause=False)
    elif a == "task_view":
        pyautogui.hotkey("win", "tab", _pause=False)
    else:
        return {"error": "unknown action"}
    return {"ok": True}


# ------------------------------------------------------------------ AIR CONTROL (the hand-tracking page runs in a PC window)
AIR_KEYS = {"next_app": ("alt", "tab"), "task_view": ("win", "tab"), "show_desktop": ("win", "d"), "close": ("alt", "f4"),
            "back": ("alt", "left"), "forward": ("alt", "right"), "zoom_in": ("ctrl", "+"), "zoom_out": ("ctrl", "-"),
            "playpause": ("playpause",), "volup": ("volumeup",), "voldown": ("volumedown",)}


def air_events(events):
    """Applies a batch of hand-control events from the AIR window: move / down / up / click / scroll / key shortcuts."""
    import nexus
    import pyautogui
    done = 0
    for ev in (events or [])[:40]:
        if not isinstance(ev, dict):
            continue
        if ev.get("type") == "shortcut":
            ks = AIR_KEYS.get(str(ev.get("name")))
            if ks:
                pyautogui.hotkey(*ks, _pause=False) if len(ks) > 1 else pyautogui.press(ks[0], _pause=False)
                done += 1
            continue
        if ev.get("type") in ("move_abs", "move_rel", "click", "down", "up", "scroll"):
            r = nexus.remote_input(ev, notice=False)
            done += 1 if r.get("ok") else 0
    return {"ok": True, "done": done}


def open_air():
    import spatial_window
    spatial_window.open_tool("air", size=(560, 440), always=True)
    return "Air Control is starting - hold your hand up to the webcam. Point to move, pinch to click, fist to scroll."


# ------------------------------------------------------------------ voice / chat
_ON = r"\b(on|start|enable|activate|arm|turn on|switch on|begin)\b"
_OFF = r"\b(off|stop|disable|deactivate|disarm|turn off|switch off|end)\b"


def handle(text):
    """'AEGIS on', 'I'm leaving', 'total recall on', 'what was I doing at 3pm yesterday', 'air control'... Returns the reply."""
    t = str(text or "").strip()
    low = t.lower()
    if re.search(r"\b(air ?control|air ?mouse|control (my|the) (pc|computer) with my hands?|hand control( my pc)?)\b", low):
        if re.search(_OFF, low):
            return "Close the Air Control window to stop it (or hold both hands up then drop them)."
        return open_air()
    if re.search(r"\b(aegis|guard (my|the) (pc|computer)|guard mode|i'?m leaving|i am leaving|watch my (pc|computer))\b", low):
        if re.search(_OFF, low) and not re.search(r"leaving", low):
            res = aegis.disarm("voice")
            return "AEGIS is off. " + aegis.summary_text(res["while_away"])
        m = re.search(r"after (\d+) ?min", low)
        if m:
            aegis.set_auto(int(m.group(1)))
            return f"AEGIS will arm itself whenever you've been away {m.group(1)} minutes."
        aegis.arm("voice")
        return ("AEGIS armed. Walk away - if anyone touches your PC I'll photograph them, lock it and alert your phone. "
                "Unlock Windows when you're back and I'll tell you what happened.")
    if re.search(r"\b(total recall|screen memory|recall)\b", low) and re.search(_ON + "|" + _OFF + r"|\bpause\b|\bdelete\b|\bresume\b", low):
        if "delete" in low:
            recall.delete("today" if "today" in low else "all")
            return "Deleted " + ("today's" if "today" in low else "all") + " screen memory."
        if "pause" in low:
            m = re.search(r"(\d+) ?(min|hour)", low)
            mins = int(m.group(1)) * (60 if m and m.group(2).startswith("hour") else 1) if m else 30
            recall.pause(mins)
            return f"Total Recall paused for {mins} minutes."
        if "resume" in low:
            recall.resume()
            return "Total Recall resumed."
        if re.search(_OFF, low):
            recall.set_on(False)
            return "Total Recall is off - I've stopped remembering your screen."
        recall.set_on(True)
        return ("Total Recall is on. I'll remember what's on your screen (never passwords, banking or private windows) for "
                f"{recall.settings()['keep_days']} days, only on this PC. Ask me things like 'what was I working on at 3?'.")
    # anything else that reached here is a question about the past
    r = recall.search(t)
    return r["answer"]


def looks_like_recall_question(text):
    low = str(text or "").lower()
    return bool(re.search(r"\b(what was i (doing|working on|reading|watching|looking at)|what did i (see|read|have open|work on)|"
                          r"find (that|the) (article|page|tab|document|video|website|email|thing) i (saw|read|had|opened|was)|"
                          r"when did i (last )?(open|see|read|work on|have)|on my screen (yesterday|earlier|this morning)|"
                          r"rewind my (day|screen))\b", low))
