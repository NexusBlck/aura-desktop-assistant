# voice.py
# AURA's ears and mouth.
#
# HEARING: your speech is captured from the mic and transcribed by Whisper
# (whisper-large-v3-turbo on Groq - the most accurate free recogniser, ~1 s),
# primed with the words you actually use (VOCAB_WORDS in .env). Whisper's
# known "hallucinations" on background noise are filtered out. While AURA is
# talking, only "stop / wait" is listened for, with the free Google recogniser, so
# AURA's own voice never uses up Whisper's daily allowance (2,000 a day).
#
# SPEAKING: Orpheus on Groq (very human: breathing, natural pauses) when it's
# available on the account, otherwise Microsoft's natural neural voices via
# edge-tts (VOICE_NAME in config.py). Long replies are split into sentences and
# the next one is prepared while the current one plays, so AURA starts talking
# sooner. Text is cleaned up for speech (no reading out asterisks or URLs).
# Supports being interrupted mid-speech.
import asyncio
import os
import queue
import re
import tempfile
import threading
import time

import edge_tts
import pygame
import requests
import speech_recognition as sr

import face_window
import config
from config import MIC_DEVICE_INDEX, VOICE_NAME

STT_MODEL = getattr(config, "STT_MODEL", "whisper-large-v3-turbo")
STT_LANGUAGE = getattr(config, "VOICE_LANGUAGE", "en")          # None = auto-detect (for mixed Urdu/English)
VOICE_ENGINE = getattr(config, "VOICE_ENGINE", "auto")           # "auto" (Orpheus if available), "orpheus" or "edge"
ORPHEUS_VOICE = getattr(config, "ORPHEUS_VOICE", "")             # "" = first voice Groq offers
WHISPER_DAILY_CAP = 1800                                         # keep a margin under Groq's 2,000/day

# Words Whisper should expect - names and terms it would otherwise mishear.
# (Never put a wake phrase like "Hey AURA" in here: on silence/noise Whisper tends to echo its
# prompt back, which made background noise wake AURA every few seconds.)
VOCAB_PROMPT = ("Assistant named AURA. " + (config.USER_NAME + ", " if getattr(config, "USER_NAME", "") else "")
                + (config.VOCAB_WORDS.rstrip(", ") + ", " if getattr(config, "VOCAB_WORDS", "") else "")
                + "Spotify, YouTube, Roblox, VS Code, Obsidian, Gmail, WhatsApp.")
# What Whisper sometimes "hears" in silence or noise - never real commands.
HALLUCINATIONS = re.compile(r"^\s*(thank(s| you)( so much)?( for watching| for listening)?|you|bye|"
                            r"subtitles? by .*|please subscribe.*|\.+|okay\.?|so\.?|um+|uh+|hmm+)\s*[.!?]*\s*$", re.I)

recognizer = sr.Recognizer()
microphone = sr.Microphone(device_index=MIC_DEVICE_INDEX)
recognizer.pause_threshold = 0.8             # a short pause ends the sentence...
recognizer.non_speaking_duration = 0.5
recognizer.dynamic_energy_threshold = True   # ...and the mic adapts to background noise
recognizer.dynamic_energy_adjustment_damping = 0.2
recognizer.dynamic_energy_ratio = 1.6

# Only one thing can use the microphone at a time - without this, the
# background wake-word listener and an interactive flow (like the journal
# or a confirmation question) can collide and crash.
_mic_lock = threading.Lock()

# Ambient-noise calibration takes ~1.5s, so it happens on the first listen()
# (already on a background thread) instead of blocking AURA's startup.
_calibrated = False
_whisper_used = {"day": "", "n": 0}


def _ensure_calibrated(source):
    global _calibrated
    if not _calibrated:
        recognizer.adjust_for_ambient_noise(source, duration=1.5)
        _calibrated = True
        print(f"(mic calibrated - energy threshold: {recognizer.energy_threshold:.0f})")


pygame.mixer.init()
_TEMP_DIR = tempfile.gettempdir()

# Shared state so other code (main.py's input threads) can tell when AURA is
# talking, and can interrupt it.
speaking_event = threading.Event()   # set while AURA is actively speaking
interrupt_event = threading.Event()  # set to cut off the current speech


# ------------------------------------------------------------------ hearing
def _whisper_budget_ok():
    today = time.strftime("%Y-%m-%d")
    if _whisper_used["day"] != today:
        _whisper_used.update(day=today, n=0)
    return _whisper_used["n"] < WHISPER_DAILY_CAP


def _whisper(audio):
    """Transcribes with Groq Whisper. Returns text, '' for noise/silence, or None if Whisper is unavailable."""
    if not _whisper_budget_ok():
        return None
    wav = audio.get_wav_data(convert_rate=16000, convert_width=2)
    return transcribe_bytes(wav, "speech.wav", "audio/wav")


