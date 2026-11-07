# main.py
import re
import threading
import time
import queue
import os
import sys

# If launched with a Python that lacks AURA's packages (e.g. VS Code picked another install),
# relaunch with the standard per-user Python 3.13 install if there is one.
try:
    import openai  # noqa: F401
except ModuleNotFoundError:
    _py313 = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Python", "Python313", "python.exe")
    if os.path.exists(_py313) and os.path.normcase(sys.executable) != os.path.normcase(_py313):
        import subprocess
        print("(wrong Python for AURA - relaunching with Python 3.13)")
        sys.exit(subprocess.call([_py313, os.path.abspath(__file__), *sys.argv[1:]]))
    raise

from ai import ask_llm
from intent import classify_intent
from pc_control import (
    open_app,
    close_app,
    open_website,
    save_site,
    open_and_type,
    play_youtube,
    write_note,
    type_text,
    press_hotkey,
    mouse_action,
    search_and_open_files,
    take_screenshot,
    set_volume,
    shutdown_pc,
    restart_pc,
)
from web_search import search_web
from web_briefing import build_briefing
from spotify_control import play_spotify
from roblox_control import play_roblox_game
from pubmed_search import search_pubmed, summarize_pubmed_results, format_verification_result, get_last_results, format_full_list
from paper_library_action import handle_library_request
from pdf_analysis import handle_pdf_request, handle_compare_request
from prisma_diagram import handle_prisma_request
from android_control import handle_phone_request
from journal import run_journal_session
import dashboard_window
from life_data import log_session, get_weekly_summary, get_trend_data, parse_session_time, get_session_type_summary
from code_helper import explain_clipboard_error
from autocoder import build_project, modify_project
from tray_icon import start_tray
from explain_popup import start_hotkey_listener
from calendar_integration import get_todays_events, get_remaining_events_today, create_event
from event_parser import parse_event
from tasks import add_task, complete_task, get_pending_tasks
from memory_store import load_store, save_store, add_entry, add_recent_turn, retrieve_relevant
from forget import forget_detail
from lessons import add_lesson
from voice import speak, listen, speaking_event, interrupt_event
import face_window
import active_window
import datetime
from zoneinfo import ZoneInfo
from config import ASSISTANT_NAME, LOCAL_TIMEZONE
from location import get_current_location
import schedule_window

WAKE_WORD = ASSISTANT_NAME.lower()  # "aura"
WAKE_WORD_ENABLED = bool(getattr(__import__("config"), "WAKE_WORD_ENABLED", False))
# Whole-word match so "Laura", "aurora", "restaurant" etc. don't wake AURA.
WAKE_WORD_RE = re.compile(r"\b" + re.escape(WAKE_WORD) + r"\b", re.IGNORECASE)
if WAKE_WORD == "aura":
    # Whisper sometimes writes the name as "Ora" / "Aurah" / "Awra" - still a wake word.
    # The looser spellings ("ora", "oura") only count after "hey" - on their own they turn up in noise.
    WAKE_WORD_RE = re.compile(r"\b(hey\s+)?(aura|aurah|auraa|awra)\b|\bhey\s+(ora|oura|orah)\b", re.IGNORECASE)

# Which agent AURA used most recently ("web" browser or "pc" desktop), so follow-ups stack onto it.
LAST_AGENT = None

# Started by Windows at login (the Startup-folder launcher passes --background):
# no "AURA is online" announcement and the big HUD starts hidden - Mini AURA is shown.
BACKGROUND_MODE = "--background" in sys.argv

DESTRUCTIVE_ACTIONS = {"shutdown", "restart"}

BASE_PERSONALITY = (
    f"You are {ASSISTANT_NAME}, a desktop AI assistant with a natural, "
    "casual personality - like a sharp, easygoing friend, not a customer "
    "service bot. Talk the way a real person actually talks:\n"
    "- Use contractions (I'm, that's, don't, you're).\n"
    "- Skip stiff openers like 'Hello! How can I help you today?' - just "
    "respond to what they said, the way a friend would.\n"
    "- Don't narrate your own helpfulness ('I'd be happy to help with "
    "that!'). Just help.\n"
    "- Vary your phrasing - don't reuse the same greeting or sign-off "
    "every time.\n"
    "- It's fine to have a light opinion, a bit of humor, or react "
    "naturally ('oh nice', 'hmm good question', 'yeah that tracks') "
    "instead of being neutral and robotic.\n"
    "- Talk with normal, natural length - like a real person actually "
    "talking, not a customer service bot AND not a person giving "
    "one-word grunts. Never reply in just one or two words unless a plain "
    "yes/no genuinely fits.\n"
    "- DEFAULT TO CONCISE: lead with the headline / most important point "
    "first, in 1-2 sentences. Don't unpack every detail unprompted - if "
    "there's more worth knowing, briefly offer it ('want the details?') "
    "instead of dumping it all. Only go deep when the user actually asks "
    "for more, explains they want detail, or the question truly can't be "
    "answered properly any shorter.\n"
    "- For real questions - news, explaining something, how/why - still "
    "keep the FIRST answer to the key point; expand only on follow-up.\n"
    "- If the user interrupts you and asks to summarize, shorten, or give "
    "just the key points, condense your PREVIOUS reply instead of "
    "repeating it.\n"
    "- GROUNDING: never claim to have checked, found, opened, or verified "
    "something on the user's actual computer unless a real action/tool "
    "actually did that. If you don't have real information, say so "
    "instead of guessing as if you checked.\n"
    "- CONFIDENCE SIGNALING: if you're recalling from memory, guessing, or "
    "not fully sure, say so plainly ('I think...', 'not 100% sure, but...') "
    "instead of stating it like a confirmed fact. If something came from "
    "an actual search, file read, or tool result, you can state it "
    "plainly since it's verified.\n"
    "CRITICAL RULE: Always reply in the exact same language and script the "
    "user just wrote in. Never switch languages on your own."
)

