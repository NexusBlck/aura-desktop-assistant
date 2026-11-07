# desktop_agent.py
# AURA operating your Windows PC to get things DONE - "open Word and write a
# leave letter", "in Spotify play my liked songs", "what does this error say",
# "rename the files in my Downloads that start with IMG", "change the
# volume in this app"...
#
# Instead of guessing pixel positions from screenshots (what vision.py does,
# and why it missed buttons), it reads the window the way screen readers do -
# Windows UI Automation: every button, menu, tab, list item and text box, with
# its name, value and exact position - and acts on those directly. Screenshots
# are only the fallback ("look") for things with no structure: images, games,
# video, canvas apps.
#
# Commands stack: AURA remembers what it just did on your PC, so "now save it
# as PDF" or "and send it to my notes folder" carry on from the last task.
#
# Guardrails (always on):
#   * Never clicks anything that deletes, uninstalls, formats, sends, pays/buys,
#     submits, signs out, shuts down / restarts, empties the recycle bin, or
#     discards unsaved work - it tells you instead.
#   * Never types into password fields. Never presses Delete outside a text box.
#   * Emergency stop: Ctrl+Alt+X stops it instantly (also: slam the mouse into
#     the top-left corner of the screen).
import json
import queue
import re
import threading
import time

import keyboard
import pyautogui

MAX_STEPS = 20
MAX_ELEMENTS = 90
MAX_TEXTS = 40
WALK_SECONDS = 2.5
HISTORY_TURNS = 6
STOP_HOTKEY = "ctrl+alt+x"

pyautogui.FAILSAFE = True   # mouse into the top-left corner aborts any automated mouse move

RISKY = re.compile(r"\b(delete|remove|uninstall|format|erase|wipe|send|post|publish|pay|buy|purchase|checkout|"
                   r"submit|sign\s*out|log\s*out|shut\s*down|restart|reset|empty\s+recycle|permanently|"
                   r"discard|don'?t\s+save|overwrite|install|transfer|withdraw)\b", re.I)
RISKY_KEYS = re.compile(r"^(shift\+)?(delete|del)$|alt\+f4.*|ctrl\+shift\+(esc|delete)", re.I)

INTERESTING = {"ButtonControl", "MenuItemControl", "ListItemControl", "TreeItemControl", "EditControl",
               "DocumentControl", "ComboBoxControl", "CheckBoxControl", "RadioButtonControl", "TabItemControl",
               "HyperlinkControl", "SplitButtonControl", "DataItemControl", "MenuBarControl", "SliderControl",
               "SpinnerControl", "HeaderItemControl"}

_stop = threading.Event()


def _install_stop_hotkey():
    try:
        keyboard.add_hotkey(STOP_HOTKEY, _stop.set)
    except Exception as e:
        print(f"(couldn't register the {STOP_HOTKEY} stop hotkey: {e})")


# ------------------------------------------------------------------ reading the screen
def _is_aura(name):
    return (name or "").startswith("AURA")


def _foreground():
    """The window the user is working in (skipping AURA's own windows)."""
    import uiautomation as auto
    win = auto.GetForegroundControl()
    if win and _is_aura(win.Name):
        try:
            import active_window
            if active_window.focus_target():
                time.sleep(0.5)
                win = auto.GetForegroundControl()
        except Exception:
            pass
    return win


def _top_windows():
    import uiautomation as auto
    out = []
    for w in auto.GetRootControl().GetChildren():
        try:
            if w.Name and not w.IsOffscreen and not _is_aura(w.Name) and w.Name not in ("Taskbar", "Program Manager") \
                    and not w.Name.startswith("Activate Windows"):
                out.append(w)
        except Exception:
            pass
    return out


def _value(c):
    try:
        if c.ControlTypeName in ("EditControl", "ComboBoxControl", "DocumentControl", "SliderControl", "SpinnerControl"):
            v = c.GetValuePattern().Value
            return (v or "")[:120]
    except Exception:
        pass
    return None


def _state(c):
    try:
        if c.ControlTypeName in ("CheckBoxControl",):
            return " [on]" if c.GetTogglePattern().ToggleState == 1 else " [off]"
        if c.ControlTypeName in ("RadioButtonControl", "ListItemControl", "TabItemControl", "TreeItemControl"):
            return " [selected]" if c.GetSelectionItemPattern().IsSelected else ""
    except Exception:
        pass
    return ""


