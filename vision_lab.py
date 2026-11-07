# vision_lab.py
# The brain behind AURA Mobile's Vision (the phone's camera HUD): ask about what you see, read /
# translate text, study mode for med school (explain / flashcards / quiz), solve, food estimates,
# a memory of your own things (recognised live on the phone), and saving captures to the PC.
#
# Live detection and recognising remembered things happen ON THE PHONE (fast, private); the phone
# only sends a photo here when you ask for an analysis. Uses Gemini's vision when a Gemini key is set
# (config.GEMINI_API_KEY), otherwise Groq's vision model (vision.py).
import base64
import datetime
import json
import os
import re
import secrets
import threading

from ai import extract_json

AURA_DIR = os.path.dirname(os.path.abspath(__file__))
MEMORY_FILE = os.path.join(AURA_DIR, "aura_vision_memory.json")
CAPTURES_DIR = os.path.join(os.path.expanduser("~"), "Documents", "AURA Vision")
_lock = threading.RLock()


# ------------------------------------------------------------------ the vision model
def _ask(image_b64, prompt, max_tokens=700):
    content = [{"type": "text", "text": prompt},
               {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}}]
    try:
        import ai
        if getattr(ai, "_gemini", None) and ai.GEMINI_MODELS:
            for model in ai.GEMINI_MODELS:
                if ai._resting_until.get(model, 0) > __import__("time").time():
                    continue
                try:
                    r = ai._gemini.chat.completions.create(model=model[len("gemini:"):], messages=[{"role": "user", "content": content}],
                                                           temperature=0.2, max_tokens=max_tokens + 1500, reasoning_effort="low")
                    text = r.choices[0].message.content
                    if text:
                        return text
                except Exception as e:
                    ai._rest(model, e)
    except Exception:
        pass
    from vision import _ask_vision
    return _ask_vision(content, max_tokens=max_tokens)


def _json(image_b64, prompt, max_tokens=700):
    raw = _ask(image_b64, prompt, max_tokens)
    try:
        data = extract_json(raw)
        return data if isinstance(data, dict) else {"text": raw}
    except ValueError:
        return {"text": raw.strip()}


def _who():
    """A little about the user, so answers fit them (who they are, what they're into)."""
    try:
        import config
        from memory_store import load_store
        s = load_store()
        facts = ([config.USER_ABOUT] if getattr(config, "USER_ABOUT", "") else []) + \
            (s.get("facts", []) + s.get("projects", []))[-8:]
        return ("About the user: " + "; ".join(facts)) if facts else ""
    except Exception:
        return ""


# ------------------------------------------------------------------ analyses
def ask(image_b64, question):
    q = (question or "What am I looking at?").strip()[:500]
    return _json(image_b64, f"""You are AURA, the user's personal assistant, looking through their phone camera.
{_who()}
They ask: "{q}"
Answer about what is actually in the image. Be specific and useful, plain spoken English (it is read aloud),
2-5 sentences, no markdown. If you can't tell from the image, say so honestly.
Respond ONLY with JSON: {{"answer": "...", "follow_ups": ["2-3 short follow-up questions they might ask next"]}}""", 450)


def read(image_b64, mode="read", lang="English"):
    if mode == "translate":
        task = f"""Read ALL the text in the image exactly, detect its language, and translate it into {lang}.
Respond ONLY with JSON: {{"language": "detected language", "text": "the original text", "translation": "the {lang} translation"}}"""
    else:
        task = """Read ALL the text in the image exactly as written (keep line breaks). Then pull out anything actionable.
Respond ONLY with JSON:
{"text": "the full text",
 "summary": "one line: what this is (e.g. 'Pharmacology lecture slide on beta blockers', 'Clinic appointment card')",
 "dates": [{"text": "the date/time as written", "event": "what happens then, as a calendar title"}],
 "todos": ["action items / deadlines worth adding as tasks"],
 "phones": ["phone numbers"], "emails": ["email addresses"], "links": ["web addresses"]}
Use [] for anything not present."""
    return _json(image_b64, "You read text from the user's phone camera for AURA, their assistant.\n" + task, 900)


