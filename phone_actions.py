# phone_actions.py
# AURA Mobile as its own device: things you ask on the phone happen ON THE PHONE; your PC is only
# used when you say so ("... on my PC" / "on my laptop").
#
#   * Websites, YouTube, Spotify, apps, web briefings, PRISMA diagrams, found files -> opened on the
#     phone (AURA catches the PC's webbrowser.open / os.startfile while it handles a phone request).
#   * Camera, dashboard, CDC WONDER, journal -> the phone's own screens.
#   * Things that can only happen on a PC (typing, mouse, controlling the screen, PBL / portal
#     agents, building apps, shutdown...) -> AURA says so and offers a one-tap "do it on my PC".
#   * iPhone limits: no app may see or control the iPhone's screen or other apps. Instead: open
#     apps/sites, run your iOS Shortcuts ("run shortcut Morning"), or send AURA a screenshot.
import os
import re
import threading
import urllib.parse
import webbrowser

import phone_bridge

ON_PC_RE = re.compile(r"[\s,]*\b(?:on|in|at|using|with|from|via)\s+(?:my|the|our)\s+(?:pc|computer|laptop|desktop|windows)\b[\s,.!?]*",
                      re.IGNORECASE)
ON_PHONE_RE = re.compile(r"[\s,]*\b(?:on|in|using)\s+(?:my|the)\s+(?:phone|iphone|mobile)\b[\s,.!?]*", re.IGNORECASE)


def split_target(text):
    """'open spotify on my laptop' -> ('open spotify', True). Phone is the default."""
    if ON_PC_RE.search(text):
        return ON_PC_RE.sub(" ", text).strip(" ,.") or text, True
    return ON_PHONE_RE.sub(" ", text).strip(" ,.") or text, False


# ------------------------------------------------------------------ telling the phone to do things
def _do(**data):
    phone_bridge.emit("do", **data)


def _open_on_phone(url, label=""):
    _do(action="open", url=url, label=label or _label(url))


def _label(url):
    if url.startswith("/v/"):
        return urllib.parse.unquote(url.rsplit("/", 1)[-1]) or "file"
    m = re.match(r"^[a-z][\w+.-]*://(?:www\.)?([^/?#]+)", url, re.I)
    return m.group(1) if m else url.split(":")[0]


def _phone_url(url):
    """A URL the PC would have opened -> one the phone can open."""
    url = str(url)
    m = re.match(r"^spotify:(track|album|artist|playlist|episode|show):(\w+)$", url)
    if m:                                    # universal link - opens the Spotify app on the phone
        return f"https://open.spotify.com/{m.group(1)}/{m.group(2)}"
    m = re.match(r"^https?://(?:localhost|127\.0\.0\.1):\d+/briefings/([^?#]+)", url)
    if m:                                    # a web briefing AURA built
        return phone_bridge._ticket("briefings", urllib.parse.unquote(m.group(1)))
    if url.startswith("file:"):
        path = urllib.parse.unquote(url.replace("file:///", "").replace("file://", ""))
        return phone_bridge.ticket_for_path(os.path.normpath(path))
    if os.path.isabs(url) and os.path.isfile(url):          # a plain file path
        return phone_bridge.ticket_for_path(url)
    return url


# ------------------------------------------------------------------ catching the PC's "open" calls
_real_open = webbrowser.open
_real_startfile = os.startfile


def _browser_open(url, new=0, autoraise=True):
    if phone_bridge.phone_target():
        link = _phone_url(url)
        if link:
            _open_on_phone(link)
            return True
    return _real_open(url, new, autoraise)


def _startfile(path, *args, **kwargs):
    if phone_bridge.phone_target():
        path = os.path.normpath(str(path))
        results = os.path.realpath(phone_bridge._roots()["search"])
        if os.path.isdir(path):
            if os.path.realpath(path).startswith(results + os.sep):
                _do(action="tool", tool="files", arg={"kind": "search", "path": os.path.basename(path),
                                                      "title": os.path.basename(path).replace(" (AURA search)", "")})
            return None
        link = phone_bridge.ticket_for_path(path)
        if link:
            _open_on_phone(link, os.path.basename(path))
        return None
    return _real_startfile(path, *args, **kwargs)


_installed = False


def install():
    """Called once at startup (main.py)."""
    global _installed
    if _installed:
        return
    _installed = True
    webbrowser.open = _browser_open
    os.startfile = _startfile


# ------------------------------------------------------------------ actions
# Only make sense on a computer (or would act on your PC): need "on my PC" when asked from the phone.
PC_ONLY = {
    "close_app": "close apps",
    "type_text": "type for you",
    "hotkey": "press keyboard shortcuts",
    "mouse_action": "use the mouse",
    "screen_action": "see and control the screen",
    "open_and_type": "open an app and type in it",
    "write_note": "write in Notepad",
    "play_roblox": "launch Roblox games",
    "explain_error": "read an error you copied on the PC",
    "build_project": "build apps",
    "modify_project": "change your apps",
    "web_task": "use AURA's browser for that",
    "phone_action": "control an Android phone over ADB",
    "shutdown": "shut down",
    "restart": "restart",
}

IPHONE_CANT = {
    "screen_action": "iOS doesn't let any app see or control your iPhone's screen. Take a screenshot and send it to me "
                     "in VISION (Photo) and I'll read it",
    "volume": "iOS doesn't let apps change the volume - use the side buttons, or make a Shortcut and say "
              "'run shortcut <its name>'",
    "screenshot": "iOS doesn't let apps take screenshots - press the side button and volume up together, then send it "
                  "to me in VISION if you want me to look at it",
    "close_app": "iOS doesn't let apps close other apps",
    "type_text": "iOS doesn't let apps type into other apps",
    "hotkey": "iPhones don't have keyboard shortcuts I can press",
    "mouse_action": "iPhones don't have a mouse I can move",
}

