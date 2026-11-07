# auth.py
# Handles sign-up/login for AURA using Firebase Authentication (free tier)
# via its REST API - no separate JS SDK or Node runtime needed, consistent
# with AURA's Python architecture. AURA never stores or handles passwords
# directly - Firebase does all of that security-critical work.
import os
import json
import requests
from config import FIREBASE_API_KEY

IDENTITY_BASE = "https://identitytoolkit.googleapis.com/v1"
SECURETOKEN_URL = "https://securetoken.googleapis.com/v1/token"
SESSION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aura_session.json")


def _friendly_error(message):
    mapping = {
        "EMAIL_EXISTS": "That email is already registered - try logging in instead.",
        "EMAIL_NOT_FOUND": "No account found with that email.",
        "INVALID_PASSWORD": "Incorrect password.",
        "INVALID_LOGIN_CREDENTIALS": "Incorrect email or password.",
        "WEAK_PASSWORD": "Password needs to be at least 6 characters.",
        "INVALID_EMAIL": "That doesn't look like a valid email address.",
    }
    for key, friendly in mapping.items():
        if key in message:
            return friendly
    return message.replace("_", " ").title()


def sign_up(name, email, password):
    """Creates a new Firebase account, sets the display name, sends a
    verification email, and saves a local session. Returns (success, info_or_error)."""
    if not FIREBASE_API_KEY:
        return False, "AURA isn't connected to the sign-up service yet - FIREBASE_API_KEY is missing in .env."

    try:
        resp = requests.post(
            f"{IDENTITY_BASE}/accounts:signUp",
            params={"key": FIREBASE_API_KEY},
            json={"email": email, "password": password, "returnSecureToken": True},
            timeout=15,
        )
        data = resp.json()
        if "error" in data:
            return False, _friendly_error(data["error"].get("message", "Sign up failed."))

        id_token = data["idToken"]

        requests.post(
            f"{IDENTITY_BASE}/accounts:update",
            params={"key": FIREBASE_API_KEY},
            json={"idToken": id_token, "displayName": name, "returnSecureToken": False},
            timeout=15,
        )

        requests.post(
            f"{IDENTITY_BASE}/accounts:sendOobCode",
            params={"key": FIREBASE_API_KEY},
            json={"requestType": "VERIFY_EMAIL", "idToken": id_token},
            timeout=15,
        )

        _save_session(data, name, email)
        return True, {"name": name, "email": email}
    except Exception as e:
        return False, f"Couldn't reach the sign-up service: {e}"


def log_in(email, password):
    if not FIREBASE_API_KEY:
        return False, "AURA isn't connected to the login service yet - FIREBASE_API_KEY is missing in .env."

    try:
        resp = requests.post(
            f"{IDENTITY_BASE}/accounts:signInWithPassword",
            params={"key": FIREBASE_API_KEY},
            json={"email": email, "password": password, "returnSecureToken": True},
            timeout=15,
        )
        data = resp.json()
        if "error" in data:
            return False, _friendly_error(data["error"].get("message", "Login failed."))

        name = data.get("displayName") or email.split("@")[0]
        _save_session(data, name, email)
        return True, {"name": name, "email": email}
    except Exception as e:
        return False, f"Couldn't reach the login service: {e}"


def _save_session(data, name, email):
    _write_session({
        "id_token": data.get("idToken"),
        "refresh_token": data.get("refreshToken"),
        "name": name,
        "email": email,
    })


def _write_session(session):
    try:
        with open(SESSION_FILE, "w", encoding="utf-8") as f:
            json.dump(session, f)
    except Exception:
        pass


def load_session():
    """Returns saved user info if a session exists locally, else None."""
    if os.path.exists(SESSION_FILE):
        try:
            with open(SESSION_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None


def validate_session():
    """Returns the saved session if it's still valid, else None. Uses the
    refresh token to get a fresh ID token from Firebase - if Firebase says
    the account/token is invalid (deleted, disabled, password changed), the
    local session is removed and the user has to log in again. If Firebase
    simply can't be reached (offline), the saved session is trusted so AURA
    still works without internet."""
    session = load_session()
    if not session or not session.get("refresh_token") or not FIREBASE_API_KEY:
        return None

    try:
        resp = requests.post(
            SECURETOKEN_URL,
            params={"key": FIREBASE_API_KEY},
            data={"grant_type": "refresh_token", "refresh_token": session["refresh_token"]},
            timeout=8,
        )
    except requests.RequestException:
        return session  # offline - keep the user signed in

    if resp.status_code >= 500:
        return session  # Firebase having a bad moment - don't sign the user out

    try:
        data = resp.json()
    except ValueError:
        data = {}
    if resp.status_code != 200 or "error" in data:
        log_out()
        return None

    session["id_token"] = data.get("id_token", session.get("id_token"))
    session["refresh_token"] = data.get("refresh_token", session["refresh_token"])
    _write_session(session)
    return session


def log_out():
    if os.path.exists(SESSION_FILE):
        try:
            os.remove(SESSION_FILE)
        except Exception:
            pass