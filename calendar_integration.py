# calendar_integration.py
# Reads events from (and adds events to) Google Calendar so AURA can tell you what's
# ACTUALLY on your schedule, instead of guessing. Grounded, not guessed.
import os
import datetime
from zoneinfo import ZoneInfo
from config import LOCAL_TIMEZONE

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS_FILE = os.path.join(BASE_DIR, "google_credentials.json")
TOKEN_FILE = os.path.join(BASE_DIR, "google_token.json")


def _get_service():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    if not os.path.exists(CREDENTIALS_FILE):
        return None

    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if creds and not creds.valid and creds.expired and creds.refresh_token:
        from google.auth.exceptions import RefreshError
        try:
            creds.refresh(Request())
        except RefreshError as e:
            # The saved sign-in was revoked or ran out (Google kills them after 7 days while the
            # OAuth app is in "Testing", or when access is removed). Retrying it can never work,
            # so drop it and sign in again below.
            print(f"(calendar sign-in expired - asking Google again: {e})")
            os.remove(TOKEN_FILE)
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())

    return build("calendar", "v3", credentials=creds)


def _fetch_events_raw():
    """Returns a list of (start_datetime_or_None, formatted_string) for
    TODAY, or None if not set up yet, or an __ERROR__ string on failure."""
    try:
        service = _get_service()
    except Exception as e:
        return f"__ERROR__: {e}"

    if not service:
        return None

    tz = ZoneInfo(LOCAL_TIMEZONE)
    now = datetime.datetime.now(tz)
    start = datetime.datetime(now.year, now.month, now.day, 0, 0, 0, tzinfo=tz).isoformat()
    end = datetime.datetime(now.year, now.month, now.day, 23, 59, 59, tzinfo=tz).isoformat()

    try:
        events_result = service.events().list(
            calendarId="primary", timeMin=start, timeMax=end,
            singleEvents=True, orderBy="startTime"
        ).execute()
        events = events_result.get("items", [])
    except Exception as e:
        return f"__ERROR__: {e}"

    results = []
    for event in events:
        start_info = event["start"].get("dateTime", event["start"].get("date"))
        summary = event.get("summary", "(no title)")
        if "T" in start_info:
            dt = datetime.datetime.fromisoformat(start_info)
            time_str = dt.strftime("%I:%M %p").lstrip("0")
            results.append((dt, f"{time_str}: {summary}"))
        else:
            results.append((None, f"All day: {summary}"))
    return results


def get_todays_events():
    """All of today's events, formatted."""
    raw = _fetch_events_raw()
    if raw is None or isinstance(raw, str):
        return raw
    return [text for _, text in raw]


def get_remaining_events_today():
    """Only events that haven't started yet, based on the REAL current
    time - used for afternoon/evening check-ins that shouldn't repeat
    things already done."""
    raw = _fetch_events_raw()
    if raw is None or isinstance(raw, str):
        return raw
    tz = ZoneInfo(LOCAL_TIMEZONE)
    now = datetime.datetime.now(tz)
    return [text for dt, text in raw if dt is None or dt >= now]


def create_event(summary, start_dt, end_dt=None, all_day=False):
    """Creates a REAL event on the user's Google Calendar. Returns
    (success: bool, error_message_or_None)."""
    try:
        service = _get_service()
    except Exception as e:
        return False, str(e)
    if not service:
        return False, "Google Calendar isn't connected yet."

    if all_day:
        end_date = end_dt or start_dt
        event_body = {
            "summary": summary,
            "start": {"date": start_dt.strftime("%Y-%m-%d")},
            "end": {"date": end_date.strftime("%Y-%m-%d")},
        }
    else:
        if end_dt is None:
            end_dt = start_dt + datetime.timedelta(hours=1)
        event_body = {
            "summary": summary,
            "start": {"dateTime": start_dt.isoformat(), "timeZone": LOCAL_TIMEZONE},
            "end": {"dateTime": end_dt.isoformat(), "timeZone": LOCAL_TIMEZONE},
        }

    try:
        service.events().insert(calendarId="primary", body=event_body).execute()
        return True, None
    except Exception as e:
        return False, str(e)