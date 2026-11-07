# spotify_control.py
# Real Spotify search + playback, via Spotify's own Web API (not scripted
# clicking, not guessing) - so it reliably finds and plays the right track.
import time
import base64
import webbrowser
import requests
from config import SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET

# Client-credentials tokens expire after ~1 hour, so the expiry is tracked
# and the token refreshed a minute early (and on any 401).
_token_cache = {"token": None, "expires_at": 0}


def _get_token(force_refresh=False):
    if not force_refresh and _token_cache["token"] and time.time() < _token_cache["expires_at"]:
        return _token_cache["token"]

    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        return None

    auth_str = f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}"
    b64_auth = base64.b64encode(auth_str.encode()).decode()

    try:
        resp = requests.post(
            "https://accounts.spotify.com/api/token",
            data={"grant_type": "client_credentials"},
            headers={"Authorization": f"Basic {b64_auth}"},
            timeout=10,
        )
        data = resp.json()
        token = data.get("access_token")
        _token_cache["token"] = token
        _token_cache["expires_at"] = time.time() + int(data.get("expires_in", 3600)) - 60
        return token
    except Exception:
        return None


def _search(query, token):
    return requests.get(
        "https://api.spotify.com/v1/search",
        params={"q": query, "type": "track", "limit": 1},
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )


def play_spotify(query):
    """Searches Spotify for a track and plays it via the desktop app
    (spotify: URIs are registered to open directly in the app and autoplay)."""
    token = _get_token()
    if not token:
        return "Spotify isn't connected yet - it needs API credentials set up in config.py."

    try:
        resp = _search(query, token)
        if resp.status_code == 401:
            token = _get_token(force_refresh=True)
            if not token:
                return "Spotify rejected AURA's credentials - check the Spotify keys in config.py."
            resp = _search(query, token)
        if resp.status_code != 200:
            return f"Spotify search failed (HTTP {resp.status_code})."

        items = resp.json().get("tracks", {}).get("items", [])
        if not items:
            return f"Couldn't find '{query}' on Spotify."

        track = items[0]
        uri = track["uri"]
        name = track["name"]
        artist = track["artists"][0]["name"] if track.get("artists") else ""

        webbrowser.open(uri)
        return f"Playing {name} by {artist} on Spotify."
    except Exception as e:
        return f"Couldn't play that on Spotify: {e}"