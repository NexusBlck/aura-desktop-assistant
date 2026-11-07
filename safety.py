# safety.py
# Extra protections for AURA, controlled by SAFE_MODE in config (on by default).
#
#   * Asks you first before AURA writes and runs new code (the app builder) or force-closes an app.
#     (Shutdown / restart always ask, even with SAFE_MODE off.)
#   * Requests that arrive from the PUBLIC INTERNET - the Apple Watch shortcut through Tailscale
#     Funnel - may only use a short allow-list of harmless actions (chat, reminders, tasks, your
#     schedule...). They can't control the PC, run code, read the screen or change settings.
#   * Tells you at start-up which sensitive features are switched on.
import os

import config

# action -> what AURA says when it asks "Just to confirm - you want me to ...?"
CONFIRM_ACTIONS = {
    "build_project": "write new code and run it on this PC to build that",
    "modify_project": "change your last project's code and run it again",
    "close_app": "force-close that app (anything unsaved in it would be lost)",
}

# The only actions a request from the public internet may trigger in SAFE_MODE.
REMOTE_ALLOWED = {
    "chat", "search_web", "get_datetime", "add_task", "complete_task", "set_reminder", "focus_session",
    "full_schedule", "greeting_briefing", "add_calendar_event", "weekly_report", "pubmed_search", "dnd",
}

REMOTE_REFUSAL = ("For safety, I only do simple things like answers, reminders and tasks from the watch - "
                  "not controlling the PC. Ask me on the PC or in the phone app.")


def needs_confirmation(action):
    return bool(config.SAFE_MODE) and action in CONFIRM_ACTIONS


def confirm_label(action):
    return CONFIRM_ACTIONS.get(action, "do that")


def _current_device():
    try:
        import phone_bridge
        return phone_bridge.current_device()
    except Exception:
        return None


def from_public_internet():
    """True while handling a request that came in over Tailscale Funnel (the Apple Watch key)."""
    device = _current_device()
    return bool(device and device.get("watch"))


def remote_refusal(action, recall_question=False):
    """The reply to give instead of acting, or '' when the action may go ahead."""
    if not config.SAFE_MODE or not from_public_internet():
        return ""
    if recall_question or action not in REMOTE_ALLOWED:
        return REMOTE_REFUSAL
    return ""


def active_sensitive_features():
    """Short descriptions of the privacy-sensitive features that are currently switched on."""
    on = []
    if getattr(config, "WAKE_WORD_ENABLED", False):
        on.append("always-on wake word (what the mic hears is sent to speech recognition)")
    try:
        import recall
        if recall.status().get("on"):
            on.append("Total Recall screen memory")
    except Exception:
        pass
    if getattr(config, "PHONE_REMOTE_CONTROL", False):
        on.append("phone remote control (LINK)")
    if os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), "google_gmail_token.json")):
        on.append("Gmail watching")
    if not config.SAFE_MODE:
        on.append("SAFE_MODE is OFF")
    return on


def announce_sensitive_features():
    on = active_sensitive_features()
    if not on:
        return
    text = "Active: " + "; ".join(on) + "."
    print(f"(privacy) {text}")
    try:
        import face_window
        face_window.show_toast("Privacy check", text, "info")
    except Exception:
        pass
