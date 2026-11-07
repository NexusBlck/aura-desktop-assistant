# location.py
# Auto-detects the user's current city via IP-based geolocation, so weather
# and location-aware features follow them automatically instead of relying
# on a fixed city. Falls back to config.LOCATION if detection ever fails.
import time
import requests
from config import LOCATION as FALLBACK_LOCATION

_cache = {"location": None, "timestamp": 0}
CACHE_SECONDS = 3600  # re-check once per hour, not on every single request


def get_current_location():
    now = time.time()
    if _cache["location"] and (now - _cache["timestamp"]) < CACHE_SECONDS:
        return _cache["location"]

    try:
        resp = requests.get("http://ip-api.com/json/", timeout=5)
        data = resp.json()
        if data.get("status") == "success":
            city = data.get("city", "")
            country = data.get("country", "")
            location = ", ".join(part for part in [city, country] if part)
            if location:
                _cache["location"] = location
                _cache["timestamp"] = now
                return location
    except Exception:
        pass

    return FALLBACK_LOCATION