def snapshot():
    """(elements dict id->control, text for the model)."""
    import uiautomation as auto
    win = _foreground()
    elements, lines, texts = {}, [], []
    started = time.time()
    if win:
        for c, depth in auto.WalkControl(win, maxDepth=40):
            if time.time() - started > WALK_SECONDS or len(elements) >= MAX_ELEMENTS:
                break
            try:
                if c.IsOffscreen:
                    continue
                kind = c.ControlTypeName
                name = (c.Name or "").replace("\n", " ").strip()[:90]
                if kind == "TextControl":
                    if name and len(texts) < MAX_TEXTS and name not in texts:
                        texts.append(name)
                    continue
                if kind not in INTERESTING or not c.IsEnabled:
                    continue
                if not name and kind not in ("EditControl", "DocumentControl", "ComboBoxControl"):
                    continue
                n = len(elements) + 1
                elements[n] = c
                extra = ""
                if getattr(c, "IsPassword", False):
                    extra = " (password field - AURA never types here)"
                else:
                    v = _value(c)
                    if v is not None:
                        extra = f' value="{v}"'
                lines.append(f'[{n}] {kind.replace("Control", "")} "{name}"{extra}{_state(c)}')
            except Exception:
                continue
    windows = [w.Name[:70] for w in _top_windows()][:15]
    text = (f"FOREGROUND WINDOW: {win.Name if win else '(none)'}\n"
            f"OTHER OPEN WINDOWS: {' | '.join(windows)}\n\n"
            f"CONTROLS IN THE FOREGROUND WINDOW:\n" + ("\n".join(lines) or "(none readable - use 'look' to see the screen)")
            + ("\n\nTEXT ON SCREEN:\n" + "\n".join(texts) if texts else ""))
    return elements, text


# ------------------------------------------------------------------ acting
def _look(question):
    """Screenshot fallback for things UI Automation can't read (images, games, video, canvas apps)."""
    try:
        import vision
        full, small = vision._capture_screen()
        return vision._ask_vision([
            {"type": "text", "text": f"Look at this screenshot of the user's Windows screen and answer briefly and "
                                     f"precisely: {question}"},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{vision._encode(small)}"}},
        ], max_tokens=400).strip()[:800]
    except Exception as e:
        return f"couldn't look at the screen: {e}"


def _type_text(text):
    keyboard.write(text, delay=0.01)


def _do(step, elements):
    act = (step.get("action") or "").lower()
    if act in ("click", "double_click", "right_click", "type", "select"):
        c = elements.get(int(step.get("id", -1) or -1))
        if c is None:
            return f"control [{step.get('id')}] isn't on screen any more - take a fresh look"
        name = c.Name or ""
        if act != "type" and RISKY.search(name):
            return f"REFUSED to click '{name}' - that could delete/send/pay/sign out/etc. Tell the user instead."
        if act == "click":
            try:
                c.GetInvokePattern().Invoke()          # presses it without moving your mouse
            except Exception:
                try:
                    c.GetSelectionItemPattern().Select()
                except Exception:
                    c.Click(simulateMove=False)
        elif act == "double_click":
            c.DoubleClick(simulateMove=False)
        elif act == "right_click":
            c.RightClick(simulateMove=False)
        elif act == "select":
            option = str(step.get("option", ""))
            try:
                c.GetExpandCollapsePattern().Expand()
                time.sleep(0.4)
            except Exception:
                c.Click(simulateMove=False)
            import uiautomation as auto
            item = auto.ListItemControl(searchFromControl=c.GetTopLevelControl(), Name=option, searchDepth=12)
            if not item.Exists(2):
                return f"option '{option}' not found in that list"
            item.Click(simulateMove=False)
        else:   # type
            if getattr(c, "IsPassword", False):
                return "REFUSED - AURA never types into password fields."
            c.SetFocus()
            c.Click(simulateMove=False)
            time.sleep(0.15)
            if step.get("replace"):
                keyboard.send("ctrl+a")
            _type_text(str(step.get("text", "")))
            if step.get("enter"):
                keyboard.send("enter")
    elif act == "press":
        keys = str(step.get("keys", "")).lower().strip()
        if RISKY_KEYS.search(keys):
            import uiautomation as auto
            focused = auto.GetFocusedControl()
            if not focused or focused.ControlTypeName not in ("EditControl", "DocumentControl") or "alt+f4" in keys:
                return f"REFUSED to press {keys} here - it could delete or close something. Tell the user instead."
        keyboard.send(keys)
    elif act == "open_app":
        from pc_control import open_app
        result = open_app(str(step.get("name", "")))
        time.sleep(2.5)
        return result
    elif act == "focus":
        title = str(step.get("window", "")).lower()
        match = next((w for w in _top_windows() if title and title in w.Name.lower()), None)
        if not match:
            return f"no open window matches '{title}'"
        try:
            match.SetActive()
        except Exception:
            match.SetFocus()
    elif act == "scroll":
        pyautogui.scroll(-600 if str(step.get("direction", "down")) == "down" else 600)
    elif act == "wait":
        time.sleep(min(float(step.get("seconds", 2) or 2), 8))
    elif act == "look":
        return "screen says: " + _look(str(step.get("question", "what is on the screen?")))
    else:
        return f"unknown action '{act}'"
    time.sleep(0.8)
    return "done"


