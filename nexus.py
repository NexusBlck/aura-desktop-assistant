# nexus.py
# The brain behind AURA Mobile's NEXUS modules (mobile/src/screens/tools/nexus/):
#   LIVE       hands-free conversation: AURA hears you and sees through the camera, and can act
#   BABEL      a live two-way interpreter (each side speaks their own language)
#   ECHO       records lectures / meetings -> transcript, summary, notes, flashcards; ask them later
#   RADAR      what's around you (OpenStreetMap), where you are, "ask about here"
#   PULSE      heart-rate / breathing / stress readings measured on the phone, kept here
#   LINK       see and control this PC from the phone: screen, mouse, keyboard, clipboard, media keys
#   PROTOCOLS  one word runs a chain of AURA commands; MISSION turns a goal into a protocol
# Everything heavy (speech, AI, the PC's screen and input) happens here; the phone does the sensing.
import base64
import datetime
import json
import math
import os
import re
import secrets
import threading
import time

from ai import ask_llm, extract_json

AURA_DIR = os.path.dirname(os.path.abspath(__file__))
ECHO_DIR = os.path.join(os.path.expanduser("~"), "Documents", "AURA Echo")
BIO_FILE = os.path.join(AURA_DIR, "aura_bio.json")
PROTOCOLS_FILE = os.path.join(AURA_DIR, "aura_protocols.json")
_lock = threading.RLock()


def _b64(data):
    data = str(data or "")
    if "," in data[:80]:
        data = data.split(",", 1)[1]
    try:
        return base64.b64decode(data)
    except Exception:
        return b""


def _ext(mime):
    return {"audio/wav": "wav", "audio/mp4": "m4a", "audio/webm": "webm", "audio/ogg": "ogg",
            "audio/mpeg": "mp3"}.get((mime or "").split(";")[0].strip(), "wav")


def _hear(audio_b64, mime, language=None):
    import voice
    raw = _b64(audio_b64)
    if len(raw) < 2000:
        return ""
    return voice.transcribe_bytes(raw, f"speech.{_ext(mime)}", mime or "audio/wav", language=language) or ""


def _json_llm(prompt, max_tokens=900, temperature=0.3):
    raw = ask_llm([{"role": "user", "content": prompt}], temperature=temperature, reasoning_effort="low", max_tokens=max_tokens)
    try:
        data = extract_json(raw)
        return data if isinstance(data, dict) else {"text": raw}
    except ValueError:
        return {"text": raw.strip()}


# ------------------------------------------------------------------ voices
# A native voice per language, so a translation sounds like a local speaker (edge-tts, free).
VOICES = {
    "English": None, "Urdu": "ur-PK-UzmaNeural", "Arabic": "ar-SA-ZariyahNeural", "Hindi": "hi-IN-SwaraNeural",
    "French": "fr-FR-DeniseNeural", "Spanish": "es-ES-ElviraNeural", "German": "de-DE-KatjaNeural",
    "Chinese": "zh-CN-XiaoxiaoNeural", "Japanese": "ja-JP-NanamiNeural", "Korean": "ko-KR-SunHiNeural",
    "Turkish": "tr-TR-EmelNeural", "Persian": "fa-IR-DilaraNeural", "Russian": "ru-RU-SvetlanaNeural",
    "Italian": "it-IT-ElsaNeural", "Portuguese": "pt-BR-FranciscaNeural", "Bengali": "bn-BD-NabanitaNeural",
    "Malay": "ms-MY-YasminNeural", "Indonesian": "id-ID-GadisNeural",
}


def speech(text, language="English"):
    """(base64 audio, content type) of AURA saying text - in that language's native voice."""
    import voice
    text = voice._speakable(text or "")[:1200]
    if not text:
        return None, None
    name = VOICES.get(language)
    if not name:
        audio, ctype = voice.synth_bytes(text)
    else:
        import asyncio
        import edge_tts
        path = os.path.join(voice._TEMP_DIR, f"aura_nexus_{threading.get_ident()}.mp3")
        try:
            asyncio.run(edge_tts.Communicate(text, name).save(path))
            with open(path, "rb") as f:
                audio, ctype = f.read(), "audio/mpeg"
        except Exception as e:
            print(f"(nexus voice error: {e})")
            audio, ctype = voice.synth_bytes(text)
    return (base64.b64encode(audio).decode() if audio else None), ctype


