# vision.py
# Lets AURA "see" your screen using a vision-capable model and act on what
# it actually sees - step by step, like a person would: look, click/type,
# look again, repeat until the task is done. Before each click it zooms in on
# the spot it picked and re-aims, because the vision model's first guess can
# be off by 30-250 pixels - enough to miss a button.
import base64
import io
import re
import time
import requests
import pyautogui
from openai import OpenAI, NotFoundError, RateLimitError, BadRequestError
from config import LLM_API_KEY, LLM_BASE_URL
from ai import extract_json
import active_window

# Groq retires/renames models fairly often (qwen3.6 -> qwen3.8 broke this
# before). The first model here that exists is used; if none do, AURA asks
# Groq for its current model list and picks the newest Qwen (Groq's
# vision-capable family) automatically.
VISION_MODEL_CANDIDATES = ["qwen/qwen3.8-27b"]

MAX_STEPS = 10          # safety cap on how many actions one request can take
STEP_PAUSE_SECONDS = 1.5  # let the app react before looking again

client = OpenAI(api_key=LLM_API_KEY or "missing-key", base_url=LLM_BASE_URL)
_vision_model = None


def _discover_vision_model():
    try:
        resp = requests.get(f"{LLM_BASE_URL}/models",
                            headers={"Authorization": f"Bearer {LLM_API_KEY}"}, timeout=10)
        ids = sorted((m["id"] for m in resp.json().get("data", [])), reverse=True)
    except Exception:
        return None
    for candidate in VISION_MODEL_CANDIDATES:
        if candidate in ids:
            return candidate
    qwen = [i for i in ids if i.startswith("qwen/")]
    return qwen[0] if qwen else None


# Groq's free tier allows only ~1000 output tokens per minute for the vision
# model, so each reply is capped and the model's hidden "thinking" is turned
# off - a step only needs a short JSON answer anyway.
MAX_OUTPUT_TOKENS = 300
_supports_reasoning_off = True


def _create(model, content, max_tokens=MAX_OUTPUT_TOKENS):
    global _supports_reasoning_off
    kwargs = dict(model=model, messages=[{"role": "user", "content": content}],
                  temperature=0, max_tokens=max_tokens)
    if _supports_reasoning_off:
        try:
            return client.chat.completions.create(reasoning_effort="none", **kwargs)
        except BadRequestError as e:
            if "reasoning" not in str(e).lower():
                raise
            _supports_reasoning_off = False  # this model doesn't take that option
    return client.chat.completions.create(**kwargs)


def _create_with_retry(model, content, attempts=4, max_tokens=MAX_OUTPUT_TOKENS):
    """Waits and retries when the per-minute limit is hit, instead of
    failing halfway through a multi-step task."""
    for attempt in range(attempts):
        try:
            return _create(model, content, max_tokens)
        except RateLimitError as e:
            if attempt == attempts - 1:
                raise
            match = re.search(r"try again in ([\d.]+)s", str(e))
            wait = min(float(match.group(1)) + 0.5, 30) if match else 10
            print(f"  (vision: hit Groq's per-minute limit, waiting {wait:.0f}s)")
            time.sleep(wait)


def _ask_vision(content, max_tokens=MAX_OUTPUT_TOKENS):
    """Sends one screenshot+prompt to the vision model, switching to a
    currently-available model automatically if the configured one is gone."""
    global _vision_model
    model = _vision_model or VISION_MODEL_CANDIDATES[0]
    try:
        response = _create_with_retry(model, content, max_tokens=max_tokens)
    except NotFoundError:
        replacement = _discover_vision_model()
        if not replacement or replacement == model:
            raise
        print(f"(vision: {model} is gone, switching to {replacement})")
        model = replacement
        response = _create_with_retry(model, content, max_tokens=max_tokens)
    _vision_model = model
    return response.choices[0].message.content or ""


SMALL_SIZE = (1280, 1280)  # screenshots sent to the model are shrunk to fit this


def _encode(image):
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=80)
    return base64.b64encode(buffer.getvalue()).decode()


def _capture_screen():
    """Returns (full-resolution screenshot, shrunk copy for the model).
    The shrunk JPEG is ~10x smaller/faster than a full-size PNG."""
    full = pyautogui.screenshot()
    small = full.copy()
    small.thumbnail(SMALL_SIZE)
    return full, small


STEP_PROMPT = """You are controlling the user's Windows PC by looking at screenshots.
The user's request: "{instruction}"
{target_note}
Steps you've already taken:
{history}

The screenshot is {width}x{height} pixels. Look at it and choose the ONE next
action. Respond ONLY with JSON, no reasoning text:
{{
  "thought": "very short: what you see and why this action",
  "action": "click" | "double_click" | "type" | "press" | "scroll" | "wait" | "done" | "fail",
  "element": "click/double_click only: exactly what you're clicking, e.g. 'the Search bar at the top'",
  "x": pixel x of the element's centre (click/double_click only),
  "y": pixel y of the element's centre (click/double_click only),
  "text": "text to type (type only)",
  "key": "key or combo like enter, esc, tab, ctrl+a (press only)",
  "direction": "up" | "down" (scroll only),
  "message": "for done/fail: ONE short spoken sentence for the user"
}}

Rules:
- PREFER KEYBOARD SHORTCUTS over clicking whenever one exists - they never
  miss. E.g. focusing a search box: "/" (Roblox, YouTube, many web apps),
  ctrl+f (find), ctrl+l or ctrl+k (browser address/search), ctrl+l (Spotify
  search); tab/enter to move and confirm; esc to close popups. Only click
  when there's no reliable shortcut.
- To type into a box (like a search bar), FIRST focus it (shortcut or click), THEN "type" on the next step.
- After typing a search, "press" enter.
- Use "done" as soon as the request is complete - or immediately if the user
  only asked what's on screen (put the answer in "message").
- Use "wait" if something is still loading.
- NEVER buy anything, spend money/Robux/credits, confirm a payment or
  purchase, enter a password, or delete anything - use "fail" and explain
  instead. Stop with "fail" if you're stuck or the screen isn't what you
  expected.
"""