def transcribe_bytes(data_bytes, filename="speech.wav", content_type="audio/wav", language=None):
    """Whisper on an audio file's bytes (the PC mic, or a clip recorded on AURA Mobile).
    language: None = the usual setting, "auto" = detect it (AURA Mobile's interpreter).
    Returns text, '' for noise/silence, or None if Whisper is unavailable."""
    if not _whisper_budget_ok():
        return None
    data = {"model": STT_MODEL, "temperature": "0", "response_format": "verbose_json"}
    if language != "auto":
        data["prompt"] = VOCAB_PROMPT
        if language or STT_LANGUAGE:
            data["language"] = language or STT_LANGUAGE
    try:
        r = requests.post(f"{config.LLM_BASE_URL}/audio/transcriptions",
                          headers={"Authorization": f"Bearer {config.LLM_API_KEY}"},
                          files={"file": (filename, data_bytes, content_type)}, data=data, timeout=30)
    except requests.RequestException as e:
        print(f"(whisper unreachable: {e})")
        return None
    _whisper_used["n"] += 1
    if r.status_code == 429:
        _whisper_used["n"] = WHISPER_DAILY_CAP     # out of allowance - use the fallback for now
        return None
    if not r.ok:
        print(f"(whisper error {r.status_code}: {r.text[:160]})")
        return None
    out = r.json()
    # Drop segments Whisper itself thinks are silence/noise.
    # The interpreter ("auto") hears short phrases and less common languages: keep anything that isn't silence.
    min_logprob = -2.5 if language == "auto" else -1.2
    segs = [s for s in out.get("segments") or []
            if s.get("no_speech_prob", 0) < 0.6 and s.get("avg_logprob", 0) > min_logprob]
    text = " ".join(s.get("text", "").strip() for s in segs).strip() if out.get("segments") else (out.get("text") or "").strip()
    if not text or HALLUCINATIONS.match(text) or _echoes_prompt(text):
        return ""
    # A word or two from a noisy clip is usually a guess - only keep it if Whisper is confident.
    if len(text.split()) <= 2 and out.get("segments") and language != "auto":
        if (max(s.get("no_speech_prob", 0) for s in out["segments"]) > 0.3
                or min(s.get("avg_logprob", 0) for s in out["segments"]) < -0.7):
            return ""
    return text


_PROMPT_WORDS = set(re.findall(r"[a-z]+", VOCAB_PROMPT.lower()))


def _echoes_prompt(text):
    """True if Whisper just repeated its vocabulary hint (what it does on noise)."""
    words = re.findall(r"[a-z]+", text.lower())
    return len(words) >= 3 and sum(w in _PROMPT_WORDS for w in words) / len(words) > 0.8


def _google(audio):
    try:
        return recognizer.recognize_google(audio)
    except sr.UnknownValueError:
        return ""
    except sr.RequestError as e:
        print(f"(speech recognition service error: {e})")
        return ""


def _duration(audio):
    return len(audio.frame_data) / float(audio.sample_rate * audio.sample_width)


def listen(timeout=None, phrase_time_limit=15, quick=False):
    """Listen through the mic once. Returns the recognised text, or None if nothing
    was understood (silence, background noise...). quick=True uses the free Google
    recogniser (used for 'stop' while AURA talks, so its own voice never costs Whisper calls).
    While AURA handles a command from AURA Mobile, the answer comes from the phone instead."""
    import phone_bridge
    if phone_bridge.phone_origin() and not quick:
        return phone_bridge.wait_reply(timeout)
    with _mic_lock:
        with microphone as source:
            _ensure_calibrated(source)
            try:
                audio = recognizer.listen(source, timeout=timeout, phrase_time_limit=phrase_time_limit)
            except sr.WaitTimeoutError:
                return None

    if _duration(audio) < 0.35:      # a click or a cough - not speech
        return None
    if quick:
        return _google(audio) or None
    text = _whisper(audio)
    if text is None:                 # Whisper unavailable / over the daily allowance
        text = _google(audio)
    return text or None


# ------------------------------------------------------------------ speaking
_URL = re.compile(r"https?://\S+|www\.\S+")


def _speakable(text):
    """Cleans text so it sounds natural spoken: no markdown symbols or long URLs."""
    text = _URL.sub("a link", text or "")
    text = re.sub(r"[*_#`>|~]+", "", text)
    text = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", text)          # [label](link) -> label
    text = re.sub(r"^\s*[-•]\s+", "", text, flags=re.M)        # bullet markers
    text = re.sub(r"\s*\n+\s*", ". ", text).replace("..", ".")
    return re.sub(r"\s{2,}", " ", text).strip()


