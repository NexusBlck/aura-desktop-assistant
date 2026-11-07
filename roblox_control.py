# roblox_control.py
# Plays a Roblox game by name, the reliable way: looks the game up with
# Roblox's own search, then launches it with Roblox's official launch link
# (roblox://...) - exactly what the "Play" button on the Roblox website
# does. Much more dependable than trying to click through the app visually.
import os
import re
import uuid
import requests
import active_window

SEARCH_URL = "https://apis.roblox.com/search-api/omni-search"


def find_game(name):
    """Returns {"name", "place_id", "players"} for the best-matching game,
    or None. Roblox's search ranks by relevance and popularity, so the top
    game result is what a person would click."""
    try:
        resp = requests.get(
            SEARCH_URL,
            params={"searchQuery": name, "sessionId": str(uuid.uuid4()), "pageType": "all"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        groups = resp.json().get("searchResults", [])
    except (requests.RequestException, ValueError) as e:
        print(f"(roblox search failed: {e})")
        return None

    # Roblox always returns SOMETHING (even for gibberish it suggests a
    # popular game), so a result only counts if it shares a real word with
    # what was asked for.
    filler = {"the", "and", "game", "games", "roblox", "play", "not", "with", "for"}
    wanted = {w for w in re.findall(r"[a-z0-9]+", name.lower()) if len(w) >= 3 and w not in filler}
    for group in groups:
        if group.get("contentGroupType") != "Game":
            continue
        for game in group.get("contents", [])[:8]:
            game_words = set(re.findall(r"[a-z0-9]+", game.get("name", "").lower()))
            if game.get("rootPlaceId") and (not wanted or wanted & game_words):
                return {
                    "name": _clean_name(game.get("name", name)),
                    "place_id": game["rootPlaceId"],
                    "players": game.get("playerCount"),
                }
    return None


def _clean_name(name):
    """'[🏚️] Adopt Me!' -> 'Adopt Me!' (the emoji tags read badly out loud)."""
    return re.sub(r"\[[^\]]*\]\s*", "", name).strip() or name


def play_roblox_game(name):
    name = (name or "").strip()
    if not name:
        return "Which Roblox game do you want to play?"

    game = find_game(name)
    if not game:
        return f"I couldn't find a Roblox game called {name}."

    before = active_window.snapshot()
    try:
        os.startfile(f"roblox://experiences/start?placeId={game['place_id']}")
    except OSError as e:
        return f"Found {game['name']}, but couldn't launch Roblox: {e}"
    active_window.track_opened(game["name"], "app", before, hint="Roblox", timeout=30)

    players = f" - {game['players']:,} people are playing right now" if game.get("players") else ""
    return f"Launching {game['name']} on Roblox{players}."