# iPhone apps AURA can open (their official URL schemes / universal links).
APP_LINKS = {
    "whatsapp": "whatsapp://", "instagram": "instagram://", "youtube": "youtube://", "spotify": "spotify:",
    "maps": "maps://", "apple maps": "maps://", "google maps": "comgooglemaps://", "gmail": "googlegmail://",
    "calendar": "calshow://", "photos": "photos-redirect://", "notes": "mobilenotes://", "messages": "sms:",
    "phone": "tel:", "mail": "message://", "facetime": "facetime://", "music": "music://", "apple music": "music://",
    "shortcuts": "shortcuts://", "telegram": "tg://", "snapchat": "snapchat://", "twitter": "twitter://", "x": "twitter://",
    "linkedin": "linkedin://", "discord": "discord://", "netflix": "nflx://", "reddit": "reddit://", "uber": "uber://",
    "zoom": "zoomus://", "teams": "msteams://", "microsoft teams": "msteams://", "outlook": "ms-outlook://",
    "word": "ms-word://", "excel": "ms-excel://", "powerpoint": "ms-powerpoint://", "google drive": "googledrive://",
    "notion": "notion://", "obsidian": "obsidian://", "roblox": "roblox://", "safari": "https://www.google.com",
    "chrome": "googlechrome://", "tiktok": "https://www.tiktok.com", "facebook": "fb://", "pinterest": "pinterest://",
    "threads": "barcelona://", "chatgpt": "https://chatgpt.com", "claude": "https://claude.ai", "files": "shareddocuments://",
    "app store": "itms-apps://", "camera": "@vision",
}

SHORTCUT_RE = re.compile(r"^\s*(?:please\s+)?(?:run|start|do|launch|play)\s+(?:my\s+|the\s+)?shortcut\s+(?:called\s+|named\s+)?[\"']?(.+?)[\"']?\s*$", re.I)


def _offer_pc(user_text, what):
    _do(action="pc_offer", command=user_text)
    return (f"That one needs your PC - I'd {what} there. Tap 'Do it on my PC' (or say it again with "
            f"'on my PC') and I'll do it on your computer.")


def _open_app(name):
    key = re.sub(r"\b(app|application|the)\b", "", (name or "").lower()).strip(" .")
    link = APP_LINKS.get(key)
    if not link:
        # partial match ("open whatsapp messenger")
        link = next((v for k, v in APP_LINKS.items() if len(k) > 2 and re.search(rf"\b{re.escape(k)}\b", key)), None)
    if link == "@vision":
        _do(action="tool", tool="vision")
        return "Opening Vision mode with your camera."
    if link:
        _open_on_phone(link, name.strip().title())
        return f"Opening {name.strip()} on your phone."
    return None


def handle(intent, user_text):
    """Handles a phone-targeted request. Returns a reply, or None to let AURA handle it as usual
    (chat, questions, tasks, reminders, email, research... - those work the same anywhere)."""
    action = intent.get("action", "chat")
    target = intent.get("target", "") or ""

    import nexus
    tool = nexus.phone_tool(user_text)
    if tool:
        _do(action="tool", tool=tool)
        return "Opening " + {"nexus": "NEXUS", "nexus-live": "LIVE mode - just talk, I'm listening and watching",
                             "nexus-babel": "the interpreter", "nexus-echo": "ECHO, the recorder", "nexus-radar": "RADAR",
                             "nexus-pulse": "PULSE, the bio-scanner", "nexus-link": "LINK to your PC",
                             "nexus-protocols": "your protocols"}.get(tool, tool) + "."

    m = SHORTCUT_RE.match(user_text)
    if m:
        name = m.group(1).strip()
        _open_on_phone("shortcuts://run-shortcut?name=" + urllib.parse.quote(name), f"Shortcut: {name}")
        return f"Running your '{name}' shortcut."

    if action in IPHONE_CANT and re.search(r"\b(phone|iphone|mobile|this screen|my screen)\b", user_text, re.I):
        return IPHONE_CANT[action] + "."
    if action in PC_ONLY:
        return _offer_pc(user_text, PC_ONLY[action])
    if action in ("volume", "screenshot"):
        return IPHONE_CANT[action] + ". Say 'on my PC' if you meant your computer."

    if action == "open_app":
        reply = _open_app(target)
        if reply:
            return reply
        # not an app the phone knows - maybe it's a website
        import pc_control
        url, _title = pc_control.resolve_website(target)
        if url:
            _open_on_phone(url)
            return f"I don't know a {target} app on iPhone, so I'm opening its website."
        return (f"I can't open {target} from here - iOS only lets me open apps that support links. "
                f"Tip: make a Shortcut that opens it, then say 'run shortcut <name>'. Or say 'on my PC'.")
    if action == "open_camera":
        _do(action="tool", tool="vision")
        return "Opening Vision mode on your phone - tap anything to identify it."
    if action == "close_camera":
        _do(action="close_tool")
        return "Closed."
    if action == "cdc_wonder":
        _do(action="tool", tool="cdc")
        return "Opening the CDC WONDER extractor - it runs on your PC in the background, you'll see progress here."
    return None      # everything else: AURA's normal handling; anything it opens lands on the phone
