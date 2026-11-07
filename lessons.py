# lessons.py
# Logs mistakes/failures persistently and surfaces relevant ones before
# similar future actions - so AURA has a chance to not blindly repeat the
# exact same failure.
import os
import json

LESSONS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aura_lessons.json")
MAX_LESSONS = 200


def load_lessons():
    if os.path.exists(LESSONS_FILE):
        try:
            with open(LESSONS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []


def save_lessons(lessons):
    try:
        with open(LESSONS_FILE, "w", encoding="utf-8") as f:
            json.dump(lessons, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"(Couldn't save lessons: {e})")


def add_lesson(action_type, note):
    lessons = load_lessons()
    lessons.append({"action_type": action_type, "note": note})
    if len(lessons) > MAX_LESSONS:
        lessons = lessons[-MAX_LESSONS:]
    save_lessons(lessons)


def get_relevant_lessons(action_type, top_k=3):
    lessons = load_lessons()
    matches = [l["note"] for l in lessons if l.get("action_type") == action_type]
    return matches[-top_k:]


# ------------------------------------------------------------------ learning from your corrections
# When you tell AURA it got something wrong ("no, I meant...", "that's not right, it's..."), the brain writes
# the lesson in one line and it's kept here. The lessons that matter for a new message are put in front of
# the AI every time, so the same mistake isn't repeated.
def add_correction(note, said=""):
    note = " ".join(str(note or "").split())[:300]
    if not note:
        return
    lessons = load_lessons()
    if any(l.get("action_type") == "correction" and l.get("note", "").lower() == note.lower() for l in lessons):
        return
    import time
    lessons.append({"action_type": "correction", "note": note, "said": str(said or "")[:200], "t": time.time()})
    save_lessons(lessons[-MAX_LESSONS:])
    print(f"(AURA learned: {note})")


def corrections():
    return [l for l in load_lessons() if l.get("action_type") == "correction"]


def relevant_for(text, top_k=6):
    """The corrections that matter for this message: the ones sharing words with it, plus the newest few."""
    items = corrections()
    if not items:
        return []
    from knowledge import tokens
    words = set(tokens(text))
    scored = sorted(((len(words & set(tokens(l["note"] + " " + l.get("said", "")))), i, l["note"]) for i, l in enumerate(items)), reverse=True)
    picked = [n for score, _, n in scored if score > 0][:top_k]
    for l in reversed(items[-3:]):                    # recent ones apply broadly ("call me Sam", "answer shorter")
        if l["note"] not in picked:
            picked.append(l["note"])
    return picked[:top_k + 3]