# ------------------------------------------------------------------ LIVE
CAN_DO = ("set reminders and alarms-style reminders, add tasks, notes, calendar events, start focus timers, "
          "do-not-disturb, play music on Spotify/YouTube, open apps and websites, search the web, write and "
          "send-ready email drafts, research (PubMed, papers), web briefings, find files, control the PC "
          "(open apps, type, screenshots, volume, shutdown), build apps, journal, log study sessions")


def live_turn(b):
    """One turn of LIVE mode: speech (+ an optional camera frame) in -> spoken answer out, and an AURA
    command when you asked for something to be done."""
    heard = _hear(b.get("audio"), b.get("mime"))
    if not heard:
        return {"heard": ""}
    history = [h for h in (b.get("history") or []) if isinstance(h, dict)][-8:]
    convo = "\n".join(f"{'User' if h.get('role') == 'user' else 'AURA'}: {str(h.get('text'))[:300]}" for h in history)
    img = str(b.get("image") or "")
    place = str(b.get("place") or "")[:200]
    import vision_lab
    prompt = f"""You are AURA, the user's personal AI, in LIVE mode: a hands-free, real-time voice conversation
{'while looking through their phone camera (the image is what the camera sees right now)' if img else '(camera off)'}.
{vision_lab._who()}
{('Where they are: ' + place) if place else ''}
Time: {datetime.datetime.now():%A %d %B %Y, %H:%M}.
Conversation so far:
{convo or '(just started)'}
User just said: "{heard}"

Reply like a brilliant, warm companion talking out loud: 1-3 short sentences, natural speech, no lists,
no markdown. If they ask about what's in view, look at the image carefully and be specific.
If they want something DONE that needs AURA's main system ({CAN_DO}), put one clear instruction for it
in "command" written as the user would say it (e.g. "remind me at 6pm to call mum"), and say you're on it.
Otherwise "command" is "". If they say goodbye / stop / end live mode, set "end" to true.
Respond ONLY with JSON: {{"answer": "...", "command": "", "end": false}}"""
    if img:
        data = vision_lab._json(img, prompt, 400)
    else:
        data = _json_llm(prompt, 400, 0.6)
    answer = str(data.get("answer") or data.get("text") or "").strip() or "Sorry, I lost that - say it again?"
    audio, ctype = speech(answer)
    return {"heard": heard, "answer": answer, "command": str(data.get("command") or "").strip()[:300],
            "end": bool(data.get("end")), "audio": audio, "ctype": ctype}


# ------------------------------------------------------------------ BABEL (interpreter)
def interpret(b):
    a, c = str(b.get("a") or "English")[:20], str(b.get("b") or "Urdu")[:20]
    heard = _hear(b.get("audio"), b.get("mime"), language="auto")
    if not heard:
        return {"heard": ""}
    data = _json_llm(f"""You are a live interpreter between a {a} speaker and a {c} speaker.
Someone just said (speech-to-text, may have small errors - it sometimes writes Urdu in Hindi/Devanagari letters
or Arabic-script languages in Latin letters): "{heard}"
Work out which of the two languages it is ({a} or {c}; if it's neither, treat it as {a}), then translate it
naturally - meaning and tone, not word-for-word - into the OTHER language. Use that language's own script.
Respond ONLY with JSON: {{"from": "{a} or {c}", "to": "the other one", "original": "cleaned-up original, in its own language's proper script",
"translation": "...", "romanized": "the translation in Latin letters if its script isn't Latin, else empty"}}""", 600, 0.2)
    to = data.get("to") if data.get("to") in (a, c) else (c if data.get("from") == a else a)
    frm = c if to == a else a
    translation = str(data.get("translation") or "").strip()
    audio, ctype = speech(translation, to) if translation else (None, None)
    return {"heard": heard, "original": data.get("original") or heard, "from": frm, "to": to,
            "translation": translation, "romanized": data.get("romanized") or "", "audio": audio, "ctype": ctype}


# ------------------------------------------------------------------ ECHO (lectures / meetings)
def _echo_path(eid):
    return os.path.join(ECHO_DIR, re.sub(r"[^a-f0-9]", "", str(eid))[:16] + ".json")