ZOOM_PROMPT = """This is a zoomed-in close-up ({width}x{height} pixels) of part of the screen.
Find: {element}
Respond ONLY with JSON: {{"x": pixel x of its centre, "y": pixel y of its centre}}
in THIS close-up image - or {{"x": null, "y": null}} if it isn't visible here."""

ZOOM_BOX = (560, 360)  # size of the close-up, in real screen pixels


def _parse_step(raw):
    raw = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.DOTALL)
    try:
        step = extract_json(raw)
        return step if isinstance(step, dict) else None
    except ValueError:
        return None


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _aim(step, full, small):
    """Turns the model's rough click position into a precise screen point:
    scale it up to the real screen, then zoom in on that area and ask again
    where exactly the element is. Returns (x, y) in real screen pixels."""
    x, y = _num(step.get("x")), _num(step.get("y"))
    if x is None or y is None:
        return None
    scale_x, scale_y = full.width / small.width, full.height / small.height
    rough_x = min(max(x, 0), small.width) * scale_x
    rough_y = min(max(y, 0), small.height) * scale_y

    element = step.get("element")
    if not element:
        return int(rough_x), int(rough_y)

    # Close-up centred on the rough guess (kept inside the screen).
    box_w, box_h = ZOOM_BOX
    left = int(min(max(rough_x - box_w / 2, 0), max(full.width - box_w, 0)))
    top = int(min(max(rough_y - box_h / 2, 0), max(full.height - box_h, 0)))
    crop = full.crop((left, top, left + box_w, top + box_h))
    try:
        raw = _ask_vision([
            {"type": "text", "text": ZOOM_PROMPT.format(width=crop.width, height=crop.height, element=element)},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{_encode(crop)}"}},
        ])
        fine = _parse_step(raw) or {}
    except Exception as e:
        print(f"  (vision: zoom-in failed, using rough position: {e})")
        fine = {}
    fx, fy = _num(fine.get("x")), _num(fine.get("y"))
    if fx is None or fy is None or not (0 <= fx <= crop.width and 0 <= fy <= crop.height):
        return int(rough_x), int(rough_y)
    return int(left + fx), int(top + fy)


def _execute(step, full, small):
    """Performs one action. Returns a short description for the history, or
    raises ValueError if the step can't be carried out."""
    action = step.get("action")
    if action in ("click", "double_click"):
        point = _aim(step, full, small)
        if not point:
            raise ValueError("no position given for the click")
        (pyautogui.doubleClick if action == "double_click" else pyautogui.click)(*point)
        return f"{action} on {step.get('element') or 'the screen'}"
    if action == "type":
        from pc_control import _paste_text
        text = step.get("text") or ""
        _paste_text(text)
        return f"typed {text!r}"
    if action == "press":
        keys = [k.strip() for k in (step.get("key") or "").lower().split("+") if k.strip()]
        if not keys:
            raise ValueError("no key given")
        pyautogui.hotkey(*keys)
        return f"pressed {'+'.join(keys)}"
    if action == "scroll":
        pyautogui.scroll(-500 if step.get("direction") == "down" else 500)
        return f"scrolled {step.get('direction', 'up')}"
    if action == "wait":
        time.sleep(2)
        return "waited for loading"
    raise ValueError(f"unknown action {action!r}")


def perform_screen_action(instruction):
    """Looks at the screen and works through the request step by step.
    Brings the app AURA last opened (e.g. Roblox) to the front first, so it
    doesn't just see AURA's own window. Returns a spoken-friendly result."""
    target = active_window.current_name()
    if target:
        active_window.focus_target()
        time.sleep(0.6)
    target_note = f"The app the user means is most likely: {target} (AURA just brought it to the front).\n" if target else ""

    history = []
    for step_num in range(1, MAX_STEPS + 1):
        try:
            full, small = _capture_screen()
            prompt = STEP_PROMPT.format(
                instruction=instruction, target_note=target_note,
                width=small.width, height=small.height,
                history="\n".join(f"{i}. {h}" for i, h in enumerate(history, 1)) or "(none yet)",
            )
            raw = _ask_vision([
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{_encode(small)}"}},
            ])
        except Exception as e:
            return f"Couldn't look at your screen: {e}"

        step = _parse_step(raw)
        if not step:
            return "I looked at your screen but couldn't work out what to do next."
        print(f"  (screen step {step_num}: {step.get('action')} - {step.get('thought', '')})")

        if step.get("action") == "done":
            return step.get("message") or "Done."
        if step.get("action") == "fail":
            return step.get("message") or "I got stuck partway through - you might need to take it from here."

        try:
            history.append(_execute(step, full, small))
        except ValueError as e:
            history.append(f"FAILED: {e}")
        except Exception as e:
            return f"Something went wrong while doing that on screen: {e}"
        time.sleep(STEP_PAUSE_SECONDS)

    return f"I took {MAX_STEPS} steps and it's still not finished, so I stopped there to be safe."