STUDY = {
    "explain": """Act as a brilliant medical-school tutor. Explain what's in this image (a textbook page, slide, diagram,
ECG, X-ray, specimen, drug label... whatever it is) so the student really understands it.
Respond ONLY with JSON: {"title": "what it is", "explanation": "clear 4-7 sentence explanation, plain text",
"key_points": ["the 3-6 things to remember"], "clinical": "one line of clinical relevance, or empty",
"mnemonic": "a memorable mnemonic if one genuinely helps, else empty"}""",
    "flashcards": """Turn what's in this image into high-yield flashcards for a medical student.
Respond ONLY with JSON: {"title": "topic", "cards": [{"q": "question", "a": "short answer"}]} with 5-8 cards.""",
    "quiz": """Write a short exam-style quiz (MBBS level) on what's in this image.
Respond ONLY with JSON: {"title": "topic", "questions": [{"q": "question", "options": ["A","B","C","D"], "answer": 0-3 index, "why": "one-line explanation"}]} with 4-5 questions.""",
    "solve": """Solve the problem shown in the image (maths, physics, chemistry, a calculation, a puzzle, a dose
calculation...). Show the method step by step, then the final answer. If nothing to solve, say so.
Respond ONLY with JSON: {"problem": "the problem in one line", "steps": ["step 1", "step 2", "..."], "answer": "final answer"}""",
    "food": """Look at the food/drink in the image and estimate its nutrition for the portion shown. Be realistic,
say the uncertainty. Respond ONLY with JSON: {"items": [{"name": "...", "portion": "...", "kcal": number}],
"total_kcal": number, "protein_g": number, "carbs_g": number, "fat_g": number, "sugar_g": number,
"verdict": "one honest line (e.g. 'High in sugar - fine as a treat')", "tip": "one practical tip"}""",
}


def study(image_b64, mode="explain"):
    prompt = STUDY.get(mode, STUDY["explain"])
    return _json(image_b64, f"You are AURA, looking through the user's phone camera.\n{_who()}\n{prompt}",
                 900 if mode in ("flashcards", "quiz", "explain") else 700)


# ------------------------------------------------------------------ memory of the user's own things
def _load():
    try:
        with open(MEMORY_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}
    data.setdefault("items", [])
    return data


def _save(data):
    tmp = MEMORY_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, MEMORY_FILE)


def memory_list():
    with _lock:
        return _load()["items"]


def memory_add(name, note, thumb_b64, embeddings, model):
    name = (name or "").strip()[:60]
    if not name or not embeddings:
        return {"error": "Give it a name first."}
    item = {"id": secrets.token_hex(6), "name": name, "note": (note or "").strip()[:300], "thumb": thumb_b64[:200_000],
            "embeddings": [[round(float(x), 5) for x in e] for e in embeddings[:12]], "model": model,
            "created": datetime.datetime.now().isoformat(timespec="seconds"), "seen": 0, "last_seen": None}
    with _lock:
        data = _load()
        data["items"].append(item)
        _save(data)
    return item


def memory_views(item_id, embeddings):
    """More angles of a remembered thing -> recognised more reliably."""
    with _lock:
        data = _load()
        for it in data["items"]:
            if it["id"] == item_id:
                it["embeddings"] = (it["embeddings"] + [[round(float(x), 5) for x in e] for e in embeddings])[-24:]
                _save(data)
                return it
    return {"error": "not found"}


def memory_seen(item_id, scene_b64=""):
    """A remembered thing was just recognised - keep when, and a small photo of where (for RECALL)."""
    with _lock:
        data = _load()
        for it in data["items"]:
            if it["id"] == item_id:
                it["seen"] = it.get("seen", 0) + 1
                it["last_seen"] = datetime.datetime.now().isoformat(timespec="seconds")
                if scene_b64:
                    it["last_scene"] = scene_b64[:150_000]
                _save(data)
                return True
    return False


def memory_delete(item_id):
    with _lock:
        data = _load()
        before = len(data["items"])
        data["items"] = [i for i in data["items"] if i["id"] != item_id]
        _save(data)
        return len(data["items"]) < before


def memory_rename(item_id, name, note):
    with _lock:
        data = _load()
        for it in data["items"]:
            if it["id"] == item_id:
                if name:
                    it["name"] = name.strip()[:60]
                if note is not None:
                    it["note"] = note.strip()[:300]
                _save(data)
                return it
    return {"error": "not found"}


