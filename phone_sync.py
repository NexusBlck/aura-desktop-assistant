# phone_sync.py
# AURA Mobile's PHONE BRAIN support (mobile/src/brain/). When the PC is off, the phone runs AURA by itself
# (it calls Groq / Gemini directly). This file is the PC's half:
#   kit()        everything the phone needs to think on its own: the AI keys and models, what AURA knows
#                about you (memory), your notes and protocols. Only paired phones get it (phone_bridge).
#   apply(ops)   when the PC is back, replays what happened on the phone meanwhile - tasks, reminders,
#                notes, memories, the conversation, journal / sessions, PULSE readings, ECHO recordings.
#                Each change has an id and is applied once, so a retried sync never doubles anything.
import datetime
import json
import os
import threading
import time

import config

AURA_DIR = os.path.dirname(os.path.abspath(__file__))
SYNC_FILE = os.path.join(AURA_DIR, "aura_phone_sync.json")
_lock = threading.RLock()


def kit():
    from memory_store import load_store
    from notes import get_notes
    import nexus
    import voice
    try:
        import vision
        vision_model = vision.VISION_MODEL_CANDIDATES[0]
    except Exception:
        vision_model = "qwen/qwen3.8-27b"
    store = load_store()
    import phone_bridge
    return {
        "v": 1, "t": time.time(),
        "keys": {"groq": config.LLM_API_KEY, "gemini": getattr(config, "GEMINI_API_KEY", "") or ""},
        "groq_base": config.LLM_BASE_URL.rstrip("/"),
        "models": {"gemini": list(getattr(config, "GEMINI_MODELS", [])), "groq": [config.LLM_MODEL] + list(config.FALLBACK_MODELS),
                   "vision": vision_model, "stt": voice.STT_MODEL},
        "vocab": voice.VOCAB_PROMPT,
        "memory": {k: store.get(k, [])[-60:] for k in ("facts", "projects", "preferences")},
        "name": phone_bridge._user_name(), "location": getattr(config, "LOCATION", ""),
        "timezone": getattr(config, "LOCAL_TIMEZONE", ""),
        "notes": get_notes(), "protocols": nexus.protocols_list(),
        "lessons": [l["note"] for l in _corrections()][-60:],
    }


def _corrections():
    try:
        from lessons import corrections
        return corrections()
    except Exception:
        return []


def knowledge_pack(limit=3000):
    """Your study material as plain passages, so the phone can search it with the PC off (knowledge.py)."""
    import knowledge
    idx = knowledge._load()
    out = []
    for info in idx["files"].values():
        for c in info["chunks"]:
            out.append({"t": c["text"], "title": info.get("title", ""), "src": info.get("label", ""), "where": c.get("where", "")})
            if len(out) >= limit:
                return {"passages": out, "built": idx.get("built")}
    return {"passages": out, "built": idx.get("built")}


# ------------------------------------------------------------------ applying the phone's changes
def _applied():
    try:
        with open(SYNC_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"ids": []}


def _mark(data, op_id):
    data["ids"] = (data["ids"] + [op_id])[-3000:]


def apply(ops, device):
    done, failed = [], []
    with _lock:
        data = _applied()
        seen = set(data["ids"])
        for op in ops[:500]:
            if not isinstance(op, dict) or not op.get("id"):
                continue
            if op["id"] in seen:
                done.append(op["id"])
                continue
            try:
                _apply_one(op.get("type"), op.get("data") or {}, device)
                _mark(data, op["id"])
                seen.add(op["id"])
                done.append(op["id"])
            except Exception as e:
                print(f"(phone sync: couldn't apply {op.get('type')}: {e!r})")
                failed.append({"id": op["id"], "error": str(e)[:200]})
        tmp = SYNC_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, SYNC_FILE)
    if done:
        try:
            import face_window
            face_window.show_toast("AURA Mobile", f"Synced {len(done)} change(s) made on your phone.", "info")
        except Exception:
            pass
    return {"done": done, "failed": failed}


