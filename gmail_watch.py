# gmail_watch.py
# AURA's inbox assistant. Connect once ("connect my Gmail"), then AURA:
#   * watches the inbox and ANNOUNCES IMPORTANT emails out loud the moment they
#     arrive - every email from a real person, plus the few organisation emails
#     that actually matter (payment declined, security alerts, deadlines, the
#     conference opening abstracts you care about...) - and ignores the noise;
#   * keeps REMINDING you (30 min, 1 h, then every 2 h) until you open, reply to,
#     archive or delete the email - or tell AURA "noted";
#   * stays quiet during quiet hours and catches you up afterwards;
#   * adds deadlines it finds in important emails to your task list.
# And on request: "tell me all the emails from ACC" (last 10 days, each one
# clearly numbered), "check my email", "read me the email from X", "reply to it
# saying ..." (saved as a DRAFT - AURA never sends), "archive it", "mark it as
# read", "remind me about it tomorrow", "always / never tell me about X".
#
# Permission used: gmail.modify (read, label, archive, drafts). Nothing is ever
# sent or permanently deleted. What counts as important can be tuned in
# aura_mail_rules.json (created on first run).
import base64
import datetime
import html
import json
import os
import re
import threading
import time
import webbrowser

import config
from email.mime.text import MIMEText
from email.utils import parseaddr, parsedate_to_datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS_FILE = os.path.join(BASE_DIR, "google_credentials.json")
TOKEN_FILE = os.path.join(BASE_DIR, "google_gmail_token.json")
STATE_FILE = os.path.join(BASE_DIR, "aura_mail_state.json")
RULES_FILE = os.path.join(BASE_DIR, "aura_mail_rules.json")
DIGEST_DIR = os.path.join(BASE_DIR, "Face", "mail")
SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

DEFAULT_RULES = {
    "_help": "AURA's email rules. 'interests' = what makes an ORGANISATION email worth announcing "
             "(people are always announced). Senders can be a name, an address or a domain. "
             "Times are 24h. Edit freely - AURA reads this file every few minutes.",
    "interests": [
        "my school, university or workplace: exams, results, deadlines, fees, timetable or schedule changes",
        "research and publishing: calls for papers, conference submissions, acceptance or rejection decisions",
        "scholarships, internships, job applications, interviews, offers",
        "my own accounts: payments failed or declined, bills due, subscriptions about to end, refunds, orders and "
        "deliveries, bookings and appointments, security alerts, password or login problems",
    ],
    "always_announce_from": [],
    "never_announce_from": [],
    "quiet_hours": ["00:00", "08:00"],
    "check_every_seconds": 60,
    "remind_after_minutes": [30, 60, 120],
    "summary_days": 10,
}

URGENT_HINT = re.compile(
    r"payment|declin|fail|overdue|past due|suspend|security|sign.?in|log.?in|password|verify|verif|locked|unusual|"
    r"invoice|bill|due|deadline|abstract|submission|call for|accept|reject|decision|interview|offer|admission|"
    r"exam|result|appointment|cancel|refund|deliver|shipped|order|ticket|booking|confirm|action required|urgent|"
    r"important|expir|renew|reminder|registration|scholarship|elective|fee|hold", re.I)
AUTOMATED_LOCAL = re.compile(
    r"no-?reply|do-?not-?reply|donotreply|notification|notify|alert|news|newsletter|info|marketing|hello|support|"
    r"team|mailer|updates?|billing|account|service|admin|contact|digest|bounce|offers?|promo|deals|community|events?",
    re.I)
NOISE_LABELS = {"CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL", "CATEGORY_FORUMS"}

_lock = threading.RLock()
_state = None
_watcher = None
_stop = threading.Event()
_hooks = {"speak": print, "toast": None}
_last_announce = {"at": 0, "who": ""}


# ------------------------------------------------------------------ files
def _load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def rules():
    r = _load_json(RULES_FILE, None)
    if r is None:
        _save_json(RULES_FILE, DEFAULT_RULES)
        r = dict(DEFAULT_RULES)
    for k, v in DEFAULT_RULES.items():
        r.setdefault(k, v)
    return r


def _st():
    global _state
    with _lock:
        if _state is None:
            _state = _load_json(STATE_FILE, {"seen": {}, "pending": {}, "held": [], "tasked": []})
        return _state


def _save_state():
    with _lock:
        s = _st()
        # keep the seen-list from growing forever
        if len(s["seen"]) > 3000:
            for k in sorted(s["seen"], key=lambda k: s["seen"][k].get("t", 0))[:1000]:
                s["seen"].pop(k, None)
        _save_json(STATE_FILE, s)


# ------------------------------------------------------------------ Gmail connection
def connected():
    return os.path.exists(TOKEN_FILE)


