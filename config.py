# config.py
# AURA's settings. This file contains NO secrets and is safe to commit.
#
# Your own values (API keys, name, timezone...) go in a file called .env next to this one.
# Copy .env.example to .env and fill it in - .env is listed in .gitignore, so it never gets
# uploaded. Real environment variables override .env, and both override the defaults below.
import os as _os

_HERE = _os.path.dirname(_os.path.abspath(__file__))


def _load_dotenv(path):
    """Minimal .env reader (KEY=value per line, # comments, optional quotes). No extra dependency."""
    values = {}
    try:
        with open(path, encoding="utf-8-sig") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key, val = key.strip(), val.strip()
                if key.startswith("export "):
                    key = key[7:].strip()
                if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                    val = val[1:-1]
                elif " #" in val:
                    val = val.split(" #", 1)[0].rstrip()
                if key:
                    values[key] = val
    except FileNotFoundError:
        pass
    return values


_DOTENV = _load_dotenv(_os.environ.get("AURA_ENV_FILE") or _os.path.join(_HERE, ".env"))


def _raw(name):
    v = _os.environ.get(name)
    return v if v is not None else _DOTENV.get(name)


def _str(name, default=""):
    v = _raw(name)
    return default if v is None or v == "" else v


def _bool(name, default):
    v = _raw(name)
    if v is None or v.strip() == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _int(name, default):
    v = _raw(name)
    try:
        return int(v) if v not in (None, "") else default
    except ValueError:
        print(f"(config: {name}={v!r} is not a number - using {default!r})")
        return default


def _list(name, default):
    """Semicolon-separated list, e.g. EXTRA_SEARCH_FOLDERS=D:\\Uni;D:\\Work"""
    v = _raw(name)
    if v is None or v.strip() == "":
        return list(default)
    return [p.strip() for p in v.split(";") if p.strip()]


# ---- AI brain: Groq (OpenAI-compatible API). REQUIRED - free key: https://console.groq.com/keys ----
LLM_API_KEY = _str("LLM_API_KEY")
LLM_BASE_URL = _str("LLM_BASE_URL", "https://api.groq.com/openai/v1")
LLM_MODEL = _str("LLM_MODEL", "openai/gpt-oss-120b")
FALLBACK_MODELS = _list("FALLBACK_MODELS", ["openai/gpt-oss-20b", "qwen/qwen3.8-27b"])

