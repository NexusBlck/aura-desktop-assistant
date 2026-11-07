# memory_store.py
# Structured, categorized long-term memory - facts about the user, ongoing
# projects, preferences/corrections - plus a short rolling window of recent
# raw turns for immediate continuity. Retrieval pulls only what's relevant
# to the current query (keyword overlap) instead of dumping everything into
# every request.
import os
import json

STORE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aura_memory_store.json")

DEFAULT_STORE = {
    "facts": [],        # durable facts about the user (name, job, etc.)
    "projects": [],      # ongoing projects/tasks they're working on
    "preferences": [],   # preferences and corrections they've given
    "recent_turns": [],  # last few raw exchanges, for immediate continuity
}

MAX_RECENT_TURNS = 12          # ~6 exchanges of raw context
MAX_ENTRIES_PER_CATEGORY = 150


def load_store():
    if os.path.exists(STORE_FILE):
        try:
            with open(STORE_FILE, "r", encoding="utf-8") as f:
                store = json.load(f)
            for key, default in DEFAULT_STORE.items():
                store.setdefault(key, list(default) if isinstance(default, list) else default)
            return store
        except Exception:
            pass
    return {k: list(v) for k, v in DEFAULT_STORE.items()}


def save_store(store):
    try:
        with open(STORE_FILE, "w", encoding="utf-8") as f:
            json.dump(store, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"(Couldn't save memory store: {e})")


def add_entry(store, category, text):
    if category not in ("facts", "projects", "preferences"):
        category = "facts"
    entries = store[category]
    if text not in entries:
        entries.append(text)
    if len(entries) > MAX_ENTRIES_PER_CATEGORY:
        store[category] = entries[-MAX_ENTRIES_PER_CATEGORY:]


def add_recent_turn(store, role, content):
    store["recent_turns"].append({"role": role, "content": content})
    if len(store["recent_turns"]) > MAX_RECENT_TURNS:
        store["recent_turns"] = store["recent_turns"][-MAX_RECENT_TURNS:]


def _score(entry_text, query_words):
    entry_words = set(entry_text.lower().split())
    return len(entry_words & query_words)


def retrieve_relevant(store, query, top_k=5):
    """Simple keyword-overlap retrieval - pulls the most relevant facts/
    projects/preferences for the CURRENT query, instead of dumping the
    entire memory into context every time. No embeddings/vector DB needed
    at this scale - keyword overlap is fast, free, and good enough."""
    query_words = {w.strip(".,!?").lower() for w in query.split() if len(w) > 2}
    scored = []
    for category in ("facts", "projects", "preferences"):
        for entry in store[category]:
            score = _score(entry, query_words)
            if score > 0:
                scored.append((score, category, entry))
    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:top_k]

    if not top:
        return ""

    lines = [f"[{cat}] {entry}" for _, cat, entry in top]
    return "Relevant things you know about this user:\n" + "\n".join(lines)