store = load_store()


def build_system_message(user_text):
    """Builds the system prompt fresh for each request, injecting only the
    memory entries relevant to THIS query (keyword retrieval) instead of
    dumping the whole memory in every time - keeps requests fast regardless
    of how much AURA has learned over time."""
    content = BASE_PERSONALITY
    relevant = retrieve_relevant(store, user_text)
    if relevant:
        content += "\n\n" + relevant
    return {"role": "system", "content": content}


def build_history_for_call(user_text):
    """Recent raw turns (short-term continuity) + relevant long-term memory
    as the system message - not the full chat log."""
    sys_msg = build_system_message(user_text)
    recent = store["recent_turns"][-10:]
    return [sys_msg] + recent


def remember(user_text, reply, memorable, category):
    """Logs the turn for short-term continuity, and - if flagged memorable -
    stores a structured entry in the right category instead of an
    ever-growing raw transcript. (The Conversation Log in the AURA window
    is fed separately - assistant_loop logs what you said, speak() logs
    everything AURA says.)"""
    add_recent_turn(store, "user", user_text)
    add_recent_turn(store, "assistant", reply)
    if memorable:
        add_entry(store, category, user_text)
    save_store(store)


def _user_context():
    """What AURA knows about the user, so research can work out WHICH thing
    they mean - e.g. "RFC gym" -> the one in their city, since they train
    there - instead of every gym in the world with that name."""
    lines = []
    try:
        lines.append(f"Location: {get_current_location()}")
    except Exception:
        pass
    remembered = (store["facts"] + store["preferences"] + store["projects"])[-25:]
    if remembered:
        lines.append("Remembered about them: " + "; ".join(remembered))
    try:
        from life_data import load_data
        activities = sorted({s["type"] for s in load_data()["sessions"]})
        if activities:
            lines.append("Activities they log/train: " + ", ".join(activities))
    except Exception:
        pass
    recent = [f"{t['role']}: {t['content'][:160]}" for t in store["recent_turns"][-6:]]
    if recent:
        lines.append("Recent conversation:\n" + "\n".join(recent))
    return "\n".join(lines)


def handle_command(intent):
    global LAST_AGENT
    action = intent.get("action")
    target = intent.get("target", "")

    if action == "open_app":
        return open_app(target)
    elif action == "close_app":
        return close_app(target)
    elif action == "open_website":
        return open_website(target)
    elif action == "save_website":
        result = save_site(target, intent.get("url", ""))
        if result.startswith("Got it"):
            open_website(target)  # open the corrected site right away
            result += " Opening it now."
        return result
    elif action == "open_and_type":
        return open_and_type(intent.get("app", ""), target, intent.get("submit", False))
    elif action == "play_youtube":
        return play_youtube(target)
    elif action == "play_spotify":
        return play_spotify(target)
    elif action == "play_roblox":
        return play_roblox_game(target)
    elif action == "write_note":
        return write_note(target)
    elif action == "type_text":
        return type_text(target)
    elif action == "hotkey":
        return press_hotkey(target)
    elif action == "mouse_action":
        return mouse_action(target)
    elif action == "screen_action":
        # Operates the PC through Windows UI Automation (exact controls), screenshots only as a fallback.
        import desktop_agent
        LAST_AGENT = "pc"
        return desktop_agent.do_task(target)
    elif action == "pubmed_search":
        lowered_target = target.lower()
        if any(p in lowered_target for p in ["full list", "all of them", "show me all", "everything you found", "list them all"]):
            papers, _ = get_last_results()
            return format_full_list(papers)
        mode = intent.get("search_mode", "broad")
        papers = search_pubmed(target, max_results=(20 if mode == "broad" else 3))
        if mode == "verify":
            return format_verification_result(papers, target)
        return summarize_pubmed_results(papers, target)
    elif action == "paper_library":
        return handle_library_request(target)
    elif action == "pdf_analysis":
        return handle_pdf_request(target)
    elif action == "compare_papers":
        return handle_compare_request(target)
    elif action == "generate_prisma":
        return handle_prisma_request(target)
    elif action == "phone_action":
        return handle_phone_request(target)
    elif action == "explain_error":
        return explain_clipboard_error()
    elif action == "build_project":
        # Runs in the background - AURA announces when it's done.
        return build_project(target)
    elif action == "modify_project":
        return modify_project(target)
    # Runs in AURA's one shared browser window, so commands stack on each other.
    elif action == "web_task":
        # Uses the browser step by step (menus, dropdowns, tables) and speaks the finished answer.
        import web_agent
        LAST_AGENT = "web"
        return web_agent.do_task(target)
    elif action == "dnd":
        import presence
        off = re.search(r"\b(off|disable|stop|end|turn\s+off|deactivate|no\s+more)\b", target.lower()) and \
            not re.search(r"\bstop\s+announcing\b", target.lower())
        presence.set_dnd(not off)
        try:
            import mini_window
            mini_window.refresh_info()
        except Exception:
            pass
        return ("Do Not Disturb is off - I'll speak up again." if off else
                "Do Not Disturb is on. I won't announce anything - emails and reminders will just pop up quietly. "
                "Say 'do not disturb off' when you're ready.")
    elif action == "email":
        # Gmail: check / summarise a sender / read / draft a reply / archive / acknowledge...
        import gmail_watch
        return gmail_watch.handle_async(target, speak)
    elif action == "cdc_wonder":
        import cdc_window
        return cdc_window.open_extractor(target)
    elif action == "open_camera":
        import vision_window
        return vision_window.show_vision()
    elif action == "close_camera":
        import vision_window
        return vision_window.hide_vision()
    elif action == "set_reminder":
        import reminders
        return reminders.add_from_text(target)
    elif action == "focus_session":
        import focus
        return focus.handle_request(target)
    elif action == "web_briefing":
        speak("On it - researching that across the web. This takes up to a minute.")
        return build_briefing(target, context=_user_context())
    elif action == "search_files":
        mode = "specific" if intent.get("search_mode") == "specific" else "topic"
        return search_and_open_files(target, mode=mode)
    elif action == "screenshot":
        return take_screenshot()
    elif action == "volume":
        return set_volume(target)
    elif action == "shutdown":
        return shutdown_pc()
    elif action == "restart":
        return restart_pc()
    return None