def _apply_one(kind, d, device):
    text = str(d.get("text") or "").strip()
    if kind == "task_add":
        from tasks import add_task
        if text:
            add_task(text[:200])
    elif kind == "task_done":
        from tasks import complete_task
        complete_task(text)
    elif kind == "reminder_add":
        import reminders
        due = datetime.datetime.fromisoformat(str(d["due"]).replace("Z", "+00:00"))
        if due.tzinfo is None:
            due = due.replace(tzinfo=reminders._now().tzinfo)
        due = due.astimezone(reminders._now().tzinfo)
        if text and due > reminders._now() - datetime.timedelta(hours=12):
            reminders.add(text[:200], due)
    elif kind == "reminder_cancel":
        import reminders
        rid = str(d.get("id") or "")
        if not reminders.cancel(rid):            # a reminder made on the phone: find it by its phone id / text
            for r in reminders.pending():
                if r.get("text") == text:
                    reminders.cancel(r["id"])
                    break
    elif kind == "note_append":
        from notes import get_notes, save_notes
        cur = get_notes() or ""
        save_notes((cur.rstrip() + "\n\n" if cur.strip() else "") + text)
    elif kind == "notes_set":
        from notes import get_notes, save_notes
        cur = get_notes() or ""
        base = str(d.get("base") or "")
        if cur == base or not cur.strip():
            save_notes(text)
        elif text != cur:                         # both changed - keep both, nothing is lost
            save_notes(cur.rstrip() + f"\n\n--- from your phone ({datetime.datetime.now():%d %b %H:%M}) ---\n" + text)
    elif kind == "memory_add":
        from memory_store import load_store, save_store, add_entry
        if text:
            store = load_store()
            add_entry(store, str(d.get("category") or "facts"), text[:300])
            save_store(store)
    elif kind == "chat":
        _chat_add(str(d.get("role") or "aura"), text, float(d.get("t") or time.time()))
    elif kind == "journal":
        from life_data import log_journal_entry
        mood = d.get("mood")
        log_journal_entry(int(mood) if str(mood or "").isdigit() else None, str(d.get("good") or "")[:1000], str(d.get("mind") or "")[:1000])
    elif kind == "log_session":
        import phone_bridge
        phone_bridge._log_session(d)
    elif kind == "bio":
        import nexus
        nexus.bio_save(d)
    elif kind == "echo_import":
        import nexus
        rec = d.get("rec") or {}
        if rec.get("id") and isinstance(rec.get("segments"), list):
            rec = {"id": str(rec["id"])[:16], "title": str(rec.get("title") or "Recording")[:80], "kind": str(rec.get("kind") or "lecture")[:20],
                   "started": float(rec.get("started") or time.time()), "segments": rec["segments"][:2000], "notes": rec.get("notes"),
                   "ended": rec.get("ended"), "from_phone": True}
            nexus._echo_save(rec)
            if rec.get("notes"):
                nexus._echo_markdown(rec)
    elif kind == "lesson":
        from lessons import add_correction
        add_correction(text or d.get("note"), d.get("said", ""))
    elif kind == "protocols":
        import nexus
        items = [p for p in (d.get("items") or []) if isinstance(p, dict) and p.get("id") and p.get("steps")]
        if items:
            nexus._protocols_write(items)
    else:
        raise ValueError(f"unknown change '{kind}'")


def _chat_add(role, text, t):
    """Puts a message said while the PC was off into the shared conversation (HUD / Mini / phone), in time order."""
    if not text:
        return
    import mini_window
    with mini_window._chat_lock:
        mini_window._chat.append({"role": role if role in ("you", "aura") else "aura", "text": text[:4000], "t": t})
        mini_window._chat.sort(key=lambda m: m.get("t") or 0)
        del mini_window._chat[:-60]
    mini_window._save_chat()
