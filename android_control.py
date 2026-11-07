# android_control.py
# Controls an Android phone from your PC via ADB (Android Debug Bridge) over
# WiFi - a real, legitimate Google developer tool, not a hack or exploit.
# Requires one-time setup on the phone (Developer Options -> Wireless
# debugging -> pair once). Can open apps, open URLs (which Android routes
# to the right app automatically), and play YouTube videos.
import re
import subprocess
import requests
from ai import ask_json
from config import ADB_PATH

# Add more apps here as you need them - find a package name by searching
# "<app name> package name android" if one you want isn't listed.
ANDROID_APP_PACKAGES = {
    "youtube": "com.google.android.youtube",
    "chrome": "com.android.chrome",
    "spotify": "com.spotify.music",
    "whatsapp": "com.whatsapp",
    "instagram": "com.instagram.android",
    "gmail": "com.google.android.gm",
    "maps": "com.google.android.apps.maps",
    "settings": "com.android.settings",
}


def _run_adb(args):
    try:
        result = subprocess.run([ADB_PATH] + args, capture_output=True, text=True, timeout=15)
        return result.stdout.strip(), result.stderr.strip()
    except FileNotFoundError:
        return None, "ADB isn't installed or ADB_PATH in config.py is wrong."
    except Exception as e:
        return None, str(e)


def is_device_connected():
    out, err = _run_adb(["devices"])
    if out is None:
        return False, err
    lines = [l for l in out.splitlines()[1:] if l.strip()]
    connected = [l for l in lines if l.endswith("device")]
    return len(connected) > 0, None


def _not_connected_message(err):
    return (
        f"No Android device connected via ADB"
        f"{f': {err}' if err else ' - make sure wireless debugging is on and paired.'}"
    )


def open_app_on_android(app_name):
    connected, err = is_device_connected()
    if not connected:
        return _not_connected_message(err)

    package = ANDROID_APP_PACKAGES.get((app_name or "").lower().strip())
    if not package:
        return f"I don't know the package name for '{app_name}' on Android yet - tell me and I can add it."

    _, err = _run_adb(["shell", "monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1"])
    if err and "no activities found" in err.lower():
        return f"Couldn't open {app_name} - it might not be installed on your phone."
    return f"Opened {app_name} on your phone."


def open_url_on_android(url):
    connected, err = is_device_connected()
    if not connected:
        return _not_connected_message(err)

    if not url.startswith("http"):
        url = "https://" + url

    _, err = _run_adb(["shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", url])
    if err:
        return f"Couldn't open that on your phone: {err}"
    return f"Opened {url} on your phone."


def play_youtube_on_android(query):
    connected, err = is_device_connected()
    if not connected:
        return _not_connected_message(err)

    try:
        search_url = "https://www.youtube.com/results?search_query=" + requests.utils.quote(query)
        resp = requests.get(search_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        match = re.search(r'"videoId":"(.*?)"', resp.text)
        if not match:
            return f"Couldn't find '{query}' on YouTube."
        video_url = f"https://www.youtube.com/watch?v={match.group(1)}"
        result = open_url_on_android(video_url)
        return result.replace("Opened", f"Playing '{query}' -") if "Opened" in result else result
    except Exception as e:
        return f"Couldn't play that on your phone: {e}"


ROUTE_PROMPT = """
Determine what phone action the user wants. Respond ONLY with JSON:
{
  "operation": "open_app" | "open_url" | "play_youtube",
  "target": "the app name, url, or youtube search query depending on operation"
}
"""


def handle_phone_request(instruction):
    messages = [
        {"role": "system", "content": ROUTE_PROMPT},
        {"role": "user", "content": instruction},
    ]
    routed = ask_json(messages)
    if not isinstance(routed, dict):
        return "I couldn't figure out what to do on your phone."

    op = routed.get("operation")
    target = routed.get("target", "")

    if op == "open_app":
        return open_app_on_android(target)
    elif op == "open_url":
        return open_url_on_android(target)
    elif op == "play_youtube":
        return play_youtube_on_android(target)
    return "I couldn't determine what to do on your phone."