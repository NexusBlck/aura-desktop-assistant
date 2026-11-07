# forget.py
# Removes a specific detail from the structured memory store. Much more
# reliable than the old approach (asking the AI to rewrite an entire raw
# chat transcript) - now it's just picking which small entries to delete
# from a short, clean list.
from ai import ask_json

FORGET_PROMPT = """
You are helping remove ONE specific detail from a list of remembered facts
about a user. You'll get the detail to forget, and a numbered list of
remembered entries (with category).

Return ONLY a JSON array of the index numbers of entries that should be
REMOVED because they're about that detail. If none should be removed,
return an empty array: []. No other text.
"""


def forget_detail(store, detail_to_forget):
    all_entries = []
    for category in ("facts", "projects", "preferences"):
        for entry in store[category]:
            all_entries.append({"category": category, "text": entry})

    if not all_entries:
        return store

    listing = "\n".join(f'{idx}: [{e["category"]}] {e["text"]}' for idx, e in enumerate(all_entries))
    user_prompt = f'Detail to forget: "{detail_to_forget}"\n\nEntries:\n{listing}'

    messages = [
        {"role": "system", "content": FORGET_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    indices_to_remove = ask_json(messages, default=[])
    if not isinstance(indices_to_remove, list):
        return store

    for i in indices_to_remove:
        if isinstance(i, int) and 0 <= i < len(all_entries):
            item = all_entries[i]
            cat, text = item["category"], item["text"]
            if text in store[cat]:
                store[cat].remove(text)

    return store