def _echo_load(eid):
    try:
        with open(_echo_path(eid), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _echo_save(rec):
    os.makedirs(ECHO_DIR, exist_ok=True)
    tmp = _echo_path(rec["id"]) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False)
    os.replace(tmp, _echo_path(rec["id"]))


def echo_start(b):
    rec = {"id": secrets.token_hex(6), "title": (str(b.get("title") or "").strip() or
                                               f"{str(b.get('kind') or 'Lecture').title()} {datetime.datetime.now():%d %b %H:%M}")[:80],
           "kind": str(b.get("kind") or "lecture")[:20], "started": time.time(), "segments": [], "notes": None}
    _echo_save(rec)
    return {"id": rec["id"], "title": rec["title"]}


def echo_chunk(b):
    with _lock:
        rec = _echo_load(b.get("id"))
    if not rec:
        return {"error": "That recording wasn't found."}
    text = _hear(b.get("audio"), b.get("mime"), language=b.get("lang") or None)
    if text:
        with _lock:
            rec = _echo_load(b.get("id")) or rec
            rec["segments"].append({"t": float(b.get("t") or 0), "text": text})
            _echo_save(rec)
    return {"text": text}


def _transcript(rec, limit=60000):
    lines = [f"[{int(s['t'] // 60):02d}:{int(s['t'] % 60):02d}] {s['text']}" for s in rec.get("segments", [])]
    text = "\n".join(lines)
    return text if len(text) <= limit else text[:limit // 2] + "\n…\n" + text[-limit // 2:]


def echo_finish(b):
    rec = _echo_load(b.get("id"))
    if not rec:
        return {"error": "That recording wasn't found."}
    if not rec["segments"]:
        return {"error": "Nothing was heard in that recording."}
    import vision_lab
    kind = rec.get("kind", "lecture")
    extra = ('"flashcards": [{"q": "...", "a": "..."}] (6-10 high-yield cards), "exam_points": ["what is likely to be examined"],'
             if kind == "lecture" else '"decisions": ["what was decided"],')
    notes = _json_llm(f"""You are AURA. Turn this {kind} transcript into excellent notes for the user.
{vision_lab._who()}
Transcript (timestamps are mm:ss):
{_transcript(rec)}

Respond ONLY with JSON: {{"title": "a specific title", "summary": "4-6 sentence summary",
"key_points": ["the most important points, each one clear sentence"], {extra}
"action_items": ["deadlines / things to do that were mentioned"], "terms": [{{"term": "...", "meaning": "..."}}]}}""", 2200)
    rec["notes"] = notes
    if notes.get("title"):
        rec["title"] = str(notes["title"])[:80]
    rec["ended"] = time.time()
    _echo_save(rec)
    _echo_markdown(rec)
    try:                                       # the new lecture is searchable right away
        import knowledge
        threading.Thread(target=knowledge.refresh, daemon=True).start()
    except Exception:
        pass
    return echo_get(rec["id"])


def _echo_markdown(rec):
    """A readable copy next to the data, so AURA's file search (and you) can find it."""
    n = rec.get("notes") or {}
    out = [f"# {rec['title']}", f"_{datetime.datetime.fromtimestamp(rec['started']):%A %d %B %Y, %H:%M}_", ""]
    if n.get("summary"):
        out += ["## Summary", n["summary"], ""]
    for key, label in (("key_points", "Key points"), ("exam_points", "Likely exam points"), ("decisions", "Decisions"),
                       ("action_items", "To do")):
        if n.get(key):
            out += [f"## {label}"] + [f"- {x}" for x in n[key]] + [""]
    if n.get("flashcards"):
        out += ["## Flashcards"] + [f"- **{c.get('q')}** - {c.get('a')}" for c in n["flashcards"] if isinstance(c, dict)] + [""]
    out += ["## Transcript", _transcript(rec, 10 ** 7)]
    safe = re.sub(r'[\\/:*?"<>|]+', "_", rec["title"])[:70]
    try:
        with open(os.path.join(ECHO_DIR, f"{safe} ({rec['id']}).md"), "w", encoding="utf-8") as f:
            f.write("\n".join(out))
    except OSError:
        pass


def echo_list():
    out = []
    if os.path.isdir(ECHO_DIR):
        for name in os.listdir(ECHO_DIR):
            if name.endswith(".json"):
                rec = _echo_load(name[:-5])
                if rec:
                    words = sum(len(s["text"].split()) for s in rec.get("segments", []))
                    last = rec["segments"][-1]["t"] if rec.get("segments") else 0
                    out.append({"id": rec["id"], "title": rec["title"], "kind": rec.get("kind"), "started": rec["started"],
                                "words": words, "duration": last, "done": bool(rec.get("notes"))})
    out.sort(key=lambda r: r["started"], reverse=True)
    return out[:100]


def echo_get(eid):
    rec = _echo_load(eid)
    if not rec:
        return {"error": "That recording wasn't found."}
    return {"id": rec["id"], "title": rec["title"], "kind": rec.get("kind"), "started": rec["started"],
            "notes": rec.get("notes"), "segments": rec.get("segments", [])}


def echo_ask(b):
    """A question about one recording, or about all of them."""
    q = str(b.get("question") or "").strip()[:400]
    if not q:
        return {"error": "Ask a question."}
    recs = [_echo_load(b["id"])] if b.get("id") else [_echo_load(r["id"]) for r in echo_list()[:12]]
    recs = [r for r in recs if r and r.get("segments")]
    if not recs:
        return {"answer": "There aren't any recordings to look through yet."}
    budget = 60000 // len(recs)
    corpus = "\n\n".join(f"=== {r['title']} ({datetime.datetime.fromtimestamp(r['started']):%d %b %Y}) ===\n{_transcript(r, budget)}" for r in recs)
    data = _json_llm(f"""Answer the user's question using ONLY these recordings of their lectures / meetings.
Quote the recording title and timestamp you used. If the answer isn't in them, say so.
{corpus}

Question: "{q}"
Respond ONLY with JSON: {{"answer": "clear answer, plain text, 1-6 sentences", "sources": ["Title @ mm:ss"]}}""", 900)
    return {"answer": data.get("answer") or data.get("text") or "", "sources": data.get("sources") or []}


def echo_delete(eid):
    rec = _echo_load(eid)
    if not rec:
        return False
    os.remove(_echo_path(eid))
    for name in os.listdir(ECHO_DIR):
        if name.endswith(f"({rec['id']}).md"):
            os.remove(os.path.join(ECHO_DIR, name))
    return True


# ------------------------------------------------------------------ RADAR (what's around you)
# category -> (OpenStreetMap key, values)
CATEGORIES = {
    "food": ("amenity", "restaurant|fast_food|food_court"), "cafe": ("amenity", "cafe|ice_cream"),
    "pharmacy": ("amenity", "pharmacy"), "hospital": ("amenity", "hospital|clinic|doctors"),
    "atm": ("amenity", "atm|bank"), "fuel": ("amenity", "fuel|charging_station"),
    "mosque": ("amenity", "place_of_worship"), "shop": ("shop", "supermarket|convenience|mall|bakery"),
    "transport": ("amenity", "bus_station|taxi"), "study": ("amenity", "library|university|college"),
    "park": ("leisure", "park|garden"),
}
_places_cache = {}
# public servers, tried in order - the main one often says "busy" (504) and then answers a moment later
OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.private.coffee/api/interpreter",
            "https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter",
            "https://overpass-api.de/api/interpreter"]
