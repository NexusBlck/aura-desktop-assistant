# object_vision.py
# The "what is this?" brain behind the Vision window. The fast on-screen
# detector (runs inside the window) only knows ~80 common objects; when you
# click something, AURA crops exactly that spot from the live camera frame and
# asks the vision AI to identify it properly and explain it. "Deep scan" asks
# the AI to find and name everything in view, including things the fast
# detector doesn't know.
import base64
import cv2
from ai import extract_json
from vision import _ask_vision
import camera

IDENTIFY_PROMPT = """You are the targeting computer of a futuristic heads-up display, looking
at a close-up crop from the user's webcam. {hint}

Identify the MAIN object in the centre of this image as precisely as you
can: exact kind, and brand/model ONLY if clearly readable or unmistakable.
If it's a person, describe only what's visible (clothing, pose) - never
guess who they are, their age, ethnicity or anything sensitive. Be honest
about uncertainty.

Respond ONLY with JSON:
{{
  "name": "short precise name, e.g. 'Wireless optical mouse'",
  "category": "one or two words, e.g. 'Computer peripheral'",
  "confidence": "high" | "medium" | "low",
  "summary": "2 sentences: what it is and what it's for",
  "details": [{{"label": "e.g. Colour / Material / Brand / Typical use / Typical price", "value": "..."}}],
  "fact": "one genuinely interesting fact about this kind of object"
}}
Give 3-5 details. Only state a brand, price or spec if you can actually see
it or it's common knowledge for this kind of object - say "Not visible" otherwise."""

SCAN_PROMPT = """You are the targeting computer of a futuristic heads-up display, looking at
the user's webcam view ({width}x{height} pixels). List every distinct physical
object you can clearly see (up to 12), most prominent first. For people,
just say "Person" - never guess identity or sensitive traits.

Respond ONLY with JSON:
{{"objects": [{{"label": "short name", "x": left px, "y": top px, "w": width px, "h": height px}}]}}"""


def _encode(image, max_side):
    h, w = image.shape[:2]
    scale = min(1.0, max_side / max(h, w))
    if scale < 1:
        image = cv2.resize(image, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return base64.b64encode(buf.tobytes()).decode(), image.shape[1], image.shape[0]


def identify(box, hint="", frame=None, margin=0.12):
    """box = {"x","y","w","h"} as fractions (0-1) of the frame - the object's
    detection box, or a square around where you clicked. Returns a dict for
    the info panel, or {"error": ...}. frame: a photo to use instead of the
    live camera (AURA Mobile sends one from the phone)."""
    if frame is None:
        frame, _ = camera.latest_frame()
    if frame is None:
        return {"error": "The camera isn't on."}
    H, W = frame.shape[:2]
    try:
        x, y, w, h = (float(box[k]) for k in ("x", "y", "w", "h"))
    except (KeyError, TypeError, ValueError):
        return {"error": "No area selected."}
    # add a margin so the whole object (and a little context) is in the crop
    mx, my = w * margin, h * margin
    x0, y0 = max(0, int((x - mx) * W)), max(0, int((y - my) * H))
    x1, y1 = min(W, int((x + w + mx) * W)), min(H, int((y + h + my) * H))
    if x1 - x0 < 24 or y1 - y0 < 24:
        return {"error": "That area is too small to identify."}
    image_b64, _, _ = _encode(frame[y0:y1, x0:x1], 768)
    hint_text = f'A fast detector guessed it might be a "{hint}" - confirm or correct that.' if hint else ""
    try:
        raw = _ask_vision([
            {"type": "text", "text": IDENTIFY_PROMPT.format(hint=hint_text)},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
        ], max_tokens=500)
        info = extract_json(raw)
    except Exception as e:
        return {"error": f"Couldn't identify it: {e}"}
    if not isinstance(info, dict) or not info.get("name"):
        return {"error": "I couldn't make out what that is - try holding it closer and steadier."}
    info["details"] = [d for d in info.get("details", []) if isinstance(d, dict) and d.get("label")][:6]
    return info


def deep_scan(frame=None):
    """Names everything in view. Returns {"objects": [{"label", "x","y","w","h" as fractions}]}.
    Boxes from the AI are approximate - the page draws them dashed. frame: a photo to use
    instead of the live camera."""
    if frame is None:
        frame, _ = camera.latest_frame()
    if frame is None:
        return {"error": "The camera isn't on."}
    image_b64, w, h = _encode(frame, 1024)
    try:
        raw = _ask_vision([
            {"type": "text", "text": SCAN_PROMPT.format(width=w, height=h)},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
        ], max_tokens=700)
        data = extract_json(raw)
    except Exception as e:
        return {"error": f"Deep scan failed: {e}"}
    objects = []
    for o in (data.get("objects", []) if isinstance(data, dict) else [])[:12]:
        try:
            box = {k: float(o[k]) for k in ("x", "y", "w", "h")}
        except (KeyError, TypeError, ValueError):
            continue
        objects.append({"label": str(o.get("label", "Object"))[:40],
                        "x": max(0, box["x"] / w), "y": max(0, box["y"] / h),
                        "w": min(1, box["w"] / w), "h": min(1, box["h"] / h)})
    return {"objects": objects}