# ---- Optional smarter brain: Google Gemini (free key: https://aistudio.google.com/apikey) ----
GEMINI_API_KEY = _str("GEMINI_API_KEY")
GEMINI_MODELS = _list("GEMINI_MODELS", ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.5-flash-lite"])

ASSISTANT_NAME = _str("ASSISTANT_NAME", "AURA")

# ---- About you (personalises answers, email drafts and speech recognition) ----
USER_NAME = _str("USER_NAME")          # e.g. "Sam"
USER_ABOUT = _str("USER_ABOUT")        # e.g. "a computer science student who plays football"
VOCAB_WORDS = _str("VOCAB_WORDS")      # names/terms you say often, comma-separated

LOCAL_TIMEZONE = _str("LOCAL_TIMEZONE", "UTC")   # IANA name, e.g. "Europe/London"
LOCATION = _str("LOCATION")                      # fallback city if IP-based detection fails, e.g. "London, UK"

# ---- Optional services ----
TAVILY_API_KEY = _str("TAVILY_API_KEY")          # live web search - https://tavily.com
SPOTIFY_CLIENT_ID = _str("SPOTIFY_CLIENT_ID")    # https://developer.spotify.com/dashboard
SPOTIFY_CLIENT_SECRET = _str("SPOTIFY_CLIENT_SECRET")
FIREBASE_API_KEY = _str("FIREBASE_API_KEY")      # optional sign-in screen; empty = single-user mode, no sign-in
HF_TOKEN = _str("HF_TOKEN")                      # Hugging Face (AURA Studio image editing / video)
SKETCHFAB_TOKEN = _str("SKETCHFAB_TOKEN")        # more 3D models in AURA Spatial
TELEGRAM_BOT_TOKEN = _str("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = _str("TELEGRAM_CHAT_ID")
ADB_PATH = _str("ADB_PATH", "adb")               # Android phone control over USB debugging

# ---- Voice ----
MIC_DEVICE_INDEX = _int("MIC_DEVICE_INDEX", None)   # None = system default; run list_mic.py to see indexes
VOICE_ENGINE = _str("VOICE_ENGINE", "edge")         # "edge", "orpheus" or "auto"
ORPHEUS_VOICE = _str("ORPHEUS_VOICE")
VOICE_NAME = _str("VOICE_NAME", "en-US-EmmaMultilingualNeural")   # run list_voices.py for all voices
STT_MODEL = _str("STT_MODEL", "whisper-large-v3-turbo")
VOICE_LANGUAGE = None if _str("VOICE_LANGUAGE", "en").lower() in ("auto", "none") else _str("VOICE_LANGUAGE", "en")
PUSH_TO_TALK_HOTKEY = _str("PUSH_TO_TALK_HOTKEY", "ctrl+alt+a")
EXPLAIN_HOTKEY = _str("EXPLAIN_HOTKEY", "ctrl+alt+e")

# Always-on wake word ("AURA, ..."). OFF by default: when it's on, everything the microphone picks
# up is sent to the speech-to-text service (Groq Whisper, or Google's free recogniser) just to check
# for the wake word. Push-to-talk (PUSH_TO_TALK_HOTKEY) and typing always work.
WAKE_WORD_ENABLED = _bool("WAKE_WORD_ENABLED", False)

# ---- Files ----
EXTRA_SEARCH_FOLDERS = _list("EXTRA_SEARCH_FOLDERS", [])
APP_INDEX_MAX_AGE_HOURS = _int("APP_INDEX_MAX_AGE_HOURS", 24)   # legacy app_launcher.py: rebuild its app list after this long
KNOWLEDGE_FOLDERS = _list("KNOWLEDGE_FOLDERS",
                          [_os.path.join(_os.path.expanduser("~"), "Documents", "AURA Knowledge")])

# ---- Phone app (see mobile/SETUP.md) ----
PHONE_BRIDGE_PORT = _int("PHONE_BRIDGE_PORT", 8740)
# LINK: lets a paired phone see this PC's screen and control it. Off unless you turn it on.
PHONE_REMOTE_CONTROL = _bool("PHONE_REMOTE_CONTROL", False)

# ---- Safety ----
# SAFE_MODE asks before AURA writes and runs new code (app builder) or force-closes an app, and stops
# requests arriving from the public internet (Apple Watch via Tailscale Funnel) from controlling the PC.
# Shutdown/restart always ask. Turn this off only if you understand the risks (see SECURITY.md).
SAFE_MODE = _bool("SAFE_MODE", True)

# Developer shortcut: shows "Skip sign-in (dev)" on the sign-in screen. Leave off.
DEV_LOGIN_BYPASS = _bool("DEV_LOGIN_BYPASS", False)


def describe_user():
    """'Sam, a computer science student' / 'Sam' / 'the user' - how AI prompts refer to you."""
    if USER_NAME and USER_ABOUT:
        return f"{USER_NAME}, {USER_ABOUT}"
    return USER_NAME or USER_ABOUT or "the user"


def missing_setup():
    """What must be configured before AURA can start ('' when it's ready)."""
    if not LLM_API_KEY:
        return ("AURA needs an AI key to start.\n\n"
                "1. Copy .env.example to a new file called .env (in the AURA folder)\n"
                "2. Put your free Groq key (https://console.groq.com/keys) after LLM_API_KEY=\n"
                "3. Start AURA again.")
    return ""
