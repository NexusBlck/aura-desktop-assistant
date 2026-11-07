# event_parser.py
# Converts a natural language event description ("BJJ tonight at 7",
# "research deadline in 2 days") into structured date/time info, using the
# REAL current date/time as grounding - so relative expressions are computed
# correctly instead of guessed by the model.
import datetime
from zoneinfo import ZoneInfo
from ai import ask_json
from config import LOCAL_TIMEZONE

PARSE_PROMPT = """
You convert a natural language event description into structured JSON for
a calendar. You are given the REAL current date/time - use it to correctly
resolve relative expressions like "tomorrow", "tonight", "in 2 days",
"next Friday", etc. Always compute the date from the given real current
date/time - never guess.

Respond ONLY with JSON in this exact shape, nothing else:
{
  "summary": "short event title",
  "all_day": true or false,
  "date": "YYYY-MM-DD",
  "start_time": "HH:MM in 24-hour format, only if all_day is false, else null",
  "end_time": "HH:MM in 24-hour format if a duration/end is clearly implied, else null"
}

If no specific time is mentioned (e.g. "research deadline in 2 days"), set
all_day to true. If a time is mentioned (e.g. "BJJ tonight at 7pm"), set
all_day to false and fill start_time. If no end time is implied, leave
end_time as null (a default 1-hour duration will be used).
"""


def parse_event(description):
    """Returns {"summary", "all_day", "start": datetime, "end": datetime_or_None}
    or None if parsing failed."""
    now = datetime.datetime.now(ZoneInfo(LOCAL_TIMEZONE))
    real_now_str = now.strftime("%A, %Y-%m-%d %H:%M")

    messages = [
        {"role": "system", "content": PARSE_PROMPT},
        {"role": "user", "content": f'Real current date/time: {real_now_str}\n\nEvent description: "{description}"'},
    ]
    data = ask_json(messages)
    if not isinstance(data, dict):
        return None

    try:
        tz = ZoneInfo(LOCAL_TIMEZONE)
        date_parts = [int(x) for x in data["date"].split("-")]

        if data.get("all_day"):
            start_dt = datetime.datetime(*date_parts, tzinfo=tz)
            return {"summary": data.get("summary", description), "all_day": True, "start": start_dt, "end": None}

        start_time = data.get("start_time") or "09:00"
        h, m = [int(x) for x in start_time.split(":")]
        start_dt = datetime.datetime(*date_parts, h, m, tzinfo=tz)

        end_dt = None
        if data.get("end_time"):
            eh, em = [int(x) for x in data["end_time"].split(":")]
            end_dt = datetime.datetime(*date_parts, eh, em, tzinfo=tz)

        return {"summary": data.get("summary", description), "all_day": False, "start": start_dt, "end": end_dt}
    except Exception:
        return None