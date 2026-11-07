# notes.py
# The HUD's quick notepad - one scratchpad, saved automatically as you type.
import os

NOTES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aura_notes.txt")
MAX_CHARS = 50_000


def get_notes():
    try:
        with open(NOTES_FILE, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def save_notes(text):
    try:
        with open(NOTES_FILE, "w", encoding="utf-8") as f:
            f.write((text or "")[:MAX_CHARS])
        return True
    except OSError as e:
        print(f"(couldn't save notes: {e})")
        return False