# ------------------------------------------------------------------ keeping things
def save_capture(image_b64, title, text):
    """Saves the photo (+ what AURA found) in Documents\\AURA Vision\\<date>\\ - indexed, so later
    'bring up everything I have on beta blockers' finds it."""
    day = os.path.join(CAPTURES_DIR, datetime.date.today().isoformat())
    os.makedirs(day, exist_ok=True)
    safe = re.sub(r'[\\/:*?"<>|]+', "", (title or "capture")).strip()[:60] or "capture"
    base = os.path.join(day, f"{datetime.datetime.now():%H%M%S} {safe}")
    with open(base + ".jpg", "wb") as f:
        f.write(base64.b64decode(image_b64))
    if text:
        with open(base + ".txt", "w", encoding="utf-8") as f:
            f.write(f"{title}\n\n{text}\n")
    try:
        import file_search
        file_search.refresh_in_background()
    except Exception:
        pass
    return {"ok": True, "path": base + ".jpg", "folder": day}


def add_to_notes(title, text):
    from notes import get_notes, save_notes
    stamp = datetime.datetime.now().strftime("%d %b %H:%M")
    block = f"\n\n--- {title} ({stamp}, from Vision) ---\n{text.strip()}\n"
    return save_notes((get_notes() or "").rstrip() + block)


def add_to_calendar(text):
    from event_parser import parse_event
    from calendar_integration import create_event
    parsed = parse_event(text)
    if not parsed:
        return {"error": "I couldn't work out the date and time from that."}
    ok, err = create_event(parsed["summary"], parsed["start"], parsed["end"], parsed["all_day"])
    if not ok:
        return {"error": f"Couldn't add it to your calendar: {err}"}
    when = parsed["start"].strftime("%A, %B %d") if parsed["all_day"] else parsed["start"].strftime("%A, %B %d at %I:%M %p").replace(" 0", " ")
    return {"ok": True, "message": f"Added '{parsed['summary']}' to your calendar for {when}."}


# ------------------------------------------------------------------ always-on observation (INTEL)
def observe(image_b64, focus=""):
    """A sharp, compact look at the whole scene - what the live detector can't name. Boxes are fractions."""
    extra = f'The user is especially interested in: "{focus}".' if focus else ""
    return _json(image_b64, f"""You are the perception core of a futuristic heads-up display, as observant as a world-class detective.
Look at EVERY part of this camera frame. {extra}
Respond ONLY with compact JSON:
{{"summary": "one line: what's going on",
 "objects": [{{"label": "precise name (brand/model only if clearly readable)", "x": 0-1, "y": 0-1, "w": 0-1, "h": 0-1, "detail": "short useful detail"}}],
 "text": ["any readable text, verbatim, short"],
 "notable": ["small details most people would miss - wear, labels, expiry dates, reflections, odd things"],
 "hazards": ["anything unsafe, else empty"]}}
x,y = top-left corner, w,h = size, all as fractions of the image. Up to 10 objects, most important first.
For people describe only what's visible (never identity, age, ethnicity or other sensitive traits).""", 600)


# ------------------------------------------------------------------ DETECTIVE
def detective(image_b64, question=""):
    q = f'Focus on: "{question}".' if question else "Work out the story of this scene."
    return _json(image_b64, f"""You are AURA in DETECTIVE mode - Sherlock Holmes' powers of observation, but rigorously honest.
{q}
Find the small clues (wear, stains, labels, positions, shadows, reflections, time cues, what's missing) and reason from them.
Never claim identity of people or sensitive traits. Separate what you SEE from what you INFER.
Respond ONLY with JSON:
{{"clues": [{{"n": 1, "clue": "what you observe", "x": 0-1, "y": 0-1}}],
 "deductions": [{{"claim": "what it suggests", "because": "which clues (by number) and why", "confidence": "high|medium|low"}}],
 "conclusion": "your best overall reading of the scene in 1-2 sentences",
 "check_next": ["what to look at to confirm or rule it out"]}}
x,y = where the clue is in the image (fractions). 4-8 clues.""", 900)