_last_creation = {"project": None, "t": 0}
_EDIT_WORDS = re.compile(r"\b(it|that|this|the (chart|picture|image|video|graph|diagram|slides|document|logo))\b.*|\b(make it|change|edit|add|remove|"
                         r"replace|turn it|more|less|bigger|smaller|brighter|darker|instead)\b", re.I)


def handle_generate(user_text):
    """Starts a Studio creation. Edits ("make it blue") go to the creation made in the last 30 minutes."""
    import studio
    import phone_bridge
    pid = _last_creation["project"] if time.time() - _last_creation["t"] < 1800 and _EDIT_WORDS.search(user_text) else None
    device = getattr(phone_bridge._local, "device", None) if phone_bridge.phone_origin() else None
    try:
        jid = studio.start(user_text, pid, [], device)
    except ValueError as e:
        return str(e)

    def follow():
        r = studio.wait(jid, 1200)
        if r.get("state") == "done":
            _last_creation.update(project=r["project"], t=time.time())
            if not device:                          # asked on the PC: show it on the PC
                try:
                    os.startfile(r["file"])
                except Exception:
                    pass
                speak(r.get("message") or "Done.")
        elif not device:
            speak("Studio couldn't make that: " + str(r.get("error") or "something went wrong"))
    threading.Thread(target=phone_bridge.carry(follow), daemon=True).start()
    if device:
        phone_bridge.emit("do", action="tool", tool="studio", arg={"job": jid, "project": pid}, device=device)
        return "On it - it's being made in Studio, you'll see it appear."
    return "On it - I'll open it when it's ready."


def handle_search(user_text, target, history_for_call):
    # Weather questions without a specified place default to the user's own location.
    if "weather" in target.lower() and " in " not in target.lower():
        target = f"{target} in {get_current_location()}"

    results = search_web(target)
    prompt = f"""The user asked: "{user_text}"

Here are fresh web search results:
{results}

Give a concise answer that leads with the key point/headline first - the
most important fact or outcome, in 1-2 sentences. Don't unpack every detail
from the search results unprompted; if there's meaningfully more worth
knowing, briefly offer to go deeper rather than dumping it all. Answer
naturally, in the same language the user used, without saying "search
results" explicitly - just explain it as if you already knew it."""
    call_messages = history_for_call + [{"role": "user", "content": prompt}]
    reply = ask_llm(call_messages)
    return reply


def _clean_weather_summary(raw_weather_info):
    """Raw search results can be messy (even raw JSON sometimes). This
    extracts just a short, clean line for the GUI display - the spoken
    reply still uses the full raw info, since the AI handles that fine."""
    prompt = (
        "From this raw weather search data, extract ONLY a short, clean "
        "summary in the format 'TEMP, CONDITION' (e.g. '84°F, Clear skies'). "
        "Output ONLY that short string - no JSON, no extra words, no quotes.\n\n"
        f"{raw_weather_info}"
    )
    try:
        result = ask_llm([{"role": "user", "content": prompt}], temperature=0)
        return result.strip().strip('"')
    except Exception:
        return "Unavailable"


def _time_label():
    """Real current hour (grounded), not guessed."""
    hour = datetime.datetime.now(ZoneInfo(LOCAL_TIMEZONE)).hour
    if hour < 12:
        return "morning"
    elif hour < 17:
        return "afternoon"
    return "evening"


def _get_user_name():
    import re
    for fact in store["facts"]:
        match = re.search(r"name is (\w+)", fact, re.IGNORECASE)
        if match:
            return match.group(1)
    return ""


def _full_briefing_reply(user_text, history_for_call, time_label):
    """Schedule + tasks + weather - the complete rundown."""
    events = get_todays_events()
    if events is None:
        calendar_note = "Google Calendar isn't connected yet - the user needs to finish that setup."
        schedule_lines = []
    elif isinstance(events, str) and events.startswith("__ERROR__"):
        calendar_note = f"Couldn't reach the calendar ({events.replace('__ERROR__: ', '')})."
        schedule_lines = []
    elif not events:
        calendar_note = "Nothing on the calendar today."
        schedule_lines = []
    else:
        calendar_note = "Today's events: " + "; ".join(events)
        schedule_lines = events

    pending = get_pending_tasks()
    tasks_note = "; ".join(t["text"] for t in pending[:6]) if pending else "No pending tasks."
    task_lines = [t["text"] for t in pending[:6]]

    location = get_current_location()
    weather_info = search_web(f"weather today in {location}")

    prompt = f"""Give the user a warm, natural {time_label} briefing using this REAL info -
don't invent anything not listed here:

Calendar: {calendar_note}
Pending tasks/ideas: {tasks_note}
Weather: {weather_info}

Speak it like a real assistant greeting them - mention their schedule (if
any), pending tasks (if any), and the weather, then ask if there's anything
they'd like to do first. Natural and conversational, not a bullet list. If
the calendar isn't connected yet, mention that plainly and offer to help set
it up, don't pretend to have a schedule."""

    call_messages = history_for_call + [{"role": "user", "content": prompt}]
    reply = ask_llm(call_messages)

    schedule_window.update_schedule(_get_user_name(), schedule_lines, _clean_weather_summary(weather_info), task_lines, time_label)
    return reply


