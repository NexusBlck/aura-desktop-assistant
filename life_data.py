# life_data.py
# Tracks real things AURA already touches - sessions you log, tasks you
# complete, papers you save, and journal check-ins YOU choose to do. No
# unprompted probing, no data that wasn't explicitly given.
import os
import json
import datetime

LIFE_DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aura_life_data.json")
DEFAULT = {"sessions": [], "journal": []}


def _parse_ts(value):
    """Parses an ISO timestamp into a NAIVE local datetime. Timestamps come
    from several places (Python's now(), the LLM, the log dialog's JS which
    may send 'Z' or '+05:00'), and comparing timezone-aware with naive
    datetimes raises TypeError - so everything is normalized here."""
    if isinstance(value, datetime.datetime):
        dt = value
    else:
        dt = datetime.datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


def load_data():
    if os.path.exists(LIFE_DATA_FILE):
        try:
            with open(LIFE_DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            for k, v in DEFAULT.items():
                data.setdefault(k, list(v))
            return data
        except Exception:
            pass
    return {k: list(v) for k, v in DEFAULT.items()}


def save_data(data):
    try:
        with open(LIFE_DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"(Couldn't save life data: {e})")


def log_session(session_type, timestamp=None, rating=None, minutes=None):
    data = load_data()
    entry = {
        "type": (session_type or "session").lower().strip(),
        "timestamp": _parse_ts(timestamp).isoformat() if timestamp else datetime.datetime.now().isoformat(),
        "rating": rating,
    }
    if minutes is not None:
        entry["minutes"] = minutes  # e.g. how long a focus session lasted
    data["sessions"].append(entry)
    save_data(data)


def focus_minutes_today():
    data = load_data()
    today_start = datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return sum(s.get("minutes") or 0 for s in data["sessions"]
               if s.get("type") == "focus" and _parse_ts(s["timestamp"]) >= today_start)


def log_journal_entry(mood, good_thing, on_mind):
    data = load_data()
    data["journal"].append({
        "timestamp": datetime.datetime.now().isoformat(),
        "mood": mood,
        "good_thing": good_thing,
        "on_mind": on_mind,
    })
    save_data(data)


def get_today_summary():
    from tasks import load_tasks
    from paper_library import load_library

    data = load_data()
    now = datetime.datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    sessions_today = [s for s in data["sessions"] if _parse_ts(s["timestamp"]) >= today_start]
    tasks = load_tasks()
    tasks_done_today = [t for t in tasks if t.get("completed_at") and _parse_ts(t["completed_at"]) >= today_start]
    papers = load_library()
    papers_today = [p for p in papers if _parse_ts(p["added"]) >= today_start]

    return {
        "sessions_today": len(sessions_today),
        "tasks_done_today": len(tasks_done_today),
        "papers_today": len(papers_today),
    }


def get_session_type_summary(session_type):
    data = load_data()
    session_type = (session_type or "").lower().strip()
    matching = [s for s in data["sessions"] if s["type"] == session_type]
    count = len(matching)
    ratings = [s["rating"] for s in matching if isinstance(s.get("rating"), (int, float))]
    avg_rating = round(sum(ratings) / len(ratings), 1) if ratings else None
    return {"count": count, "avg_rating": avg_rating}


def parse_session_time(text):
    """Converts something like 'just now', 'this morning', 'yesterday at 6pm'
    into a real timestamp, grounded with the actual current date/time so
    relative phrases resolve correctly instead of being guessed."""
    from ai import ask_json

    now = datetime.datetime.now()
    prompt = f"""Real current date/time: {now.strftime('%A, %Y-%m-%d %H:%M')}

The user said this about when something happened: "{text}"

Respond ONLY with JSON: {{"iso_timestamp": "YYYY-MM-DDTHH:MM:SS"}}
Resolve relative phrases (e.g. "just now", "this morning", "yesterday
evening") using the real current date/time above. If unclear, use the
current date/time."""
    data = ask_json([{"role": "user", "content": prompt}], default={})
    raw_ts = data.get("iso_timestamp") if isinstance(data, dict) else None
    try:
        return _parse_ts(raw_ts).isoformat() if raw_ts else now.isoformat()
    except ValueError:
        return now.isoformat()


def get_weekly_summary():
    from tasks import load_tasks
    from paper_library import load_library

    data = load_data()
    now = datetime.datetime.now()
    week_ago = now - datetime.timedelta(days=7)

    sessions = [s for s in data["sessions"] if _parse_ts(s["timestamp"]) >= week_ago]
    journal = [j for j in data["journal"] if _parse_ts(j["timestamp"]) >= week_ago]

    tasks = load_tasks()
    tasks_done = [t for t in tasks if t.get("completed_at") and _parse_ts(t["completed_at"]) >= week_ago]

    papers = load_library()
    papers_added = [p for p in papers if _parse_ts(p["added"]) >= week_ago]

    session_counts = {}
    for s in sessions:
        session_counts[s["type"]] = session_counts.get(s["type"], 0) + 1

    moods = [j["mood"] for j in journal if isinstance(j.get("mood"), (int, float))]
    avg_mood = round(sum(moods) / len(moods), 1) if moods else None

    return {
        "sessions_by_type": session_counts,
        "total_sessions": len(sessions),
        "tasks_completed": len(tasks_done),
        "papers_added": len(papers_added),
        "journal_entries": len(journal),
        "avg_mood": avg_mood,
    }


def get_trend_data(weeks=8):
    from tasks import load_tasks
    from paper_library import load_library

    data = load_data()
    tasks = load_tasks()
    papers = load_library()
    now = datetime.datetime.now()

    week_labels, tasks_per_week, papers_per_week, mood_per_week = [], [], [], []
    rating_per_week = []
    session_type_totals = {}

    for i in range(weeks - 1, -1, -1):
        week_start = (now - datetime.timedelta(weeks=i, days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        week_end = week_start + datetime.timedelta(days=7)
        week_labels.append(week_start.strftime("%b %d"))

        t_count = sum(1 for t in tasks if t.get("completed_at") and week_start <= _parse_ts(t["completed_at"]) < week_end)
        tasks_per_week.append(t_count)

        p_count = sum(1 for p in papers if week_start <= _parse_ts(p["added"]) < week_end)
        papers_per_week.append(p_count)

        week_journals = [j for j in data["journal"] if week_start <= _parse_ts(j["timestamp"]) < week_end]
        moods = [j["mood"] for j in week_journals if isinstance(j.get("mood"), (int, float))]
        mood_per_week.append(round(sum(moods) / len(moods), 1) if moods else None)

        week_sessions = [s for s in data["sessions"] if week_start <= _parse_ts(s["timestamp"]) < week_end]
        ratings = [s["rating"] for s in week_sessions if isinstance(s.get("rating"), (int, float))]
        rating_per_week.append(round(sum(ratings) / len(ratings), 1) if ratings else None)

        for s in week_sessions:
            session_type_totals[s["type"]] = session_type_totals.get(s["type"], 0) + 1

    recent_journal = sorted(data["journal"], key=lambda j: j["timestamp"], reverse=True)[:10]

    return {
        "week_labels": week_labels,
        "tasks_per_week": tasks_per_week,
        "papers_per_week": papers_per_week,
        "mood_per_week": mood_per_week,
        "rating_per_week": rating_per_week,
        "session_type_totals": session_type_totals,
        "recent_journal": recent_journal,
    }