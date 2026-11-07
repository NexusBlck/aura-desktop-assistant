# login_window.py
# The sign-up/login screen that gates the rest of AURA. Created hidden if a
# valid local session already exists (so returning users skip straight to
# the app), otherwise shown first - the rest of AURA's windows stay hidden
# until login succeeds.
import os
import threading
import json
import webview
from auth import sign_up as auth_sign_up, log_in as auth_log_in
from config import DEV_LOGIN_BYPASS

_window = None
_ready = threading.Event()
_on_success_callback = None


class LoginAPI:
    def sign_up(self, name, email, password, confirm_password, agreed_terms):
        if not agreed_terms:
            return json.dumps({"success": False, "error": "You need to agree to the Terms & Conditions to continue."})
        if not name or not name.strip():
            return json.dumps({"success": False, "error": "Please enter your name."})
        if not email or not email.strip():
            return json.dumps({"success": False, "error": "Please enter your email."})
        if password != confirm_password:
            return json.dumps({"success": False, "error": "Passwords don't match."})
        if len(password) < 6:
            return json.dumps({"success": False, "error": "Password needs to be at least 6 characters."})

        success, result = auth_sign_up(name.strip(), email.strip(), password)
        if success:
            _complete_login(result)
            return json.dumps({"success": True})
        return json.dumps({"success": False, "error": result})

    def log_in(self, email, password):
        if not email or not password:
            return json.dumps({"success": False, "error": "Please enter both email and password."})
        success, result = auth_log_in(email.strip(), password)
        if success:
            _complete_login(result)
            return json.dumps({"success": True})
        return json.dumps({"success": False, "error": result})

    # --- TEMPORARY dev bypass (remove with DEV_LOGIN_BYPASS in config.py) ---
    def is_dev_bypass_enabled(self):
        return bool(DEV_LOGIN_BYPASS)

    def dev_bypass(self):
        if not DEV_LOGIN_BYPASS:
            return json.dumps({"success": False, "error": "Dev bypass is disabled."})
        remember_dev_bypass()
        _complete_login({"name": "Dev", "email": "", "dev_bypass": True})
        return json.dumps({"success": True})


DEV_BYPASS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aura_dev_bypass.flag")


def remember_dev_bypass():
    """'Skip sign-in (dev)' is remembered, so AURA (incl. its start with Windows) doesn't stop at
    the sign-in screen every time. Deleted when DEV_LOGIN_BYPASS is turned off in config.py."""
    try:
        with open(DEV_BYPASS_FILE, "w") as f:
            f.write("skip sign-in (dev)")
    except Exception:
        pass


def dev_bypass_remembered():
    if not DEV_LOGIN_BYPASS:
        if os.path.exists(DEV_BYPASS_FILE):
            try:
                os.remove(DEV_BYPASS_FILE)
            except Exception:
                pass
        return False
    return os.path.exists(DEV_BYPASS_FILE)


def _complete_login(user_info):
    if _window:
        try:
            _window.hide()
        except Exception:
            pass
    if _on_success_callback:
        _on_success_callback(user_info)


def _on_loaded():
    _ready.set()


def create_login_window(on_success, start_hidden=False):
    """Must be called BEFORE webview.start(), same as AURA's other windows."""
    global _window, _on_success_callback
    _on_success_callback = on_success
    _window = webview.create_window(
        "AURA - Sign In",
        "http://localhost:8734/login.html",
        width=420,
        height=680,
        resizable=False,
        background_color="#04070a",
        hidden=start_hidden,
        js_api=LoginAPI(),
    )
    _window.events.loaded += _on_loaded