def _service(interactive=False):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES) if os.path.exists(TOKEN_FILE) else None
    if creds and not creds.valid and creds.expired and creds.refresh_token:
        from google.auth.exceptions import RefreshError
        try:
            creds.refresh(Request())
            with open(TOKEN_FILE, "w") as f:
                f.write(creds.to_json())
        except RefreshError:
            # The saved sign-in was revoked or ran out (Google kills them after 7 days while the
            # OAuth app is in "Testing"). Retrying it can never work - when the user asked to
            # connect, drop it and sign in again below; otherwise report it.
            if not interactive:
                raise
            os.remove(TOKEN_FILE)
            creds = None
    if not creds or not creds.valid:
        if interactive:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0, prompt="consent")   # opens Google's sign-in page in the browser
        else:
            raise RuntimeError("Gmail isn't connected - say 'connect my Gmail'.")
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _friendly_error(e):
    text = str(e)
    if "accessNotConfigured" in text or "has not been used in project" in text or "is disabled" in text:
        project = _load_json(CREDENTIALS_FILE, {}).get("installed", {}).get("project_id", "")
        return ("the Gmail API isn't switched on for AURA's Google project yet. Open "
                f"https://console.cloud.google.com/apis/library/gmail.googleapis.com?project={project} , click Enable, "
                "then say 'connect my Gmail' again.")
    if "invalid_grant" in text:
        return "Google says AURA's Gmail permission expired - say 'connect my Gmail' to sign in again."
    return text[:200]


def connect():
    """Opens Google's sign-in page (you approve it there), then starts watching."""
    if not os.path.exists(CREDENTIALS_FILE):
        return "I need google_credentials.json (the same one Calendar uses) in the AURA folder first."
    try:
        svc = _service(interactive=True)
        me = svc.users().getProfile(userId="me").execute()
    except Exception as e:
        return f"I couldn't connect Gmail: {_friendly_error(e)}"
    rules()
    start()
    return (f"Gmail connected - {me.get('emailAddress')}. I'll announce important emails as they arrive and keep "
            "reminding you until you deal with them. Say 'noted' to stop a reminder.")


# ------------------------------------------------------------------ reading messages
def _headers(msg):
    return {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}