def _remaining_events_reply(user_text, history_for_call, time_label):
    """Just what's left to do today - no weather, no full rundown."""
    events = get_remaining_events_today()
    if events is None:
        calendar_note = "Google Calendar isn't connected yet."
        schedule_lines = []
    elif isinstance(events, str) and events.startswith("__ERROR__"):
        calendar_note = f"Couldn't reach the calendar ({events.replace('__ERROR__: ', '')})."
        schedule_lines = []
    elif not events:
        calendar_note = "Nothing left on the calendar for today."
        schedule_lines = []
    else:
        calendar_note = "Still left today: " + "; ".join(events)
        schedule_lines = events

    prompt = f"""Give the user a brief, natural {time_label} check-in using ONLY this
REAL info about what's LEFT to do today. Don't mention weather, don't give a
full rundown of the whole day, just what's still remaining:

{calendar_note}

Keep it short and natural, like a quick check-in."""

    call_messages = history_for_call + [{"role": "user", "content": prompt}]
    reply = ask_llm(call_messages)

    schedule_window.update_schedule(_get_user_name(), schedule_lines, "", [], time_label)
    return reply


def handle_greeting_briefing(user_text, history_for_call):
    """Triggered ONLY by an actual greeting. Behavior depends on the REAL
    current time: before noon -> full briefing, after noon -> just what's
    left to do today."""
    time_label = _time_label()
    hour = datetime.datetime.now(ZoneInfo(LOCAL_TIMEZONE)).hour
    if hour < 12:
        return _full_briefing_reply(user_text, history_for_call, time_label)
    return _remaining_events_reply(user_text, history_for_call, time_label)


def handle_full_schedule(user_text, history_for_call):
    """Triggered by an EXPLICIT schedule request - always gives everything,
    regardless of time of day."""
    return _full_briefing_reply(user_text, history_for_call, _time_label())


def confirm_destructive_action(action_label):
    """High-stakes actions (shutdown/restart) get a spoken confirmation
    before executing - everything else stays instant."""
    speak(f"Just to confirm - you want me to {action_label}? Say yes to continue.")
    reply = listen(timeout=8, phrase_time_limit=5)
    if reply and any(w in reply.lower() for w in ["yes", "yeah", "confirm", "do it", "go ahead"]):
        return True
    speak("Okay, cancelled.")
    return False


MEMORY_CATEGORIES = ("facts", "projects", "preferences")
MEMORY_BACKUP_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aura_memory_store.before_wipe.json")

# Targets that mean "all of it" rather than one specific detail - a safety
# net in case the classifier picks forget_memory for a full wipe request.
_FORGET_ALL_TARGET = re.compile(r"^\s*(every|any)\s*thing\b|^\s*all\b|\beverything you know\b|\ball (of )?(my|your) (data|memory)|\bknow about me\s*$", re.IGNORECASE)


def wipe_all_memory():
    """Clears everything AURA remembers about the user. Saves a copy first
    (aura_memory_store.before_wipe.json) so a wipe by mistake can be undone
    by copying that file back over aura_memory_store.json."""
    import json
    had_anything = any(store[c] for c in MEMORY_CATEGORIES)
    if had_anything:
        try:
            with open(MEMORY_BACKUP_FILE, "w", encoding="utf-8") as f:
                json.dump(store, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"(couldn't save memory backup: {e})")
    for key in MEMORY_CATEGORIES + ("recent_turns",):
        store[key].clear()
    save_store(store)
    if had_anything:
        speak("Done - I've wiped everything I knew about you. Fresh start.")
    else:
        speak("There wasn't anything saved about you to begin with, but I've cleared our recent chat too. Fresh start.")