UA = {"User-Agent": "AURA-personal-assistant/1.0 (private use)"}


def _dist_bearing(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    d = 2 * 6371000 * math.asin(math.sqrt(a))
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return d, (math.degrees(math.atan2(y, x)) + 360) % 360


def places(b):
    import requests
    lat, lon = float(b.get("lat")), float(b.get("lon"))
    radius = max(200, min(int(b.get("radius") or 1200), 5000))
    cats = [c for c in (b.get("cats") or list(CATEGORIES)) if c in CATEGORIES]
    key = (round(lat, 3), round(lon, 3), radius, tuple(sorted(cats)))
    hit = _places_cache.get(key)
    if hit and time.time() - hit[0] < 600:
        elements = hit[1]
    else:
        by_key = {}                      # one search per OpenStreetMap key (much faster than one per category)
        for c in cats:
            by_key.setdefault(CATEGORIES[c][0], []).append(CATEGORIES[c][1])
        q = "".join(f'nwr["{k}"~"^({"|".join(v)})$"](around:{radius},{lat},{lon});' for k, v in by_key.items())
        elements = None
        t0 = time.time()
        for server in OVERPASS:
            if time.time() - t0 > 50:
                break
            try:
                r = requests.post(server, data={"data": f"[out:json][timeout:20];({q});out center 300;"}, headers=UA, timeout=14)
                if r.ok:
                    elements = r.json().get("elements", [])
                    break
            except Exception:
                continue
        if elements is None:
            # every server busy: the last results from about here are better than nothing
            near = [v for k, v in _places_cache.items() if abs(k[0] - key[0]) <= 0.01 and abs(k[1] - key[1]) <= 0.01]
            if not near:
                return {"error": "The map services are busy right now - try again in a moment.", "places": []}
            elements = max(near, key=lambda v: v[0])[1]
        else:
            _places_cache[key] = (time.time(), elements)
    out = []
    for e in elements:
        tags = e.get("tags") or {}
        plat, plon = e.get("lat") or (e.get("center") or {}).get("lat"), e.get("lon") or (e.get("center") or {}).get("lon")
        if plat is None:
            continue
        cat = next((c for c in cats if re.fullmatch(CATEGORIES[c][1], tags.get(CATEGORIES[c][0], ""))), "place")
        kind = tags.get(CATEGORIES[cat][0]) if cat in CATEGORIES else (tags.get("amenity") or tags.get("shop") or "place")
        d, br = _dist_bearing(lat, lon, plat, plon)
        out.append({"name": tags.get("name:en") or tags.get("name") or kind.replace("_", " ").title(), "kind": kind, "cat": cat,
                    "lat": plat, "lon": plon, "dist": round(d), "bearing": round(br, 1),
                    "open": tags.get("opening_hours", ""), "phone": tags.get("phone") or tags.get("contact:phone") or ""})
    out.sort(key=lambda p: p["dist"])
    return {"places": out[:150]}


def where(b):
    import requests
    try:
        r = requests.get("https://nominatim.openstreetmap.org/reverse", params={"lat": b.get("lat"), "lon": b.get("lon"),
                         "format": "jsonv2", "zoom": 17, "accept-language": "en"}, headers=UA, timeout=12)
        d = r.json()
        a = d.get("address") or {}
        short = ", ".join(x for x in (a.get("road"), a.get("suburb") or a.get("neighbourhood"), a.get("city") or a.get("town")) if x)
        return {"address": d.get("display_name", ""), "short": short or d.get("display_name", "")[:80]}
    except Exception:
        return {"address": "", "short": ""}


def around_ask(b):
    near = b.get("places") or []
    lines = "\n".join(f"- {p.get('name')} ({p.get('kind')}, {p.get('dist')} m, bearing {p.get('bearing')}°"
                      f"{', hours ' + p['open'] if p.get('open') else ''})" for p in near[:60] if isinstance(p, dict))
    data = _json_llm(f"""You are AURA. The user is at {b.get('address') or 'lat ' + str(b.get('lat')) + ', lon ' + str(b.get('lon'))},
facing {b.get('heading', '?')}° (compass). Time: {datetime.datetime.now():%A %H:%M}.
Places around them (from OpenStreetMap):
{lines or '(none found)'}
They ask: "{str(b.get('question') or 'What is around me?')[:300]}"
Answer helpfully in 2-4 spoken sentences; use directions like "about 200 metres to your left" based on
bearing vs their heading. If you recommend a place, name it exactly as listed.
Respond ONLY with JSON: {{"answer": "...", "highlight": ["names of places you mentioned"]}}""", 500, 0.4)
    answer = data.get("answer") or data.get("text") or ""
    audio, ctype = speech(answer)
    return {"answer": answer, "highlight": data.get("highlight") or [], "audio": audio, "ctype": ctype}


# ------------------------------------------------------------------ PULSE (bio readings)
def bio_save(b):
    kind = str(b.get("kind") or "")
    if kind not in ("heart", "breath", "reaction", "sound"):
        return {"error": "unknown reading"}
    entry = {"kind": kind, "t": time.time(), "value": float(b.get("value") or 0),
             "extra": {k: v for k, v in (b.get("extra") or {}).items() if isinstance(v, (int, float, str))}}
    with _lock:
        data = bio_list()
        data.append(entry)
        data = data[-1000:]
        with open(BIO_FILE + ".tmp", "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(BIO_FILE + ".tmp", BIO_FILE)
    return {"ok": True}


def bio_list():
    try:
        with open(BIO_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


# ------------------------------------------------------------------ LINK (this PC from the phone)
_remote = {"last": 0}


def remote_allowed():
    import config
    return bool(getattr(config, "PHONE_REMOTE_CONTROL", True))


def _remote_notice():
    """Tells whoever is at the PC that the phone is looking at / controlling it (once per session)."""
    now = time.time()
    if now - _remote["last"] > 300:
        try:
            import face_window
            face_window.show_toast("AURA LINK", "Your phone is viewing and controlling this PC.", "info")
        except Exception:
            pass
    _remote["last"] = now


def screen_jpeg(width=900, quality=55):
    from PIL import ImageGrab, ImageDraw
    import io
    import pyautogui
    _remote_notice()
    img = ImageGrab.grab()
    W, H = img.size
    width = max(320, min(int(width or 900), 1920))
    if W > width:
        img = img.resize((width, round(H * width / W)))
    # the PC's mouse pointer, so you can see where a click will land
    try:
        mx, my = pyautogui.position()
        sx, sy = mx * img.width / W, my * img.height / H
        d = ImageDraw.Draw(img)
        d.ellipse([sx - 7, sy - 7, sx + 7, sy + 7], outline=(58, 214, 255), width=2)
        d.ellipse([sx - 2, sy - 2, sx + 2, sy + 2], fill=(158, 243, 255))
    except Exception:
        pass
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=max(20, min(int(quality or 55), 85)))
    return buf.getvalue(), W, H


_KEYS = {"enter", "backspace", "tab", "esc", "space", "delete", "up", "down", "left", "right", "home", "end",
         "pageup", "pagedown", "f5", "f11", "win", "playpause", "nexttrack", "prevtrack", "volumeup", "volumedown",
         "volumemute"}
_MODS = {"ctrl", "alt", "shift", "win"}


def remote_input(ev, notice=True):
    import pyautogui
    if notice:
        _remote_notice()
    kind = ev.get("type")
    W, H = pyautogui.size()
    clamp = lambda x, y: (max(2, min(W - 3, int(x))), max(2, min(H - 3, int(y))))     # never a fail-safe corner
    try:
        if kind == "move_rel":
            x, y = pyautogui.position()
            pyautogui.moveTo(*clamp(x + float(ev.get("dx") or 0), y + float(ev.get("dy") or 0)), _pause=False)
        elif kind == "move_abs":
            pyautogui.moveTo(*clamp(float(ev.get("x")) * W, float(ev.get("y")) * H), _pause=False)
        elif kind == "click":
            if ev.get("x") is not None:
                pyautogui.moveTo(*clamp(float(ev["x"]) * W, float(ev["y"]) * H), _pause=False)
            button = ev.get("button") if ev.get("button") in ("left", "right", "middle") else "left"
            pyautogui.click(button=button, clicks=2 if ev.get("double") else 1, interval=0.06, _pause=False)
        elif kind in ("down", "up"):
            (pyautogui.mouseDown if kind == "down" else pyautogui.mouseUp)(_pause=False)
        elif kind == "scroll":
            dy, dx = int(ev.get("dy") or 0), int(ev.get("dx") or 0)
            if dy:
                pyautogui.scroll(max(-2000, min(2000, dy)), _pause=False)
            if dx:
                pyautogui.hscroll(max(-2000, min(2000, dx)), _pause=False)
        elif kind == "type":
            text = str(ev.get("text") or "")[:2000]
            if text.isascii() and "\n" not in text:
                pyautogui.write(text, interval=0.005, _pause=False)
            else:
                import pc_control
                pc_control._paste_text(text)
        elif kind == "lock":
            import ctypes
            ctypes.windll.user32.LockWorkStation()
        elif kind == "key":
            keys = [k for k in str(ev.get("keys") or "").lower().split("+") if k]
            if not keys or any(k not in _KEYS and k not in _MODS and not re.fullmatch(r"[a-z0-9]|f\d{1,2}", k) for k in keys):
                return {"error": "unknown key"}
            pyautogui.hotkey(*keys, _pause=False) if len(keys) > 1 else pyautogui.press(keys[0], _pause=False)
        else:
            return {"error": "unknown input"}
    except pyautogui.FailSafeException:
        return {"error": "fail-safe"}
    return {"ok": True}


def clipboard(b):
    import pyperclip
    if "text" in b:
        pyperclip.copy(str(b.get("text") or "")[:100000])
        return {"ok": True}
    try:
        return {"text": pyperclip.paste()[:100000]}
    except Exception:
        return {"text": ""}


# ------------------------------------------------------------------ PROTOCOLS & MISSIONS
DEFAULT_PROTOCOLS = [
    {"id": "study", "name": "Study", "icon": "book", "steps": ["turn on do not disturb", "start a 50 minute focus session for studying",
                                                                "play lofi study music on spotify"]},
    {"id": "morning", "name": "Good morning", "icon": "sun", "steps": ["give me my morning briefing", "what's on my schedule today",
                                                                         "do I have any important emails"]},
    {"id": "night", "name": "Night", "icon": "moon", "steps": ["what are my reminders for tomorrow", "turn on do not disturb"]},
    {"id": "sprint", "name": "Focus sprint", "icon": "bolt", "steps": ["turn on do not disturb", "start a 25 minute focus session",
                                                                       "what's on my task list"]},
]


def protocols_list():
    try:
        with open(PROTOCOLS_FILE, encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
    except Exception:
        pass
    return [dict(p) for p in DEFAULT_PROTOCOLS]


def _protocols_write(items):
    with open(PROTOCOLS_FILE + ".tmp", "w", encoding="utf-8") as f:
        json.dump(items, f, indent=1, ensure_ascii=False)
    os.replace(PROTOCOLS_FILE + ".tmp", PROTOCOLS_FILE)


def protocol_save(b):
    name = str(b.get("name") or "").strip()[:40]
    steps = [str(s).strip()[:300] for s in (b.get("steps") or []) if str(s).strip()][:12]
    if not name or not steps:
        return {"error": "A protocol needs a name and at least one step."}
    with _lock:
        items = protocols_list()
        pid = b.get("id") or re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or secrets.token_hex(3)
        item = {"id": pid, "name": name, "icon": str(b.get("icon") or "bolt")[:12], "steps": steps}
        items = [p for p in items if p["id"] != pid] + [item]
        _protocols_write(items)
    return {"ok": True, "protocol": item, "items": items}


def protocol_delete(pid):
    with _lock:
        items = [p for p in protocols_list() if p["id"] != pid]
        _protocols_write(items)
    return {"items": items}


PROTOCOL_RE = re.compile(r"^\s*(?:aura[, ]+)?(?:run|activate|engage|initiate|start|execute|launch)\s+(?:the\s+|my\s+)?(.+?)\s+protocol\s*[.!]?\s*$", re.I)


def match_protocol(text):
    """'run study protocol' / 'engage the night protocol' -> that protocol (or None)."""
    m = PROTOCOL_RE.match(str(text or ""))
    if not m:
        return None
    want = m.group(1).lower().strip()
    items = protocols_list()
    return (next((p for p in items if p["name"].lower() == want or p["id"] == want), None)
            or next((p for p in items if want in p["name"].lower()), None))


_running = {"id": None, "stop": False}


def protocol_run(name, steps, origin_device=None):
    """Runs the steps one after another through AURA's normal command queue (each one finishes before
    the next starts). Progress goes to the phone live."""
    import phone_bridge
    if _running["id"]:
        return {"error": "A protocol is already running."}
    steps = [s for s in steps if str(s).strip()][:12]
    if not steps:
        return {"error": "Nothing to run."}
    run_id = secrets.token_hex(4)
    _running.update(id=run_id, stop=False)

    def wait_idle(timeout=120):
        t0 = time.time()
        time.sleep(0.8)
        quiet = 0
        while time.time() - t0 < timeout and not _running["stop"]:
            busy = phone_bridge._status.get("thinking") or phone_bridge._status.get("asking") or phone_bridge._queue.qsize() > 0
            quiet = 0 if busy else quiet + 1
            if quiet >= 3:
                return
            time.sleep(0.4)

    def run():
        try:
            phone_bridge.emit("protocol", run=run_id, name=name, step=0, total=len(steps), state="start")
            wait_idle()
            for i, step in enumerate(steps):
                if _running["stop"]:
                    phone_bridge.emit("protocol", run=run_id, name=name, step=i, total=len(steps), state="stopped")
                    return
                phone_bridge.emit("protocol", run=run_id, name=name, step=i + 1, total=len(steps), state="step", text=step)
                if origin_device:
                    cmd = phone_bridge.PhoneText(step)
                    cmd.device = origin_device
                else:
                    cmd = step
                phone_bridge._queue.put(cmd)
                wait_idle()
            phone_bridge.emit("protocol", run=run_id, name=name, step=len(steps), total=len(steps), state="done")
        finally:
            _running.update(id=None, stop=False)
    threading.Thread(target=run, daemon=True, name="protocol").start()
    return {"ok": True, "run": run_id, "total": len(steps)}


def protocol_stop():
    _running["stop"] = True
    return {"ok": True}


def mission_plan(b):
    goal = str(b.get("goal") or "").strip()[:500]
    if not goal:
        return {"error": "Tell AURA the goal."}
    import vision_lab
    data = _json_llm(f"""You are AURA's mission planner. The user's goal: "{goal}"
{vision_lab._who()}
Time now: {datetime.datetime.now():%A %d %B %Y, %H:%M}.
AURA can carry out spoken commands like these (one command per step): {CAN_DO}; also "research <topic> on pubmed",
"web briefing on <topic>", "search the web for <x>", "draft an email to <who> about <x>", "add <x> to my tasks",
"remind me <when> to <x>", "start a <n> minute focus session", "open <site> on my PC", "find files about <x>".
Break the goal into 2-8 concrete commands AURA can actually run, in order, that directly serve THIS goal
(use what you know about the user only to tailor them - never add their other projects), each written exactly as the user would
say it to AURA. Don't include anything AURA can't do; never send email or submit forms without the user - drafts only.
Respond ONLY with JSON: {{"title": "short mission name", "steps": [{{"command": "...", "why": "few words"}}],
"note": "one line on anything the user must do themselves, or empty"}}""", 900, 0.3)
    steps = [s for s in (data.get("steps") or []) if isinstance(s, dict) and s.get("command")][:8]
    return {"title": str(data.get("title") or goal)[:60], "steps": steps, "note": data.get("note") or ""}


# ------------------------------------------------------------------ voice: open a NEXUS module on the phone
PHONE_TOOLS = [
    (r"\b(live|jarvis) mode\b|\bgo live\b|\bstart live\b", "nexus-live"),
    (r"\b(interpreter|translator|babel)\b|\btranslate (?:a |this |our )?conversation\b", "nexus-babel"),
    (r"\b(echo|lecture recorder|record (?:the |this |my |a |our )?(?:lecture|meeting|class))\b", "nexus-echo"),
    (r"\b(radar|what'?s around me|nearby places|qibla|compass)\b", "nexus-radar"),
    (r"\b(pulse|heart ?rate|bio ?scan|breathing rate|my pulse|stress (?:test|check))\b", "nexus-pulse"),
    (r"\b(link|remote|control)\b.*\b(pc|computer|laptop)\b.*\bfrom (?:my |the )?phone\b|\baura link\b|\bopen link\b|\bpc remote\b", "nexus-link"),
    (r"\b(protocols?|missions?)\b", "nexus-protocols"),
    (r"\bnexus\b", "nexus"),
]
_OPEN_RE = re.compile(r"^\s*(?:aura[, ]+)?(?:open|start|launch|show|go to|activate|turn on|enter|use|check|record|measure|scan)\b", re.I)


def phone_tool(text):
    """'open radar' / 'start live mode' / 'check my heart rate' -> the NEXUS module to open on the phone."""
    text = str(text or "")
    if not _OPEN_RE.search(text) or PROTOCOL_RE.match(text):
        return None
    for pattern, tool in PHONE_TOOLS:
        if re.search(pattern, text, re.I):
            return tool
    return None