def _body_text(payload, limit=4000):
    """Plain text of an email (falls back to stripped HTML)."""
    found = {"plain": "", "html": ""}

    def walk(p):
        mime = p.get("mimeType", "")
        data = p.get("body", {}).get("data")
        if data and mime in ("text/plain", "text/html"):
            text = base64.urlsafe_b64decode(data + "===").decode("utf-8", "replace")
            key = "plain" if mime == "text/plain" else "html"
            if not found[key]:
                found[key] = text
        for part in p.get("parts", []) or []:
            walk(part)
    walk(payload or {})
    text = found["plain"]
    if not text.strip() and found["html"]:
        text = re.sub(r"(?is)<(script|style).*?</\1>", " ", found["html"])
        text = re.sub(r"(?s)<br\s*/?>|</p>|</div>|</tr>", "\n", text)
        text = html.unescape(re.sub(r"(?s)<[^>]+>", " ", text))
    text = re.sub(r"[ \t‌​\xa0]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    return text[:limit]


def _info(msg, with_body=False):
    h = _headers(msg)
    name, addr = parseaddr(h.get("from", ""))
    try:
        when = parsedate_to_datetime(h.get("date")) if h.get("date") else None
    except Exception:
        when = None
    info = {
        "id": msg["id"], "threadId": msg.get("threadId"), "labels": msg.get("labelIds", []),
        "from_name": name or addr.split("@")[0], "from_addr": addr.lower(), "subject": h.get("subject", "(no subject)"),
        "snippet": html.unescape(msg.get("snippet", "")), "date": when.isoformat() if when else "",
        "internal": int(msg.get("internalDate", 0)), "message_id": h.get("message-id", ""),
        "reply_to": h.get("reply-to", ""),
        "bulk": bool(h.get("list-unsubscribe") or h.get("list-id") or h.get("precedence", "").lower() in ("bulk", "list", "junk")
                     or h.get("auto-submitted", "no").lower() != "no"),
    }
    if with_body:
        info["body"] = _body_text(msg.get("payload"))
    return info


def _looks_like_person(m):
    local = m["from_addr"].split("@")[0]
    if m["bulk"] or AUTOMATED_LOCAL.search(local) or NOISE_LABELS & set(m["labels"]):
        return False
    name = m["from_name"]
    return bool(re.match(r"^[A-Za-z][A-Za-z.'\- ]{1,40}$", name)) and len(name.split()) <= 4 and \
        not re.search(r"\b(team|inc|ltd|llc|university|bank|support|store|group|official|news|club|services?)\b", name, re.I)


def _matches(sender_rule, m):
    rule = sender_rule.lower().strip()
    return bool(rule) and (rule in m["from_addr"] or rule in m["from_name"].lower())


# ------------------------------------------------------------------ deciding what's important
def _classify(svc, msgs):
    """Returns {id: verdict} for new messages. People and rule-matches are decided directly;
    clear marketing noise is skipped without the AI; everything else goes to the AI in batches."""
    r = rules()
    verdicts, ask = {}, []
    for m in msgs:
        if any(_matches(s, m) for s in r["never_announce_from"]):
            verdicts[m["id"]] = {"important": False, "why": "muted sender"}
        elif any(_matches(s, m) for s in r["always_announce_from"]):
            ask.append((m, True))
        elif _looks_like_person(m):
            ask.append((m, True))          # important - the AI only writes the summary
        elif (NOISE_LABELS & set(m["labels"]) or m["bulk"]) and not URGENT_HINT.search(m["subject"] + " " + m["snippet"]):
            verdicts[m["id"]] = {"important": False, "why": "marketing / newsletter"}
        else:
            ask.append((m, None))
    for i in range(0, len(ask), 6):
        batch = ask[i:i + 6]
        try:
            verdicts.update(_ask_ai(svc, batch, r))
        except Exception as e:
            # AI unavailable (e.g. daily limit): still announce people, using the subject; the rest
            # get no verdict, so they stay "unseen" and are judged on a later check.
            print(f"(mail: couldn't judge emails right now: {str(e)[:120]})")
            for m, forced in batch:
                if forced:
                    verdicts[m["id"]] = {"important": True, "summary": f"Subject: {m['subject']}."}
    return verdicts


def _ask_ai(svc, batch, r):
    from autocoder import _paced_json
    try:
        from memory_store import load_store, retrieve_relevant
        about = retrieve_relevant(load_store(), "email work study research university interests", top_k=6)
    except Exception:
        about = ""
    blocks = []
    for m, forced in batch:
        try:
            full = svc.users().messages().get(userId="me", id=m["id"], format="full").execute()
            body = _body_text(full.get("payload"), 900)
        except Exception:
            body = m["snippet"]
        hint = "ALWAYS IMPORTANT (a real person or a VIP sender) - just summarise" if forced else \
            ("looks like a person" if _looks_like_person(m) else "organisation / automated")
        blocks.append(f'<email id="{m["id"]}">\nFrom: {m["from_name"]} <{m["from_addr"]}>  [{hint}]\n'
                      f'Gmail labels: {", ".join(l for l in m["labels"] if l.startswith("CATEGORY") or l == "IMPORTANT")}\n'
                      f'Subject: {m["subject"]}\nText: {body[:900]}\n</email>')
    today = datetime.date.today().isoformat()
    prompt = f"""You screen the inbox of {config.describe_user()} and decide which emails deserve to be ANNOUNCED out loud.

What they care about from organisations:
{chr(10).join('- ' + x for x in r['interests'])}
{('What AURA knows about them:' + chr(10) + about) if about else ''}

IMPORTANT = emails from real people (always), and organisation emails that need them to ACT or that they'd
hate to miss: payment declined/failed, security or login alerts, account problems, bills or deadlines,
decisions about them (acceptance, results, interviews), changes to their bookings/orders/classes, and
opportunities matching their interests (e.g. a conference they follow opening abstract submissions).
NOT important = marketing, newsletters, recommendations, "new shows this week", sales, social notifications,
generic announcements, receipts for routine things - even from organisations they care about.

Today is {today}. Emails:
{chr(10).join(blocks)}

Reply ONLY with JSON: {{"emails": [{{"id": "...", "important": true/false, "kind": "person|action|money|security|deadline|decision|opportunity|uni|booking|other",
"summary": "ONE short spoken sentence saying what it is and what they need to do (no greetings)",
"deadline": "YYYY-MM-DD or null", "deadline_what": "what is due, or null"}}]}}"""
    data = _paced_json(prompt, temperature=0.1, expect=180 * len(batch) + 200, default={}) or {}
    out = {}
    for e in data.get("emails") or []:
        if isinstance(e, dict) and e.get("id"):
            out[e["id"]] = e
    for m, forced in batch:   # never drop a person's email just because the AI reply was incomplete
        v = out.get(m["id"]) or {"important": bool(forced), "summary": m["subject"]}
        if forced:
            v["important"] = True
        out[m["id"]] = v
    return out


# ------------------------------------------------------------------ announcing + reminding
def _quiet_now():
    """Quiet hours, Do Not Disturb, or you're away from the PC -> hold announcements for later."""
    try:
        import presence
        if not presence.proactive_ok():
            return True
    except Exception:
        pass
    try:
        a, b = rules()["quiet_hours"]
        now = datetime.datetime.now().strftime("%H:%M")
        return (a <= now < b) if a <= b else (now >= a or now < b)
    except Exception:
        return False


def _say(text):
    _hooks["speak"](text)


def _toast(title, body):
    if _hooks.get("toast"):
        try:
            _hooks["toast"](title, body, "reminder")
        except Exception:
            pass


def _announce(entries, catching_up=False):
    if not entries:
        return
    for e in entries:
        _toast(f"Email - {e['from_name']}", e["summary"])
    if len(entries) == 1:
        e = entries[0]
        _say(("While you were away: " if catching_up else "") + f"New email from {e['from_name']}. {e['summary']}")
    else:
        parts = "; ".join(f"from {e['from_name']}: {e['summary']}" for e in entries[:5])
        more = f" And {len(entries) - 5} more." if len(entries) > 5 else ""
        _say(f"{'While you were away, you got' if catching_up else 'You have'} {len(entries)} important emails. {parts}.{more}")
    _last_announce.update(at=time.time(), who=entries[-1]["from_name"])


def _schedule_next(p):
    steps = rules()["remind_after_minutes"] or [30]
    n = p.get("reminders", 0)
    p["next"] = time.time() + 60 * steps[min(n, len(steps) - 1)]


def _add_deadline_task(e, v):
    if not v.get("deadline") or e["id"] in _st()["tasked"]:
        return
    try:
        from tasks import add_task
        add_task(f"📧 {v.get('deadline_what') or e['subject']} - due {v['deadline']} ({e['from_name']})")
        _st()["tasked"].append(e["id"])
        _st()["tasked"] = _st()["tasked"][-300:]
    except Exception as ex:
        print(f"(mail: couldn't add task: {ex})")


def _resolved(svc, p):
    """True once you've opened, replied to, archived or deleted the email."""
    try:
        msg = svc.users().messages().get(userId="me", id=p["id"], format="minimal").execute()
    except Exception as e:
        return "404" in str(e) or "notFound" in str(e)
    labels = set(msg.get("labelIds", []))
    if "UNREAD" not in labels or "INBOX" not in labels or "TRASH" in labels:
        return True
    try:
        thread = svc.users().threads().get(userId="me", id=p["threadId"], format="minimal").execute()
        return any("SENT" in t.get("labelIds", []) and int(t.get("internalDate", 0)) > p.get("internal", 0)
                   for t in thread.get("messages", []))
    except Exception:
        return False


def _cycle(svc, first_run=False):
    s = _st()
    # 1) new mail
    query = "in:inbox newer_than:1d" if first_run else "in:inbox newer_than:2d"
    resp = svc.users().messages().list(userId="me", q=query, maxResults=30).execute()
    new = []
    for ref in resp.get("messages", []) or []:
        if ref["id"] in s["seen"]:
            continue
        msg = svc.users().messages().get(userId="me", id=ref["id"], format="metadata",
                                         metadataHeaders=["From", "Subject", "Date", "List-Unsubscribe", "List-Id",
                                                          "Precedence", "Auto-Submitted", "Message-ID", "Reply-To"]).execute()
        info = _info(msg)
        if first_run and "UNREAD" not in info["labels"]:
            s["seen"][info["id"]] = {"t": time.time(), "important": False}
            continue
        new.append(info)
    if new:
        verdicts = _classify(svc, new)
        fresh = []
        for m in new:
            v = verdicts.get(m["id"])
            if v is None:
                continue   # not judged yet - try again next check
            s["seen"][m["id"]] = {"t": time.time(), "important": bool(v.get("important"))}
            if v.get("important") and "UNREAD" in m["labels"]:
                entry = {**{k: m[k] for k in ("id", "threadId", "from_name", "from_addr", "subject", "internal")},
                         "summary": (v.get("summary") or m["subject"]).strip(), "kind": v.get("kind", ""),
                         "deadline": v.get("deadline"), "reminders": 0, "announced": time.time()}
                _schedule_next(entry)
                s["pending"][m["id"]] = entry
                fresh.append(entry)
                _add_deadline_task(m, v)
        if fresh:
            if _quiet_now():
                s["held"].extend(e["id"] for e in fresh)
            else:
                _announce(fresh, catching_up=first_run)
    # 2) after quiet hours, catch up on what arrived overnight
    if s["held"] and not _quiet_now():
        held = [s["pending"][i] for i in s["held"] if i in s["pending"]]
        s["held"] = []
        _announce(held, catching_up=True)
    # 3) stop reminding about what you've dealt with; remind about the rest
    due = []
    for pid, p in list(s["pending"].items()):
        if _resolved(svc, p):
            s["pending"].pop(pid, None)
            continue
        if time.time() >= p.get("next", 0) and pid not in s["held"]:
            due.append(p)
    if due and not _quiet_now():
        if len(due) == 1:
            p = due[0]
            _say(f"Reminder: you haven't dealt with the email from {p['from_name']} yet - {p['summary']} "
                 "Say 'noted' if you've seen it.")
        else:
            names = ", ".join(sorted({p["from_name"] for p in due}))
            _say(f"Reminder: {len(due)} important emails are still waiting - from {names}. Say 'noted' if you've seen them.")
        for p in due:
            p["reminders"] = p.get("reminders", 0) + 1
            _schedule_next(p)
    _save_state()


def _loop():
    first = not _st()["seen"]
    while not _stop.is_set():
        try:
            svc = _service()
            _cycle(svc, first_run=first)
            first = False
        except Exception as e:
            print(f"(mail watcher: {_friendly_error(e)})")
        _stop.wait(max(20, int(rules().get("check_every_seconds", 60))))


def start(speak=None, toast=None):
    """Starts the background watcher (if Gmail is connected)."""
    global _watcher
    if speak:
        _hooks["speak"] = speak
    if toast:
        _hooks["toast"] = toast
    if not connected() or (_watcher and _watcher.is_alive()):
        return
    _stop.clear()
    _watcher = threading.Thread(target=_loop, daemon=True, name="gmail-watch")
    _watcher.start()


# ------------------------------------------------------------------ on-request features
def _search(svc, q, n=25, body=True):
    resp = svc.users().messages().list(userId="me", q=q, maxResults=n).execute()
    out = []
    for ref in resp.get("messages", []) or []:
        msg = svc.users().messages().get(userId="me", id=ref["id"], format="full" if body else "metadata").execute()
        out.append(_info(msg, with_body=body))
    out.sort(key=lambda m: m["internal"])
    return out


def _sender_query(sender, days):
    sender = sender.strip()
    if "@" in sender or re.match(r"^[\w.-]+\.[a-z]{2,}$", sender):
        who = f"from:{sender}"
    else:
        who = f'from:"{sender}"' if " " in sender else f"from:{sender}"
    return f"{who} newer_than:{int(days)}d"


def _nice_date(m):
    try:
        d = datetime.datetime.fromisoformat(m["date"])
        return d.strftime("%A %d %B").replace(" 0", " ")
    except Exception:
        return ""


def emails_from(sender, days=None):
    """Summarises EVERY email from a sender in the last N days, clearly numbered. Returns spoken text."""
    from autocoder import _paced_json
    days = days or rules()["summary_days"]
    svc = _service()
    msgs = _search(svc, _sender_query(sender, days))
    if not msgs:
        return f"I didn't find any emails from {sender} in the last {days} days."
    summaries = {}
    for i in range(0, len(msgs), 6):
        chunk = msgs[i:i + 6]
        blocks = "\n".join(f'<email n="{i + k + 1}">\nSubject: {m["subject"]}\nDate: {_nice_date(m)}\nText: {m.get("body", m["snippet"])[:700]}\n</email>'
                           for k, m in enumerate(chunk))
        data = _paced_json(f"""Summarise each email for {config.describe_user()}. For each: 1-2 plain sentences with what
it's actually about and anything they need to do or note (dates, deadlines, amounts). Mark relevant=true only if
it matters to them (action needed, deadline, decision, opportunity matching: {'; '.join(rules()['interests'][:3])}).
{blocks}
Reply ONLY with JSON: {{"emails": [{{"n": number, "summary": "...", "relevant": true/false}}]}}""",
                           expect=120 * len(chunk) + 150, default={}) or {}
        for e in data.get("emails") or []:
            try:
                summaries[int(e["n"])] = e
            except (KeyError, TypeError, ValueError):
                continue
    total = len(msgs)
    page = _digest_page(sender, days, msgs, summaries)
    rel = sum(1 for n in summaries.values() if n.get("relevant"))
    parts = [f"You got {total} email{'s' if total != 1 else ''} from {sender} in the last {days} days"
             + (f", and {rel} of them look{'s' if rel == 1 else ''} worth your attention." if rel else ", none of them urgent.")
             + " I've opened them on screen too."]
    for n, m in enumerate(msgs, 1):
        s = summaries.get(n, {})
        flag = "This one matters. " if s.get("relevant") else ""
        parts.append(f"Email {n} of {total}, {_nice_date(m)}, titled {m['subject']}. {flag}{s.get('summary') or m['snippet'][:160]} "
                     f"End of email {n}.")
    if page:
        webbrowser.open(page)
    return " ".join(parts)


def _digest_page(sender, days, msgs, summaries):
    """A readable page: one card per email, clearly separated, with an 'Open in Gmail' link."""
    try:
        os.makedirs(DIGEST_DIR, exist_ok=True)
        cards = []
        for n, m in enumerate(msgs, 1):
            s = summaries.get(n, {})
            cards.append(f"""<article class="card{' hot' if s.get('relevant') else ''}">
  <div class="top"><span class="num">Email {n} of {len(msgs)}</span><span class="date">{html.escape(_nice_date(m))}</span>
  {'<span class="badge">Worth your attention</span>' if s.get('relevant') else ''}</div>
  <h2>{html.escape(m['subject'])}</h2>
  <p>{html.escape(s.get('summary') or m['snippet'])}</p>
  <a href="https://mail.google.com/mail/u/0/#all/{m['threadId']}" target="_blank">Open in Gmail ↗</a>
</article>""")
        name = re.sub(r"[^a-z0-9]+", "-", sender.lower()).strip("-") or "sender"
        path = os.path.join(DIGEST_DIR, f"{name}.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"""<!doctype html><html><head><meta charset="utf-8"><title>Emails from {html.escape(sender)}</title>
<style>body{{margin:0;background:#02050a;color:#d6f6ff;font:15px/1.55 'Segoe UI',sans-serif;padding:32px 16px}}
main{{max-width:760px;margin:0 auto}} h1{{font:800 24px 'Segoe UI';color:#9ef3ff;margin:0 0 4px}} .sub{{color:#6f97a6;margin-bottom:24px}}
.card{{background:#07121b;border:1px solid #1b5366;border-left:4px solid #1b5366;border-radius:12px;padding:16px 18px;margin:0 0 16px}}
.card.hot{{border-left-color:#ffb547;box-shadow:0 0 18px rgba(255,181,71,.12)}} .top{{display:flex;gap:12px;align-items:center;font-size:12px;color:#6f97a6}}
.num{{color:#3ad6ff;font-weight:700;letter-spacing:.06em;text-transform:uppercase}} .badge{{margin-left:auto;background:rgba(255,181,71,.15);color:#ffb547;border-radius:999px;padding:2px 10px;font-weight:600}}
h2{{font-size:17px;margin:8px 0 6px;color:#fff}} p{{margin:0 0 10px;color:#cfeaf3}} a{{color:#3ad6ff;text-decoration:none;font-size:13px}}</style></head>
<body><main><h1>Emails from {html.escape(sender)}</h1><div class="sub">Last {days} days · {len(msgs)} email(s) · summarised by AURA</div>
{''.join(cards)}</main></body></html>""")
        import face_window
        return f"http://localhost:{face_window.PORT}/mail/{name}.html"
    except Exception as e:
        print(f"(mail: couldn't write digest page: {e})")
        return None


def check():
    """'Check my email' - what's waiting + today's count."""
    svc = _service()
    s = _st()
    pending = list(s["pending"].values())
    unread = svc.users().messages().list(userId="me", q="in:inbox is:unread newer_than:1d", maxResults=50).execute()
    n_unread = len(unread.get("messages", []) or [])
    if not pending:
        return f"No important emails waiting. You have {n_unread} unread email{'s' if n_unread != 1 else ''} from today, none that need you."
    lines = "; ".join(f"from {p['from_name']}: {p['summary']}" for p in pending[:6])
    return f"{len(pending)} important email{'s' if len(pending) != 1 else ''} waiting - {lines}"


_FILLER = {"the", "one", "ones", "email", "emails", "mail", "from", "about", "that", "this", "and", "its"}


def _pick(which=""):
    """The pending email the user means: by sender/subject words, or the most recent."""
    pend = sorted(_st()["pending"].values(), key=lambda p: p.get("announced", 0))
    which = (which or "").lower().strip()
    if which and which not in ("latest", "last", "it", "that", "this"):
        hits = [p for p in pend if which in p["from_name"].lower() or which in p["from_addr"] or which in p["subject"].lower()]
        if not hits:   # "the Rayyan one", "neurology" - match on any meaningful word
            words = [w for w in re.findall(r"[a-z0-9]+", which) if len(w) > 2 and w not in _FILLER]
            hits = [p for p in pend if any(w in f"{p['from_name']} {p['from_addr']} {p['subject']}".lower() for w in words)]
        return hits[-1] if hits else None
    return pend[-1] if pend else None


def _changed():
    """The pending list changed - update Mini AURA's "N important emails" chip right away."""
    _save_state()
    try:
        import mini_window
        mini_window.refresh_info()
    except Exception:
        pass


def pending_list():
    """For Mini AURA's email list (oldest first)."""
    return [{"id": p["id"], "from": p["from_name"], "subject": p["subject"], "summary": p.get("summary", "")}
            for p in sorted(_st()["pending"].values(), key=lambda p: p.get("announced", 0))]


def dismiss(ids):
    """Stops reminding about these emails (Mini AURA's x buttons). Returns how many are left."""
    s = _st()
    for i in ids:
        s["pending"].pop(i, None)
        if i in s["held"]:
            s["held"].remove(i)
    _changed()
    return len(s["pending"])


def acknowledge(which=""):
    s = _st()
    if (which or "").lower().strip() in ("all", "everything", "all of them", "them", "those", "these"):
        n = len(s["pending"])
        s["pending"].clear()
        s["held"].clear()
        _changed()
        return f"Noted - I'll stop reminding you about {'those ' + str(n) + ' emails' if n != 1 else 'that email'}." if n else "Nothing pending."
    # several at once: "Rayyan, Elsevier and the neurology one"
    parts = [w for w in re.split(r"\s*(?:,|;|&|\band\b)\s*", which or "") if w.strip()] or [""]
    picked = []
    for part in parts:
        p = _pick(part)
        if p and p not in picked:
            picked.append(p)
    if not picked:
        names = ", ".join(p["from"] for p in pending_list())
        return (f"I couldn't tell which one you meant. Still waiting: {names}." if names
                else "There's no email I'm reminding you about right now.")
    for p in picked:
        s["pending"].pop(p["id"], None)
    _changed()
    rest = len(s["pending"])
    who = " and ".join(p["from_name"] for p in picked)
    return f"Done - dismissed the email{'s' if len(picked) > 1 else ''} from {who}." + (
        f" {rest} still waiting." if rest else " Nothing else waiting.")


def _find_one(svc, who):
    p = _pick(who) if who else _pick()
    if p and (not who or who.lower() in (p["from_name"] + p["from_addr"] + p["subject"]).lower()):
        msg = svc.users().messages().get(userId="me", id=p["id"], format="full").execute()
        return _info(msg, with_body=True)
    if who:
        found = _search(svc, _sender_query(who, 30), n=1)
        if not found:
            found = _search(svc, f"{who} newer_than:30d", n=1)
        return found[-1] if found else None
    return None


def read_email(who=""):
    from autocoder import _paced
    svc = _service()
    m = _find_one(svc, who)
    if not m:
        return f"I couldn't find an email{' from ' + who if who else ''} to read."
    text = _paced(f"""Tell a busy student what this email says, out loud, in 3-6 plain sentences: who it's from, what it's
about, what they need to do and by when, and any important details (amounts, dates, places, links described
in words). No markdown.
From: {m['from_name']} <{m['from_addr']}>  Date: {_nice_date(m)}
Subject: {m['subject']}
{m.get('body', m['snippet'])[:3500]}""", temperature=0.2, expect=400)
    return text.strip()


def draft_reply(who, message):
    """Writes a reply and saves it as a Gmail DRAFT (never sends)."""
    from autocoder import _paced
    svc = _service()
    m = _find_one(svc, who)
    if not m:
        return f"I couldn't find the email{' from ' + who if who else ''} to reply to."
    try:
        me = svc.users().getProfile(userId="me").execute().get("emailAddress", "")
    except Exception:
        me = ""
    body = _paced(f"""Write the body of a reply email for {config.describe_user()} to the email below.
What they want to say: "{message}"
Match the tone of the original (formal for organisations/professors, friendly for friends). Keep it short and
natural{(", sign off as " + config.USER_NAME) if config.USER_NAME else ""}. Output ONLY the email body text.
Original from {m['from_name']}: Subject: {m['subject']}
{m.get('body', m['snippet'])[:1800]}""", temperature=0.4, expect=500).strip()
    mime = MIMEText(body)
    mime["To"] = m["reply_to"] or (f"{m['from_name']} <{m['from_addr']}>" if m["from_name"] else m["from_addr"])
    mime["Subject"] = m["subject"] if m["subject"].lower().startswith("re:") else f"Re: {m['subject']}"
    if me:
        mime["From"] = me
    if m["message_id"]:
        mime["In-Reply-To"] = m["message_id"]
        mime["References"] = m["message_id"]
    raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()
    svc.users().drafts().create(userId="me", body={"message": {"raw": raw, "threadId": m["threadId"]}}).execute()
    webbrowser.open("https://mail.google.com/mail/u/0/#drafts")
    return f"I've written a reply to {m['from_name']} and saved it as a draft - I opened your drafts so you can check it and press send."


def _modify(who, add=(), remove=(), done_msg=""):
    svc = _service()
    m = _find_one(svc, who)
    if not m:
        return f"I couldn't find that email{' from ' + who if who else ''}."
    svc.users().messages().modify(userId="me", id=m["id"], body={"addLabelIds": list(add), "removeLabelIds": list(remove)}).execute()
    _st()["pending"].pop(m["id"], None)
    _save_state()
    return done_msg.format(name=m["from_name"])


def snooze(who, when):
    import reminders
    p = _pick(who)
    if not p:
        return "There's no email I'm reminding you about right now."
    text, due = reminders.parse(f"remind me {when} to deal with the email from {p['from_name']}: {p['subject']}")
    if not due:
        return "When should I remind you about it? Say something like 'tomorrow at 9'."
    reminders.add(f"Email from {p['from_name']}: {p['subject']}", due)
    _st()["pending"].pop(p["id"], None)
    _save_state()
    return f"Okay - I'll stop nagging and remind you about the email from {p['from_name']} {reminders._describe_due(due)}."


def set_sender_rule(sender, always):
    r = rules()
    key, other = ("always_announce_from", "never_announce_from") if always else ("never_announce_from", "always_announce_from")
    s = sender.strip().lower()
    if s and s not in r[key]:
        r[key].append(s)
    r[other] = [x for x in r[other] if x != s]
    _save_json(RULES_FILE, r)
    return (f"Got it - I'll always tell you about emails from {sender}." if always
            else f"Okay - I won't announce emails from {sender} any more (unless you ask about them).")


OP_PROMPT = """The user said something about their email: "{text}"
Emails AURA is currently reminding them about (most recent last):
{pending}

Work out what they want. Reply ONLY with JSON:
{{"op": "connect|check|ack|from_sender|read|reply|archive|mark_read|snooze|always|never",
  "who": "sender name/address/subject words they referred to, or '' (= the most recent one), or 'all' for ack-all",
  "days": number or null, "message": "for reply: what they want to say", "when": "for snooze: when to remind"}}
Meanings: connect = connect/set up Gmail. check = any important/new emails? ack = 'noted', 'got it', 'I've seen it',
'I've read them', 'dismiss/clear/remove/ignore X', 'X isn't important' - for ack, "who" = 'all' when they mean every one
(e.g. 'noted all of them', 'clear them'), or the senders they named separated by commas (e.g. 'Rayyan, Elsevier').
from_sender = tell/summarise ALL emails from someone (default 10 days). read = read/what does the email say.
reply = reply/respond saying X (saved as a draft). archive = archive/remove from inbox. mark_read = mark as read.
snooze = remind me about it later. always = always tell me about emails from X. never = stop announcing emails from X."""


SLOW_OPS = {"from_sender", "read", "reply", "connect"}


def handle_async(text, speak):
    """AURA's 'email' action: quick things answer straight away; slow ones (summaries,
    reading, drafting, connecting) run in the background and are spoken when ready."""
    result = {}

    def work():
        result["text"] = handle(text, _op_out=result)
        if result.get("background"):
            speak(result["text"])

    try:
        import phone_bridge
        work = phone_bridge.carry(work)      # asked on the phone -> the reply / digest go to the phone
    except Exception:
        pass
    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(6)   # most quick ops finish well within this
    if t.is_alive() or result.get("op") in SLOW_OPS:
        result["background"] = True
        if "text" in result:   # finished during the wait anyway
            return result["text"]
        op = result.get("op")
        return {"from_sender": "Give me a moment - I'm going through those emails.",
                "read": "One sec - opening that email.", "reply": "Writing the reply now.",
                "connect": "Opening Google's sign-in page - approve AURA there."}.get(op, "One moment.")
    return result.get("text", "")


def handle(text, _op_out=None):
    """Entry point for AURA's 'email' action."""
    from autocoder import _paced_json
    pend = "\n".join(f"- {p['from_name']} <{p['from_addr']}>: {p['subject']}"
                     for p in sorted(_st()["pending"].values(), key=lambda p: p.get("announced", 0))) or "(none)"
    if re.match(r"^\s*(connect|link|set ?up|hook up)\b.*\b(g?mail|email|inbox)\b", text, re.I):
        cmd = {"op": "connect"}
    else:
        cmd = _paced_json(OP_PROMPT.format(text=text, pending=pend), expect=200, default={}) or {}
    op, who = cmd.get("op", "check"), (cmd.get("who") or "").strip()
    if _op_out is not None:
        _op_out["op"] = op
    if op == "connect":
        return connect()
    if not connected():
        return "Gmail isn't connected yet - say 'connect my Gmail' and approve it in the browser."
    try:
        if op == "ack":
            return acknowledge(who)
        if op == "from_sender":
            return emails_from(who or text, cmd.get("days") or None)
        if op == "read":
            return read_email(who)
        if op == "reply":
            return draft_reply(who, cmd.get("message") or text)
        if op == "archive":
            return _modify(who, remove=["INBOX"], done_msg="Archived the email from {name}.")
        if op == "mark_read":
            return _modify(who, remove=["UNREAD"], done_msg="Marked the email from {name} as read.")
        if op == "snooze":
            return snooze(who, cmd.get("when") or "in 1 hour")
        if op in ("always", "never"):
            return set_sender_rule(who or text, op == "always") if who else "Which sender do you mean?"
        return check()
    except Exception as e:
        return f"I hit a problem with Gmail: {_friendly_error(e)}"


def describe():
    """For the intent classifier: was an important email just announced? (so 'noted' goes here)"""
    if not _st()["pending"]:
        return ""
    recent = time.time() - _last_announce["at"] < 20 * 60
    who = _last_announce["who"] or _pick()["from_name"]
    return f"AURA {'just announced' if recent else 'is reminding the user about'} an important email (from {who})"