def _chunks(text, limit=220):
    """Sentence-sized pieces (so speech starts quickly and long replies stream)."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    out, cur = [], ""
    for s in sentences:
        if not s:
            continue
        if cur and len(cur) + len(s) + 1 > limit:
            out.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}".strip()
        while len(cur) > limit:                       # one very long sentence: break at a comma/space
            cut = max(cur.rfind(", ", 0, limit), cur.rfind(" ", 0, limit))
            cut = cut if cut > 40 else limit
            out.append(cur[:cut].strip(" ,"))
            cur = cur[cut:].strip(" ,")
    if cur:
        out.append(cur)
    return out


_orpheus = {"ok": VOICE_ENGINE in ("auto", "orpheus"), "voice": ORPHEUS_VOICE, "warned": False}


def _orpheus_tts(text, path):
    """Groq Orpheus (very human). Returns True on success; disables itself for the session if unavailable."""
    if not _orpheus["ok"]:
        return False
    body = {"model": "canopylabs/orpheus-v1-english", "input": text, "response_format": "wav"}
    if _orpheus["voice"]:
        body["voice"] = _orpheus["voice"]
    for _attempt in range(2):
        try:
            r = requests.post(f"{config.LLM_BASE_URL}/audio/speech",
                              headers={"Authorization": f"Bearer {config.LLM_API_KEY}"}, json=body, timeout=30)
        except requests.RequestException:
            return False
        if r.ok and r.content:
            with open(path, "wb") as f:
                f.write(r.content)
            return True
        msg = r.text
        # No/invalid voice given: pick one from the list Groq sends back, and retry once.
        m = re.search(r"voice.*?(?:one of|must be|valid)[^\[]*\[([^\]]+)\]", msg, re.I | re.S)
        if r.status_code == 400 and m and _attempt == 0:
            voices = [v.strip(" '\"") for v in m.group(1).split(",") if v.strip(" '\"")]
            if voices:
                _orpheus["voice"] = body["voice"] = voices[0]
                print(f"(orpheus: using voice '{voices[0]}' - options: {', '.join(voices)})")
                continue
        if "terms" in msg.lower() or r.status_code in (401, 403, 404):
            _orpheus["ok"] = False      # not enabled on this account - use the Microsoft voice
            if not _orpheus["warned"]:
                print("(Orpheus voice not available yet - accept its terms at "
                      "https://console.groq.com/playground?model=canopylabs%2Forpheus-v1-english . Using edge-tts.)")
                _orpheus["warned"] = True
        elif r.status_code == 429:
            pass                         # rate limited right now - this sentence uses edge-tts
        return False
    return False


async def _edge_save(text, voice, path):
    await edge_tts.Communicate(text, voice).save(path)


def _synth(text, n):
    """Makes the audio for one chunk. Returns a file path, or None."""
    base = os.path.join(_TEMP_DIR, f"aura_speech_{n % 4}")
    if _orpheus_tts(text, base + ".wav"):
        return base + ".wav"
    try:
        asyncio.run(_edge_save(text, VOICE_NAME, base + ".mp3"))
        return base + ".mp3"
    except Exception as e:
        print(f"(voice output error: {e})")
        return None


def synth_bytes(text):
    """AURA's voice for one piece of text, as (audio bytes, content type) - played on AURA Mobile.
    Uses its own temp file so it never clashes with what the PC is playing."""
    text = _speakable(text)
    if not text:
        return None, None
    base = os.path.join(_TEMP_DIR, f"aura_phone_{threading.get_ident()}")
    try:
        if _orpheus_tts(text, base + ".wav"):
            path, ctype = base + ".wav", "audio/wav"
        else:
            asyncio.run(_edge_save(text, VOICE_NAME, base + ".mp3"))
            path, ctype = base + ".mp3", "audio/mpeg"
        with open(path, "rb") as f:
            return f.read(), ctype
    except Exception as e:
        print(f"(phone voice error: {e})")
        return None, None


_speak_lock = threading.Lock()


def speak(text):
    """Speak text out loud. Can be interrupted by setting interrupt_event (done
    automatically by voice/text input while speaking). One reply at a time: a
    reminder firing while AURA is already talking waits its turn."""
    with _speak_lock:
        _speak(text)


def _speak(text):
    # Everything AURA says goes to the Conversation Log in the AURA window.
    face_window.add_log("aura", text)
    import phone_bridge
    if phone_bridge.phone_origin():
        return      # asked from AURA Mobile - the phone reads it out, the PC stays quiet
    pieces = _chunks(_speakable(text))
    if not pieces:
        return
    ready = queue.Queue()

    def producer():   # prepares the next sentence while the current one plays
        for i, p in enumerate(pieces):
            if interrupt_event.is_set():
                break
            ready.put(_synth(p, i))
        ready.put(StopIteration)

    interrupt_event.clear()
    threading.Thread(target=producer, daemon=True).start()
    speaking_event.set()
    face_window.start_talking()
    try:
        while True:
            path = ready.get()
            if path is StopIteration or interrupt_event.is_set():
                break
            if not path:
                continue
            try:
                pygame.mixer.music.load(path)
                pygame.mixer.music.play()
                while pygame.mixer.music.get_busy():
                    if interrupt_event.is_set():
                        pygame.mixer.music.stop()
                        break
                    pygame.time.Clock().tick(30)
            finally:
                try:
                    pygame.mixer.music.unload()
                except Exception:
                    pass
    except Exception as e:
        print(f"(voice output error: {e})")
    finally:
        face_window.stop_talking()
        speaking_event.clear()