# ------------------------------------------------------------------ SENTINEL
def watch(image_b64, conditions):
    conds = [str(c)[:150] for c in (conditions or []) if str(c).strip()][:6]
    if not conds:
        return {"triggered": [], "description": ""}
    lst = "\n".join(f"{i + 1}. {c}" for i, c in enumerate(conds))
    return _json(image_b64, f"""You are a security/monitoring camera's brain. The user asked to be alerted when:
{lst}
Look at this frame. For each condition, decide if it is TRUE RIGHT NOW (be strict - only if clearly visible).
Respond ONLY with JSON: {{"triggered": [{{"condition": "the condition text", "evidence": "what you see that proves it"}}],
"description": "one short line describing the scene"}}""", 300)


# ------------------------------------------------------------------ MED lens (educational)
MED = {
    "drug": """Identify the medicine (pill, blister, box, bottle, label). Respond ONLY with JSON:
{"name": "generic (brand) - only if readable/clear, else best guess marked 'possibly'", "strength": "...", "form": "...",
"class": "drug class", "uses": ["main indications"], "how_it_works": "one line mechanism",
"typical_dose": "standard adult dosing as printed or typical", "side_effects": ["common + serious"],
"interactions": ["important interactions"], "warnings": ["contraindications / red flags"], "confidence": "high|medium|low"}""",
    "ecg": """Interpret this ECG systematically, like teaching an MBBS student. Respond ONLY with JSON:
{"rate": "...", "rhythm": "...", "axis": "...", "pr": "...", "qrs": "...", "qt": "...", "st_t": "...",
"findings": ["key abnormalities"], "impression": "most likely interpretation", "differentials": ["..."],
"next_steps": ["what a clinician would do next"], "confidence": "high|medium|low"}""",
    "xray": """Describe this radiograph/scan systematically (ABCDE / systematic approach) for an MBBS student.
Respond ONLY with JSON: {"study": "what it is (view, region)", "systematic": ["each step of the review with findings"],
"findings": ["key findings"], "impression": "...", "differentials": ["..."], "confidence": "high|medium|low"}""",
    "lab": """Read this lab report. Respond ONLY with JSON: {"tests": [{"name": "...", "value": "...", "range": "...", "flag": "high|low|normal"}],
"abnormal": ["what's out of range and what it can mean"], "pattern": "what the results together suggest",
"questions_for_doctor": ["..."]}""",
    "lesion": """Describe this skin lesion / wound objectively (morphology: site, size estimate, shape, border, colour, surface,
surrounding skin) as a student would in a case presentation. Do NOT diagnose definitively. Respond ONLY with JSON:
{"description": "...", "features": ["ABCDE-style features"], "differentials": ["possibilities to consider"],
"red_flags": ["features that need prompt review"], "advice": "when to see a doctor"}""",
}


MED_WHAT = {"drug": "a medicine (pill, tablet, capsule, blister pack, box, bottle or drug label)", "ecg": "an ECG / rhythm strip",
            "xray": "a radiograph or medical scan", "lab": "a lab / test report", "lesion": "skin, a lesion or a wound"}


def med(image_b64, kind="drug"):
    prompt = MED.get(kind, MED["drug"])
    what = MED_WHAT.get(kind, "the subject")
    data = _json(image_b64, f"""You are AURA's clinical lens for a medical student. Educational only - be accurate and honest about uncertainty.
FIRST check: does this image clearly show {what}? If it does NOT, do not guess or invent anything - respond ONLY with
{{"not_found": "No {re.sub(r'^an? ', '', what.split(' (')[0])} visible - the image shows <what it actually shows>."}}
Otherwise:
{prompt}""", 900)
    if isinstance(data, dict) and data.get("not_found"):
        return {"not_found": data["not_found"]}
    if isinstance(data, dict):
        data["disclaimer"] = "Educational - not a diagnosis. Check with a clinician / the official drug information."
    return data


