# hf.py
# AURA STUDIO's "magic" AI edits through Hugging Face (free account, no card):
#   edit_image(path, "add sunglasses")   FLUX.1 Kontext - changes a photo exactly as asked, keeping the rest
#   text_to_video("a fox running in snow")  LTX-Video - a short AI video clip (studio.make_video uses these for scenes)
# Needs your free token (huggingface.co -> Settings -> Access Tokens -> "Read"). Set it in the app
# (Studio -> AI EDITS) or in config.py as HF_TOKEN. Free accounts get a daily GPU allowance.
import json
import os
import shutil

AURA_DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN_FILE = os.path.join(AURA_DIR, "aura_hf.json")
EDIT_SPACE = "black-forest-labs/FLUX.1-Kontext-Dev"
VIDEO_SPACE = "Lightricks/ltx-video-distilled"


def token():
    try:
        with open(TOKEN_FILE, encoding="utf-8") as f:
            t = json.load(f).get("token", "")
            if t:
                return t
    except Exception:
        pass
    import config
    return getattr(config, "HF_TOKEN", "") or ""


def set_token(t):
    t = str(t or "").strip()
    if t and not t.startswith("hf_"):
        raise ValueError("That doesn't look like a Hugging Face token (they start with hf_).")
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        json.dump({"token": t}, f)
    return bool(t)


def ready():
    return bool(token())


def _client(space):
    if not ready():
        raise RuntimeError("Magic AI edits need your free Hugging Face token - add it in Studio → AI EDITS.")
    from gradio_client import Client
    return Client(space, token=token(), verbose=False)


def _friendly(e):
    s = str(e)
    if "quota" in s.lower() or "exceeded" in s.lower() or "gpu" in s.lower():
        return "Today's free Hugging Face GPU allowance is used up - try again tomorrow (or later today)."
    return "The AI edit service didn't answer (" + s[:120] + ")."


def edit_image(path, instruction):
    """Returns the path of the edited picture."""
    from gradio_client import handle_file
    try:
        r = _client(EDIT_SPACE).predict(input_image=handle_file(path), prompt=instruction[:500], seed=0, randomize_seed=True,
                                        guidance_scale=2.5, steps=28, api_name="/infer")
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(_friendly(e))
    out = r[0] if isinstance(r, (list, tuple)) else r
    if isinstance(out, dict):
        out = out.get("path") or out.get("url")
    if not out or not os.path.exists(out):
        raise RuntimeError("The AI edit came back empty - try describing the change differently.")
    return out


IMAGE_SPACE = "black-forest-labs/FLUX.1-schnell"


def generate_image(prompt, width=1024, height=1024, seed=0):
    """A sharp AI picture with FLUX (returns a file path) - much better than the no-account service."""
    try:
        r = _client(IMAGE_SPACE).predict(prompt=prompt[:900], seed=int(seed), randomize_seed=not seed, width=int(width), height=int(height),
                                         num_inference_steps=4, api_name="/infer")
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(_friendly(e))
    out = r[0] if isinstance(r, (list, tuple)) else r
    if isinstance(out, dict):
        out = out.get("path") or out.get("url")
    if not out or not os.path.exists(out):
        raise RuntimeError("The AI picture came back empty.")
    return out


def text_to_video(prompt, seconds=3):
    """Returns the path of a short AI video clip (mp4)."""
    try:
        r = _client(VIDEO_SPACE).predict(prompt=prompt[:500], negative_prompt="worst quality, inconsistent motion, blurry, jittery, distorted",
                                         input_image_filepath=None, input_video_filepath=None, height_ui=512, width_ui=704,
                                         mode="text-to-video", duration_ui=max(2, min(6, int(seconds))), ui_frames_to_use=9, seed_ui=42,
                                         randomize_seed=True, ui_guidance_scale=1, improve_texture_flag=True, api_name="/text_to_video")
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(_friendly(e))
    out = r[0] if isinstance(r, (list, tuple)) else r
    if isinstance(out, dict):
        out = out.get("video") or out.get("path")
        if isinstance(out, dict):
            out = out.get("path")
    if not out or not os.path.exists(out):
        raise RuntimeError("The AI video clip came back empty.")
    keep = os.path.join(os.path.expanduser("~"), "Documents", "AURA Studio", "_tmp", os.path.basename(out))
    os.makedirs(os.path.dirname(keep), exist_ok=True)
    shutil.copy(out, keep)
    return keep
