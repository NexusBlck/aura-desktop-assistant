# vision_window.py
# AURA's "Iron Man" Vision mode: a full-screen window showing the live
# webcam with every recognised object boxed and labelled. Click anything to
# have AURA identify it and explain it. The camera only runs while this
# window is open.
import os
import json
import threading
import webview
import camera

PAGE_URL = "http://localhost:8734/vision.html"

# The object-recognition model (COCO-SSD, ~67 MB) is saved inside AURA the
# first time Vision mode opens, so every later launch loads it instantly
# from your PC (and works offline) instead of re-downloading it.
MODEL_REMOTE = "https://storage.googleapis.com/tfjs-models/savedmodel/ssd_mobilenet_v2/"
MODEL_LOCAL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "face", "models", "ssd_mobilenet_v2")
_model_download_started = False


def _download_model():
    import requests
    try:
        os.makedirs(MODEL_LOCAL_DIR, exist_ok=True)
        manifest = requests.get(MODEL_REMOTE + "model.json", timeout=30)
        manifest.raise_for_status()
        files = [p for group in manifest.json()["weightsManifest"] for p in group["paths"]]
        for name in files:
            target = os.path.join(MODEL_LOCAL_DIR, name)
            if os.path.exists(target) and os.path.getsize(target) > 0:
                continue
            resp = requests.get(MODEL_REMOTE + name, timeout=120)
            resp.raise_for_status()
            with open(target + ".part", "wb") as f:
                f.write(resp.content)
            os.replace(target + ".part", target)
        # model.json last: the page only uses the local copy once everything is there
        with open(os.path.join(MODEL_LOCAL_DIR, "model.json"), "wb") as f:
            f.write(manifest.content)
        print("(vision: recognition model saved locally)")
    except Exception as e:
        print(f"(vision: couldn't save the recognition model locally - will keep using the online copy: {e})")


def _ensure_model_saved():
    global _model_download_started
    if _model_download_started or os.path.exists(os.path.join(MODEL_LOCAL_DIR, "model.json")):
        return
    _model_download_started = True
    threading.Thread(target=_download_model, daemon=True).start()

_window = None
_ready = threading.Event()


class VisionAPI:
    """Called from the Vision window's JavaScript (window.pywebview.api)."""

    def start_camera(self):
        ok, error = camera.start()
        return json.dumps({"ok": ok, "error": error})

    def start_camera_status(self):
        return json.dumps({"on": camera.is_on()})

    def identify(self, box, hint=""):
        import object_vision
        info = object_vision.identify(box, hint)
        if not info.get("error"):
            # Say what it is out loud, in the background so the panel shows right away.
            line = f"That's {_article(info['name'])} {info['name'].lower()}."
            threading.Thread(target=_say, args=(line,), daemon=True).start()
        return json.dumps(info)

    def deep_scan(self):
        import object_vision
        return json.dumps(object_vision.deep_scan())

    def close(self):
        hide_vision()


def _article(name):
    return "an" if name[:1].lower() in "aeiou" else "a"


def _say(text):
    try:
        from voice import speak
        speak(text)
    except Exception:
        pass


def _on_loaded():
    _ready.set()


def _on_closing():
    """Clicking X hides the window and switches the camera off (AURA keeps running)."""
    hide_vision()
    return False


def create_window():
    """Must be called BEFORE webview.start(), same as AURA's other windows."""
    global _window
    _window = webview.create_window(
        "AURA - Vision",
        PAGE_URL,
        width=1400,
        height=850,
        resizable=True,
        background_color="#02050a",
        hidden=True,
        js_api=VisionAPI(),
    )
    _window.events.loaded += _on_loaded
    _window.events.closing += _on_closing


def show_vision():
    """Opens Vision mode: camera on, window up. Returns a spoken reply."""
    if not _window:
        return "Vision mode isn't available right now."
    ok, error = camera.start()
    if not ok:
        return error
    _ensure_model_saved()
    try:
        _window.show()
        _window.maximize()
        if _ready.is_set():
            _window.evaluate_js("window.visionStart && window.visionStart()")
    except Exception as e:
        return f"Couldn't open the camera window: {e}"
    return "Vision mode online. Everything I recognise is labelled - click anything and I'll tell you what it is."


def hide_vision():
    if _window:
        try:
            _window.evaluate_js("window.visionStop && window.visionStop()")
        except Exception:
            pass
        try:
            _window.hide()
        except Exception:
            pass
    camera.stop()
    return "Camera off."
