# ui_skins.py - which interface the PC's AURA windows use (the original HUD or one of the five alternative UIs, each
# with themes). The choice is saved in aura_ui.json so it stays until you change it, even after AURA restarts.
# The look itself is CSS: the phone app's skins (mobile/src/ui/skins/*.css, copied into Face/skins/app so the HUD's
# local web server can serve them) + Face/skins/hud.css for the HUD-only parts + Face/skins/mini.css for Mini AURA.
import json
import os
import shutil

AURA_DIR = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(AURA_DIR, "aura_ui.json")
SRC = os.path.join(AURA_DIR, "mobile", "src", "ui", "skins")
DST = os.path.join(AURA_DIR, "Face", "skins", "app")
SKINS = {"hud": ["default"], "ledger": ["paper", "linen", "night"], "hmi": ["daylight", "nightshift", "hazard"],
         "swiss": ["signal", "klein", "noir"], "console": ["amber", "phosphor", "daylight"], "haven": ["sand", "sage", "dusk"]}


def sync():
    """Keeps the HUD's copy of the skin stylesheets identical to the app's (one source of truth)."""
    os.makedirs(DST, exist_ok=True)
    for name in ("base.css", "ledger.css", "hmi.css", "swiss.css", "console.css", "haven.css"):
        src, dst = os.path.join(SRC, name), os.path.join(DST, name)
        try:
            if not os.path.exists(dst) or os.path.getmtime(src) > os.path.getmtime(dst):
                shutil.copy2(src, dst)
        except OSError:
            pass


def get():
    sync()
    try:
        with open(STATE, encoding="utf-8") as f:
            v = json.load(f)
        if v.get("skin") in SKINS:
            th = v.get("theme") if v.get("theme") in SKINS[v["skin"]] else SKINS[v["skin"]][0]
            return {"skin": v["skin"], "theme": th}
    except Exception:
        pass
    return {"skin": "hud", "theme": "default"}


def set(skin, theme=None):
    skin = skin if skin in SKINS else "hud"
    theme = theme if theme in SKINS[skin] else SKINS[skin][0]
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump({"skin": skin, "theme": theme}, f)
    return {"skin": skin, "theme": theme}