def process_command(user_text):
    """Runs one command through AURA's brain and speaks the result."""
    if user_text.lower().strip(" .!") in ["forget everything", "clear memory", "reset memory", "forget all"]:
        wipe_all_memory()
        return

    # "run study protocol" - a saved chain of commands (AURA Mobile's PROTOCOLS, nexus.py)
    import nexus
    protocol = nexus.match_protocol(user_text)
    if protocol:
        import phone_bridge
        res = nexus.protocol_run(protocol["name"], protocol["steps"],
                                 getattr(user_text, "device", None) if isinstance(user_text, phone_bridge.PhoneText) else None)
        speak(res.get("error") or f"{protocol['name']} protocol engaged - {len(protocol['steps'])} steps.")
        return

    history_for_call = build_history_for_call(user_text)
    known_facts = (store["facts"] + store["preferences"])[-30:]
    import web_agent
    import desktop_agent
    # What's open / what AURA was just doing - the agent used most recently goes first, so a
    # follow-up like "now save it" or "and my results?" stacks onto the right one.
    agents = [web_agent.describe(), desktop_agent.describe()]
    if LAST_AGENT == "pc":
        agents.reverse()
    import gmail_watch
    open_now = "; ".join(d for d in (gmail_watch.describe(), *agents, active_window.describe()) if d)
    intent = classify_intent(user_text, history_for_call, known_facts=known_facts,
                             open_target=open_now or None)
    action = intent.get("action", "chat")
    memorable = intent.get("memorable", False)
    category = intent.get("memory_category", "facts")

    # You corrected AURA: keep the lesson, so the same mistake isn't made again (lessons.py)
    if intent.get("correction"):
        from lessons import add_correction
        add_correction(intent["correction"], user_text)

    # Asked on AURA Mobile (without "on my PC"): do it on the phone, or say why it needs the PC.
    import phone_bridge
    if phone_bridge.phone_target():
        import phone_actions
        reply = phone_actions.handle(intent, user_text)
        if reply is not None:
            remember(user_text, reply, False, category)
            speak(reply)
            return

    # SAFE_MODE: a request from the public internet (watch via Funnel) may not control the PC (safety.py)
    import safety
    import overdrive
    refusal = safety.remote_refusal(action, overdrive.looks_like_recall_question(user_text))
    if refusal:
        remember(user_text, refusal, False, category)
        speak(refusal)
        return

    # AURA OVERDRIVE: AEGIS guard, TOTAL RECALL, AIR CONTROL (overdrive.py)
    if action == "overdrive" or overdrive.looks_like_recall_question(user_text):
        reply = overdrive.handle(user_text)
        remember(user_text, reply, False, category)
        speak(reply)
        return

    # AURA SPATIAL: bring up anything in 3D, or steer the open 3D view (spatial.py / spatial_window.py).
    import spatial_window
    if action == "spatial" or spatial_window.wants(user_text, phone_bridge.phone_origin()):
        reply = spatial_window.handle(user_text)
        remember(user_text, reply, False, category)
        speak(reply)
        return

    # AURA STUDIO: make anything / change the last creation with words (studio.py). Phone: Studio opens with progress.
    if action == "generate":
        import studio
        reply = handle_generate(user_text)
        remember(user_text, reply, False, category)
        speak(reply)
        return

    # A HARD question: plan -> your own material / the web / exact maths -> the strongest free model thinking hard ->
    # a second model checks it (smart.py). AURA says it's thinking while that runs.
    if action == "chat" and intent.get("difficulty") == "hard":
        import smart
        result = {}
        worker = threading.Thread(target=phone_bridge.carry(lambda: result.update(zip(("reply", "info"), smart.answer(
            user_text, history_for_call, known_facts, draft=intent.get("reply", ""))))), daemon=True)
        worker.start()
        worker.join(1.5)
        if worker.is_alive():
            speak("Let me think that through properly.")
        worker.join(120)
        reply = (result.get("reply") or intent.get("reply") or "").strip()
        if reply:
            remember(user_text, reply, memorable, category)
            speak(reply)
            return

    if action == "chat" and intent.get("reply"):
        reply = intent["reply"]
        remember(user_text, reply, memorable, category)
        speak(reply)
        return

    if action == "search_web":
        reply = handle_search(user_text, intent.get("target", user_text), history_for_call)
        remember(user_text, reply, False, category)
        speak(reply)
        return

    if action == "forget_all_memory":
        wipe_all_memory()
        return

    if action == "forget_memory":
        # Routed by the classifier (not a "forget " prefix check), so casual
        # phrases like "forget about it" no longer wipe memory entries.
        detail = intent.get("target", "").strip()
        if not detail:
            speak("What should I forget?")
            return
        if _FORGET_ALL_TARGET.search(detail):
            wipe_all_memory()
            return
        before = sum(len(store[c]) for c in MEMORY_CATEGORIES)
        if before == 0:
            speak("I don't actually have anything saved about you yet, so there's nothing to forget.")
            return
        forget_detail(store, detail)
        save_store(store)
        removed = before - sum(len(store[c]) for c in MEMORY_CATEGORIES)
        if removed:
            speak(f"Done - I've forgotten {detail}, but kept everything else.")
        else:
            speak(f"I couldn't find anything I'd saved about {detail}.")
        return

    if action == "get_location":
        loc = get_current_location()
        reply = f"You're currently in {loc}, based on your PC's IP address."
        remember(user_text, reply, False, category)
        speak(reply)
        return

    if action == "get_datetime":
        now = datetime.datetime.now(ZoneInfo(LOCAL_TIMEZONE))
        reply = f"It's {now.strftime('%I:%M %p').lstrip('0')} on {now.strftime('%A, %B %d')}."
        remember(user_text, reply, False, category)
        speak(reply)
        return

    if action == "log_session":
        session_type = intent.get("target", "") or "session"
        speak(f"Logging a {session_type} session. When did this happen - just say 'now' or give a time?")
        when_raw = listen(timeout=8, phrase_time_limit=10)
        timestamp = parse_session_time(when_raw) if when_raw else None

        speak("How much did you enjoy it, 1 to 10?")
        rating_raw = listen(timeout=8, phrase_time_limit=6)
        rating = None
        if rating_raw:
            import re
            match = re.search(r"\d+", rating_raw)
            if match:
                rating = int(match.group())

        log_session(session_type, timestamp=timestamp, rating=rating)

        summary = get_session_type_summary(session_type)
        reply = f"Logged. You've now logged {summary['count']} {session_type} session(s)"
        reply += f", averaging {summary['avg_rating']} out of 10." if summary['avg_rating'] is not None else "."
        remember(user_text, reply, False, category)
        speak(reply)
        return

    if action == "start_journal":
        run_journal_session()
        dashboard_window.show_dashboard()
        remember(user_text, "(completed a journal check-in)", False, category)
        return

    if action == "open_dashboard":
        dashboard_window.show_dashboard()
        summary = get_weekly_summary()
        trend = get_trend_data()

        prompt = f"""Analyze this REAL tracked data and give the user genuine insights and
conclusions - actual patterns, not just a recap of numbers. Only mention
patterns that are actually supported by this data; don't invent trends that
aren't there, and if there isn't enough data yet for a real conclusion, say
that plainly instead of forcing an insight.

This week's summary: {summary}

Last 8 weeks (week labels: {trend['week_labels']}):
- Tasks completed per week: {trend['tasks_per_week']}
- Papers added per week: {trend['papers_per_week']}
- Mood per week: {trend['mood_per_week']}
- Session type totals: {trend['session_type_totals']}

Lead with the most notable pattern or conclusion, mention 1-2 more if
relevant, keep it natural and conversational."""

        call_messages = history_for_call + [{"role": "user", "content": prompt}]
        reply = ask_llm(call_messages)
        remember(user_text, reply, False, category)
        speak(reply)
        return

    if action == "weekly_report":
        summary = get_weekly_summary()
        prompt = f"""Give the user a natural, warm weekly recap using this REAL data -
don't invent anything not listed here:

Sessions logged: {summary['total_sessions']} ({summary['sessions_by_type']})
Tasks completed: {summary['tasks_completed']}
Papers added to their library: {summary['papers_added']}
Journal entries: {summary['journal_entries']}
Average mood: {summary['avg_mood']}

Lead with the overall headline of how the week went, keep it warm and
concise - don't just list numbers robotically."""
        call_messages = history_for_call + [{"role": "user", "content": prompt}]
        reply = ask_llm(call_messages)
        remember(user_text, reply, False, category)
        speak(reply)
        dashboard_window.show_dashboard()
        return

    if action == "greeting_briefing":
        reply = handle_greeting_briefing(user_text, history_for_call)
        remember(user_text, reply, False, category)
        speak(reply)
        return

    if action == "full_schedule":
        reply = handle_full_schedule(user_text, history_for_call)
        remember(user_text, reply, False, category)
        speak(reply)
        return

    if action == "add_calendar_event":
        parsed = parse_event(intent.get("target", user_text))
        if not parsed:
            reply = "I couldn't figure out the date or time for that - can you say it a bit more clearly?"
        else:
            success, error = create_event(parsed["summary"], parsed["start"], parsed["end"], parsed["all_day"])
            if success:
                if parsed["all_day"]:
                    when = parsed["start"].strftime("%A, %B %d")
                else:
                    when = parsed["start"].strftime("%A, %B %d at %I:%M %p").replace(" 0", " ")
                reply = f"Added '{parsed['summary']}' to your calendar for {when}."
            else:
                reply = f"Couldn't add that to your calendar: {error}"
        remember(user_text, reply, False, category)
        speak(reply)
        return

    if action == "add_task":
        task_text = intent.get("target", "").strip()
        if task_text:
            add_task(task_text)
            reply = f"Got it, added to your list: {task_text}."
        else:
            reply = "What should I add to your list?"
        remember(user_text, reply, False, category)
        speak(reply)
        return

    if action == "complete_task":
        fragment = intent.get("target", "").strip()
        done_text = complete_task(fragment) if fragment else None
        reply = f"Marked '{done_text}' as done." if done_text else "I couldn't find a matching task."
        remember(user_text, reply, False, category)
        speak(reply)
        return

    if action in DESTRUCTIVE_ACTIONS:
        label = "shut down your PC" if action == "shutdown" else "restart your PC"
        if not confirm_destructive_action(label):
            return
    if safety.needs_confirmation(action):
        if not confirm_destructive_action(safety.confirm_label(action)):
            return

    if action != "chat":
        result = handle_command(intent)
        if result:
            if any(marker in result.lower() for marker in ["couldn't", "failed", "didn't find", "i couldn't find"]):
                add_lesson(action, result)
            remember(user_text, result, memorable, category)
            speak(result)
            return

    history_for_call.append({"role": "user", "content": user_text})
    reply = ask_llm(history_for_call)
    remember(user_text, reply, memorable, category)
    speak(reply)