# ------------------------------------------------------------------ COMPARE
def compare(image_a, image_b, question=""):
    q = question or "Compare them - what's the difference, and which is better for what?"
    content = [{"type": "text", "text": f"""You are AURA. The user photographed two things (image A, then image B) and asks: "{q}"
Respond ONLY with JSON: {{"a": "what A is", "b": "what B is", "differences": ["..."], "similarities": ["..."],
"verdict": "a direct answer to their question", "pick": "A|B|depends"}}"""},
               {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_a}"}},
               {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b}"}}]
    raw = None
    try:
        import ai
        if getattr(ai, "_gemini", None) and ai.GEMINI_MODELS:
            r = ai._gemini.chat.completions.create(model=ai.GEMINI_MODELS[0][len("gemini:"):], messages=[{"role": "user", "content": content}],
                                                   temperature=0.2, max_tokens=2000, reasoning_effort="low")
            raw = r.choices[0].message.content
    except Exception:
        raw = None
    if not raw:
        from vision import _ask_vision
        raw = _ask_vision(content, max_tokens=600)
    try:
        d = extract_json(raw)
        return d if isinstance(d, dict) else {"verdict": raw}
    except ValueError:
        return {"verdict": raw}


# ------------------------------------------------------------------ DOC: multi-page scan -> PDF (+ searchable text)
def scan_doc(images_b64, title="Scanned document"):
    import io
    from PIL import Image
    pages = [Image.open(io.BytesIO(base64.b64decode(b))).convert("RGB") for b in images_b64[:30]]
    if not pages:
        return {"error": "No pages."}
    folder = os.path.join(os.path.expanduser("~"), "Documents", "AURA Uploads")
    os.makedirs(folder, exist_ok=True)
    safe = re.sub(r'[\\/:*?"<>|]+', "", title).strip()[:60] or "Scanned document"
    path = os.path.join(folder, f"{safe} {datetime.datetime.now():%Y-%m-%d %H%M}.pdf")
    pages[0].save(path, save_all=True, append_images=pages[1:], resolution=150)
    texts = []
    for i, b in enumerate(images_b64[:30]):
        try:
            r = read(b, "read")
            texts.append(f"--- Page {i + 1} ---\n{r.get('text', '')}")
        except Exception:
            texts.append(f"--- Page {i + 1} --- (couldn't read)")
    with open(path[:-4] + ".txt", "w", encoding="utf-8") as f:     # so AURA's file search can find what it says
        f.write(f"{title}\n\n" + "\n\n".join(texts))
    try:
        import file_search
        file_search.refresh_in_background()
    except Exception:
        pass
    return {"ok": True, "path": path, "name": os.path.basename(path), "pages": len(pages), "text": "\n\n".join(texts)[:20000]}


# ------------------------------------------------------------------ timeline + RECALL
TIMELINE_FILE = os.path.join(AURA_DIR, "aura_vision_timeline.json")


def timeline_add(summary, thumb_b64="", kind="observe"):
    entry = {"t": datetime.datetime.now().isoformat(timespec="seconds"), "kind": kind, "summary": str(summary)[:400], "thumb": (thumb_b64 or "")[:60_000]}
    with _lock:
        try:
            with open(TIMELINE_FILE, encoding="utf-8") as f:
                items = json.load(f)
        except Exception:
            items = []
        items = (items + [entry])[-300:]
        tmp = TIMELINE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(items, f)
        os.replace(tmp, TIMELINE_FILE)
    return entry


def timeline_list(limit=80):
    try:
        with open(TIMELINE_FILE, encoding="utf-8") as f:
            return json.load(f)[-limit:][::-1]
    except Exception:
        return []


def recall(question):
    """'Where did I leave my keys?' - answered from what Vision saw (timeline + remembered things' last sightings)."""
    from ai import ask_llm
    seen = [f"- your {i['name']}: last seen {i.get('last_seen') or 'never'} (seen {i.get('seen', 0)} times){'; note: ' + i['note'] if i.get('note') else ''}"
            for i in memory_list()]
    tl = [f"- {e['t']}: {e['summary']}" for e in timeline_list(120)[::-1]]
    prompt = f"""The user asks: "{question}"
Answer ONLY from what AURA's camera actually saw (below). Give times in a friendly way (e.g. "about 20 minutes ago").
If the answer isn't in there, say so honestly. 1-3 sentences.

Their remembered things:
{chr(10).join(seen) or '(none)'}

What the camera observed (oldest first):
{chr(10).join(tl[-120:]) or '(nothing yet)'}

Current time: {datetime.datetime.now().isoformat(timespec='minutes')}"""
    answer = ask_llm([{"role": "user", "content": prompt}], temperature=0.2)
    q = question.lower()
    match = next((i for i in memory_list() if i["name"].lower() in q or any(w in q for w in i["name"].lower().split() if len(w) > 3)), None)
    return {"answer": answer, "item": {"name": match["name"], "last_seen": match.get("last_seen"), "scene": match.get("last_scene", "")} if match else None}