AGENT_PROMPT = """You are AURA, operating the user's Windows PC to finish their request completely - they do not
want to do manual steps. You see the foreground window's controls (from Windows UI Automation: exact names,
values, states) and the list of other open windows. Choose exactly ONE next action.

The user's request: "{task}"
{memory}
Steps taken so far:
{steps}

{screen}

Rules:
- Prefer acting on controls by id. Keyboard shortcuts (press) are great when reliable: ctrl+s save, ctrl+n new,
  ctrl+f find, ctrl+l address bar, alt+tab, win (Start), win+e File Explorer, enter, esc, tab.
- To work in an app that isn't open, use open_app. To switch to an open one, use focus with part of its title.
- If the controls list is empty/unhelpful (games, images, video, canvas apps), use look to read the screen.
- NEVER delete, uninstall, format, send, pay/buy, submit, sign out, shut down or discard work - answer telling
  the user to do that part. Never type passwords.
- Don't repeat the same action more than twice; if something didn't work, try another way.
- When the request is complete - or you have the information asked for - use answer: 1-3 natural spoken
  sentences with the actual result. If it's impossible, answer saying why.

Reply ONLY with JSON, one of:
{{"action": "click"|"double_click"|"right_click", "id": N, "why": "..."}}
{{"action": "type", "id": N, "text": "...", "replace": true/false, "enter": true/false, "why": "..."}}
{{"action": "select", "id": N, "option": "item name", "why": "..."}}
{{"action": "press", "keys": "ctrl+s", "why": "..."}}
{{"action": "open_app", "name": "word", "why": "..."}}
{{"action": "focus", "window": "part of the window title", "why": "..."}}
{{"action": "scroll", "direction": "down"|"up", "why": "..."}}
{{"action": "wait", "seconds": 2, "why": "..."}}
{{"action": "look", "question": "what to read off the screen", "why": "..."}}
{{"action": "answer", "text": "..."}}"""


def run_task(task, status=print, memory=()):
    """The agent loop. Returns the final spoken answer."""
    import uiautomation as auto
    from autocoder import _paced, DailyLimitReached
    from ai import extract_json
    _stop.clear()
    mem = ""
    if memory:
        mem = "Earlier on this PC (the user may refer back to it):\n" + "\n".join(
            f"- asked \"{m['task']}\" -> {m['answer']}" for m in memory[-HISTORY_TURNS:])
    steps = []
    with auto.UIAutomationInitializerInThread():
        for n in range(MAX_STEPS):
            if _stop.is_set():
                return "Stopped - you pressed the emergency stop."
            elements, screen = snapshot()
            prompt = AGENT_PROMPT.format(task=task, memory=mem, steps="\n".join(steps[-12:]) or "(none yet)", screen=screen)
            try:
                step = extract_json(_paced(prompt, temperature=0.1, reasoning_effort="low", expect=600))
            except DailyLimitReached as e:
                return f"I've hit the free AI model's daily limit, so I had to stop - {e}."
            except Exception:
                steps.append(f"{n + 1}. (reply wasn't valid JSON - reply with JSON only)")
                continue
            if not isinstance(step, dict):
                continue
            if step.get("action") == "answer":
                return str(step.get("text") or "").strip() or "Done."
            if _stop.is_set():
                return "Stopped - you pressed the emergency stop."
            status(str(step.get("why") or step.get("action"))[:120])
            try:
                result = _do(step, elements)
            except pyautogui.FailSafeException:
                return "Stopped - you moved the mouse into the corner (emergency stop)."
            except Exception as e:
                result = f"failed: {str(e).splitlines()[0][:150]}"
            desc = {k: v for k, v in step.items() if k != "why"}
            key = json.dumps(desc, sort_keys=True)
            if sum(1 for s in steps if key in s) >= 2:
                result += " | WARNING: third time doing exactly this - stop repeating it and try something different"
            steps.append(f"{n + 1}. {key} -> {result}")
    return "I couldn't finish that within my step limit - I've left things where I stopped."


# ------------------------------------------------------------------ queue so commands stack
class DesktopSession:
    def __init__(self):
        self.jobs = queue.Queue()
        self.memory = []
        self.thread = None
        self.busy = False

    def submit(self, task, say):
        if not (self.thread and self.thread.is_alive()):
            self.thread = threading.Thread(target=self._loop, daemon=True)
            self.thread.start()
        self.jobs.put((task, say))

    def _loop(self):
        while True:
            task, say = self.jobs.get()
            self.busy = True
            try:
                answer = run_task(task, lambda t: say(t, final=False), self.memory)
            except Exception as e:
                answer = f"Something went wrong while using your PC: {str(e).splitlines()[0][:160]}"
            finally:
                self.busy = False
            self.memory.append({"task": task, "answer": answer[:300]})
            say(answer, final=True)


SESSION = DesktopSession()
_install_stop_hotkey()


def _say(text, final=False):
    print(f"  [pc] {text}")
    try:
        if final:
            from voice import speak
            speak(text)
        else:
            import face_window
            face_window.add_log("aura", text)
    except Exception:
        pass


def do_task(task):
    """Entry point for AURA: runs in the background; speaks the result when done."""
    SESSION.submit(task, _say)
    return "On it." + ("" if SESSION.memory else f" (Press {STOP_HOTKEY.title()} any time to stop me.)")


def describe():
    """For the intent classifier: the last thing AURA did on the PC (so follow-ups stack)."""
    if not SESSION.memory:
        return ""
    return f"AURA has been operating the PC (last request: \"{SESSION.memory[-1]['task']}\")"
