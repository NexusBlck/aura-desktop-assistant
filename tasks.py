# tasks.py
# A simple persistent tasks/ideas list - for things like "Instagram idea
# pending" or a research deadline note. Separate from calendar events,
# since not everything is a scheduled event.
import os
import json
import datetime

TASKS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aura_tasks.json")


def load_tasks():
    if os.path.exists(TASKS_FILE):
        try:
            with open(TASKS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []


def save_tasks(tasks):
    try:
        with open(TASKS_FILE, "w", encoding="utf-8") as f:
            json.dump(tasks, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"(Couldn't save tasks: {e})")


def add_task(text):
    tasks = load_tasks()
    tasks.append({
        "text": text,
        "added": datetime.datetime.now().isoformat(),
        "done": False,
        "completed_at": None,
    })
    save_tasks(tasks)


def complete_task(text_fragment):
    """Marks the first matching pending task as done. Returns its text, or None."""
    tasks = load_tasks()
    fragment = text_fragment.lower()
    for t in tasks:
        if not t["done"] and fragment in t["text"].lower():
            t["done"] = True
            t["completed_at"] = datetime.datetime.now().isoformat()
            save_tasks(tasks)
            return t["text"]
    return None


def get_pending_tasks():
    return [t for t in load_tasks() if not t["done"]]