def voice_worker(q):
    """Runs in the background: listens for the wake word, then a command,
    and puts the recognized text on the shared queue. Also listens for
    'stop'/'wait' while AURA is talking, to interrupt it."""
    STOP_WORDS = ["stop", "wait", "shut up", "quiet", "pause"]

    while True:
        if speaking_event.is_set():
            # While AURA talks, only listen for "stop" - with the free recogniser, so AURA's
            # own voice doesn't use up Whisper's daily allowance.
            heard = listen(timeout=5, phrase_time_limit=4, quick=True)
            if heard and any(w in heard.lower() for w in STOP_WORDS):
                interrupt_event.set()
            continue

        if push_to_talk_active.is_set():     # push-to-talk has the mic right now
            time.sleep(0.1)
            continue
        if not WAKE_WORD_ENABLED:            # opt-in (config): only push-to-talk / typing start a command
            time.sleep(0.2)
            continue
        heard = listen(timeout=2, phrase_time_limit=15)   # short timeout so push-to-talk never waits long
        if not heard:
            continue

        if not WAKE_WORD_RE.search(heard):
            continue
        print(f"(wake word heard in: {heard!r})")

        remainder = WAKE_WORD_RE.sub("", heard, count=1).strip(" ,.!?")
        if remainder and len(remainder.split()) >= 1 and not re.fullmatch(r"(hey|hi|hello|ok|okay)", remainder, re.I):
            q.put(remainder)
        else:
            listen_for_command(q, quiet_if_nothing=True)


