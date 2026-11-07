# spatial_window.py - AURA SPATIAL on the PC: the same 3D explorer as the phone / iPad (mobile/src/screens/tools/spatial.js),
# opened in its own Microsoft Edge app window (Edge gives it WebGL + the webcam for hand control). It signs in with this run's
# local key (phone_bridge.PC_KEY - only accepted from this PC, never through Tailscale). Close the window and it's gone.
import os
import re
import shutil
import subprocess
import urllib.parse

AURA_DIR = os.path.dirname(os.path.abspath(__file__))
PROFILE = os.path.join(AURA_DIR, "browser_data", "spatial")        # its own Edge profile (keeps the camera permission)

_VIEW_WORDS = re.compile(r"^(please |aura,? |now |ok |and )*(rotate|turn|spin|tilt|zoom|explode|reassemble|put it back|hide|isolate|highlight|focus|"
                         r"show (me )?(the|its|only|just|everything|all)|make (it|the)|x-?ray|wireframe|cut|slice|label|reset (the )?view|compare|"
                         r"what am i looking at|what is (this|that) part|stop spinning|animate|front view|back view|top view|side view)\b", re.I)


def _edge():
    for p in (os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
              os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
              os.path.expandvars(r"%LocalAppData%\Microsoft\Edge\Application\msedge.exe")):
        if os.path.isfile(p):
            return p
    return shutil.which("msedge")


def active():
    """Is the PC's spatial window open right now (its live connection to AURA is up)?"""
    import phone_bridge
    return phone_bridge._connected.get("pc-window", 0) > 0


def wants(text, from_phone=False):
    """A command for the open 3D view, said to AURA on the PC ("rotate it", "show me the left ventricle")."""
    return not from_phone and active() and bool(_VIEW_WORDS.search(str(text or "").strip()))


def open_view(query=None):
    import phone_bridge
    if active():
        phone_bridge.emit("do", action="tool", tool="spatial", arg={"query": query} if query else {}, device="pc-window")
        return True
    url = f"http://127.0.0.1:{phone_bridge.PORT}/?pc={phone_bridge.PC_KEY}&spatial={urllib.parse.quote(query or '')}"
    edge = _edge()
    if edge:
        os.makedirs(PROFILE, exist_ok=True)
        subprocess.Popen([edge, f"--app={url}", f"--user-data-dir={PROFILE}", "--window-size=1440,920", "--no-first-run",
                          "--no-default-browser-check", "--autoplay-policy=no-user-gesture-required"],
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    else:
        import webbrowser
        webbrowser.open(url)
    return True


def open_tool(tool, size=(1440, 920), always=False):
    """Opens one AURA tool (e.g. 'air') in its own Edge app window on this PC. always=True keeps it tracking in the background."""
    import phone_bridge
    url = f"http://127.0.0.1:{phone_bridge.PORT}/?pc={phone_bridge.PC_KEY}&tool={urllib.parse.quote(tool)}"
    edge = _edge()
    flags = ["--disable-background-timer-throttling", "--disable-renderer-backgrounding", "--disable-backgrounding-occluded-windows"] if always else []
    if edge:
        prof = os.path.join(AURA_DIR, "browser_data", "aura-" + tool)
        os.makedirs(prof, exist_ok=True)
        subprocess.Popen([edge, f"--app={url}", f"--user-data-dir={prof}", f"--window-size={size[0]},{size[1]}", "--no-first-run",
                          "--no-default-browser-check", *flags], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    else:
        import webbrowser
        webbrowser.open(url)
    return True


def handle(user_text):
    """'Bring up X' / a view command, from the PC or a phone. Returns what AURA says."""
    import phone_bridge
    if phone_bridge.phone_origin():
        device = getattr(phone_bridge._local, "device", None)
        phone_bridge.emit("do", action="tool", tool="spatial", arg={"query": user_text}, device=device)
        return "On it - bringing it up in Spatial."
    if active():
        phone_bridge.emit("do", action="tool", tool="spatial", arg={"query": user_text}, device="pc-window")
        return "On it."
    open_view(user_text)
    return "Opening Spatial - bringing it up now."