push_to_talk_active = threading.Event()
_COMMAND_QUEUE = None


def push_to_talk():
    """Ctrl+Alt+A / Mini AURA's mic button: listen for one command, no wake word."""
    if speaking_event.is_set():
        interrupt_event.set()
    if push_to_talk_active.is_set() or _COMMAND_QUEUE is None:
        return
    push_to_talk_active.set()
    threading.Thread(target=listen_for_command, args=(_COMMAND_QUEUE, ""), daemon=True).start()


def listen_for_command(q, prompt="Yes?", quiet_if_nothing=False):
    """Asks, then listens for one command (used after a bare "AURA" and by push-to-talk)."""
    if prompt:
        speak(prompt)
    else:
        try:
            import winsound
            winsound.Beep(880, 120)       # push-to-talk: a short beep = "I'm listening"
        except Exception:
            pass
    face_window.set_listening(True)
    try:
        user_text = listen(timeout=7, phrase_time_limit=15)
    finally:
        face_window.set_listening(False)
        push_to_talk_active.clear()
    if user_text:
        q.put(user_text)
    elif not quiet_if_nothing:    # after a bare "AURA" that was probably noise, just go back to sleep
        speak("Sorry, I didn't catch that.")


def text_worker(q):
    """Runs in the background: reads typed input and puts it on the shared
    queue. If AURA is speaking when you type, it interrupts it."""
    while True:
        try:
            typed = input()
        except EOFError:
            continue
        if typed.strip():
            if speaking_event.is_set():
                interrupt_event.set()
            q.put(typed.strip())


def assistant_loop(q):
    if BACKGROUND_MODE:
        print(f"{ASSISTANT_NAME} started in the background")   # started with Windows - no need to announce it
    else:
        speak(f"{ASSISTANT_NAME} is online. " + ("Say my name, press " if WAKE_WORD_ENABLED else "Press ")
              + f"{getattr(__import__('config'), 'PUSH_TO_TALK_HOTKEY', 'ctrl+alt+a')} to talk, or type in the AURA window.")
    threading.Thread(target=voice_worker, args=(q,), daemon=True).start()
    if sys.stdin and sys.stdin.isatty():   # typing in a terminal (not when AURA runs in the background)
        threading.Thread(target=text_worker, args=(q,), daemon=True).start()

    while True:
        user_text = q.get()
        # Your message shows in the AURA window's Conversation Log (typed in
        # the window, typed in the terminal, or spoken - all end up here).
        face_window.add_log("you", user_text)

        import phone_bridge
        if user_text.lower().strip(" .!") in ["exit", "quit", "bye"]:
            if isinstance(user_text, phone_bridge.PhoneText):
                # "bye" on the phone shouldn't switch AURA off at home (the phone couldn't start it again)
                phone_bridge.begin_command(user_text)
                speak("Bye! I'll be here on your PC.")
                phone_bridge.end_command()
                continue
            speak("Goodbye!")
            os._exit(0)

        # One failing command (network down, API error, bad model reply)
        # must not kill the assistant loop - otherwise AURA goes deaf until
        # restart. Real errors still print to the terminal for debugging.
        face_window.set_thinking(True)
        user_text = phone_bridge.begin_command(user_text)   # phone: answer + act on the phone unless "on my PC"
        try:
            process_command(user_text)
        except Exception as e:
            print(f"(error while handling '{user_text}': {e!r})")
            try:
                from openai import AuthenticationError
                speak("My AI key was rejected - check LLM_API_KEY in your .env file, then restart me."
                      if isinstance(e, AuthenticationError) else "Sorry, something went wrong on my end with that one. Try again?")
            except Exception:
                pass
        finally:
            phone_bridge.end_command()
            face_window.set_thinking(False)


_services_started = False


def _start_background_services(command_queue):
    """Starts the assistant loop, hotkey listener, and tray icon. Guarded
    so it only ever runs once, whether login was skipped (cached session)
    or completed just now."""
    global _services_started
    if _services_started:
        return
    _services_started = True
    threading.Thread(target=assistant_loop, args=(command_queue,), daemon=True).start()

    # Live system graphs, timed reminders and the focus timer all run in the
    # background and talk to you through the HUD + voice when something's due.
    import system_monitor
    import reminders
    import focus
    system_monitor.start()

    import presence

    def _reminder_due(reminder):
        face_window.show_toast("Reminder", reminder["text"], "reminder")
        if presence.proactive_ok():
            speak(f"Hey - reminder: {reminder['text']}.")
        else:   # Do Not Disturb or away from the PC: pop-up only, mentioned when you're back
            presence.note_missed(f"a reminder to {reminder['text']}.")
            face_window.add_log("aura", f"Reminder: {reminder['text']}")

    def _focus_event(mode, minutes, label):
        if mode == "focus":
            face_window.show_toast("Focus session complete", f"{minutes} min of {label} logged. 5-minute break started.", "focus")
            speak(f"Nice work - that's {minutes} minutes of focus done. Take a five minute break, I'll tell you when it's over.")
        else:
            face_window.show_toast("Break's over", "Ready for the next session?", "focus")
            speak("Break's over. Ready for another focus session?")

    reminders.start_checker(_reminder_due)
    focus.start_checker(_focus_event)

    # Mini AURA's watcher (dodging, hiding behind fullscreen apps / the HUD), and "welcome back"
    # catch-ups after you've been away.
    import mini_window
    mini_window.start()
    presence.start_watch(speak)
    # AURA OVERDRIVE: AEGIS guard watcher + TOTAL RECALL (captures only while it's switched on)
    import aegis
    import recall
    aegis.start(speak)
    recall.start()
    import safety
    threading.Timer(8, safety.announce_sensitive_features).start()

    # First start of the day in the morning: a short spoken briefing of your day.
    if presence.first_start_today() and 5 <= datetime.datetime.now().hour < 13:
        def _morning():
            if presence.proactive_ok():
                command_queue.put("good morning")
        threading.Timer(25, _morning).start()

    # Gmail: announces important emails as they arrive and keeps reminding until they're dealt with
    # (only once Gmail has been connected - "connect my Gmail").
    import gmail_watch
    gmail_watch.start(speak=speak, toast=face_window.show_toast)

    # AURA Mobile: the phone app's connection to this PC (see mobile/ARCHITECTURE.md).
    import phone_bridge
    import phone_actions
    phone_actions.install()      # what's asked on the phone opens on the phone
    phone_bridge.start(command_queue)

    # Ctrl+Alt+Space from anywhere pops AURA up with the command box ready.
    try:
        import keyboard
        keyboard.add_hotkey("ctrl+alt+space", face_window.summon)
    except Exception as e:
        print(f"(couldn't register the Ctrl+Alt+Space hotkey: {e})")

    # Push-to-talk: press it and just speak - no wake word needed (great in noisy rooms).
    ptt = getattr(__import__("config"), "PUSH_TO_TALK_HOTKEY", "ctrl+alt+a")
    try:
        import keyboard
        keyboard.add_hotkey(ptt, push_to_talk)
    except Exception as e:
        print(f"(couldn't register the {ptt} push-to-talk hotkey: {e})")
    # Bring the list of every document on the PC up to date in the background,
    # so "bring up everything on X" doesn't have to wait for a scan.
    import file_search
    file_search.refresh_in_background()
    import knowledge                      # your study material, searchable for smart answers
    knowledge.refresh_in_background()
    start_hotkey_listener()
    start_tray(
        show_face=face_window.show_face,
        show_dashboard=dashboard_window.show_dashboard,
        show_schedule=schedule_window.show_schedule,
        quit_app=lambda: os._exit(0),
        pair_phone=lambda: __import__("phone_bridge").new_pairing_code(),
    )


def _single_instance():
    """Only one AURA at a time (a second start - e.g. from the Startup folder - just exits)."""
    import ctypes
    ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\AURA_Assistant_SingleInstance")
    return ctypes.windll.kernel32.GetLastError() != 183   # ERROR_ALREADY_EXISTS


def _log_to_file_if_no_console():
    """Running with pythonw (from the Startup folder) there's no console - keep a log file instead."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
    os.makedirs(log_dir, exist_ok=True)
    path = os.path.join(log_dir, "aura.log")
    if os.path.exists(path) and os.path.getsize(path) > 5_000_000:
        os.replace(path, path + ".old")
    stream = open(path, "a", encoding="utf-8", buffering=1)
    stream.write(f"\n===== AURA started {datetime.datetime.now():%Y-%m-%d %H:%M:%S} =====\n")
    sys.stdout = sys.stderr = stream


def main():
    global _COMMAND_QUEUE
    import webview
    import auth
    import login_window
    import mini_window

    _log_to_file_if_no_console()
    import config
    problem = config.missing_setup()
    if problem:
        print(problem)
        if not os.environ.get("AURA_NO_DIALOGS"):
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, problem, "AURA - setup needed", 0x40)
        return
    if not _single_instance():
        print("AURA is already running - not starting a second copy.")
        return

    command_queue = queue.Queue()
    _COMMAND_QUEUE = command_queue
    # Checks the saved session with Firebase (refreshes it, or signs out if
    # the account is no longer valid) instead of trusting any file on disk.
    session = auth.validate_session()
    if not session and login_window.dev_bypass_remembered():
        session = {"name": "Dev", "dev_bypass": True}   # "Skip sign-in (dev)" was used before - don't ask every start
    if not session and not auth.FIREBASE_API_KEY:
        session = {"name": "", "local": True}   # no sign-in service configured: single-user mode

    def _on_login_success(user_info):
        face_window.show_face()
        _start_background_services(command_queue)

    # Both windows must be created before the single webview.start() call.
    # If a valid session already exists, the login window stays hidden and
    # the face window shows immediately; otherwise it's the reverse.
    login_window.create_login_window(on_success=_on_login_success, start_hidden=bool(session))
    # Started with Windows (--background): the big HUD stays hidden; Mini AURA is on screen.
    face_window.launch_face_window(command_queue, start_hidden=not bool(session) or BACKGROUND_MODE)
    mini_window.create_window(command_queue, push_to_talk)

    if session:
        _start_background_services(command_queue)

    webview.start()


def _name_console():
    """Labels AURA's terminal window, so it's obvious which window closes AURA."""
    try:
        import ctypes
        if ctypes.windll.kernel32.GetConsoleWindow():
            ctypes.windll.kernel32.SetConsoleTitleW("AURA - close this window to stop AURA")
    except Exception:
        pass


if __name__ == "__main__":
    _name_console()
    try:
        main()
    except Exception:
        # A real crash: exit code 3 tells aura_launcher.pyw to restart AURA. Closing the window or
        # killing the process gives other codes, so AURA stays stopped when you stop it.
        import traceback
        traceback.print_exc()
        os._exit(3)