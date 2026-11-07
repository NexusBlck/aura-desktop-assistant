# studio.py
# AURA STUDIO - "make anything, then change it with words". One request -> AURA decides WHAT to make and HOW,
# makes it, and keeps every version so you can keep editing ("make the bars blue", "shorter", "add a title").
#
#   image        AI picture (Pollinations, free)            photo_edit   precise edits of a photo (crop, colours, text,
#   chart        bar / line / pie / donut / scatter / ...                background blur/removal, filters...) + AI edits
#   diagram      flowcharts, mind maps, timelines (Kroki)                with a free Hugging Face token (hf.py)
#   document     Word (.docx)   slides  PowerPoint (.pptx)   spreadsheet  Excel (.xlsx)
#   webpage      a self-contained web page                  svg          logos / icons / vector art
#   qr           QR code                                    audio        narration in a chosen voice / language
#   text         essays, scripts, code, anything written    video        narrated video from AI pictures (+ AI clips)
#   video_edit   trim, speed, crop, text, filters, narration, GIF, merge... on your own videos (ffmpeg)
# Projects live in Documents\AURA Studio\<id>\ (project.json + v1.png, v2.png ...). Jobs run in the background.
import base64
import datetime
import io
import json
import math
import os
import re
import secrets
import subprocess
import threading
import time
import urllib.parse

from ai import ask_llm, extract_json

STUDIO_DIR = os.path.join(os.path.expanduser("~"), "Documents", "AURA Studio")
UPLOADS = os.path.join(STUDIO_DIR, "_uploads")
FONT = next((f for f in (r"C:\Windows\Fonts\segoeuib.ttf", r"C:\Windows\Fonts\arialbd.ttf", r"C:\Windows\Fonts\arial.ttf") if os.path.exists(f)), None)
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_lock = threading.RLock()
_jobs = {}

IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".heic", ".bmp", ".gif")
VIDEO_EXT = (".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv")


def ffmpeg():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def _run(args, timeout=900):
    r = subprocess.run([ffmpeg(), "-hide_banner", "-y", *args], capture_output=True, text=True, timeout=timeout, creationflags=NO_WINDOW)
    if r.returncode != 0:
        raise RuntimeError("video engine: " + (r.stderr.strip().splitlines() or ["failed"])[-1][:200])
    return r


def probe(path):
    """(duration seconds, has audio, width, height) of a video/audio file."""
    r = subprocess.run([ffmpeg(), "-hide_banner", "-i", path], capture_output=True, text=True, creationflags=NO_WINDOW)
    err = r.stderr
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
    dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0.0
    size = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", err)
    return dur, "Audio:" in err, int(size.group(1)) if size else 0, int(size.group(2)) if size else 0


# ------------------------------------------------------------------ projects
def _pdir(pid):
    return os.path.join(STUDIO_DIR, re.sub(r"[^a-z0-9]", "", str(pid))[:20])


def load(pid):
    try:
        with open(os.path.join(_pdir(pid), "project.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save(proj):
    os.makedirs(_pdir(proj["id"]), exist_ok=True)
    tmp = os.path.join(_pdir(proj["id"]), "project.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(proj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, os.path.join(_pdir(proj["id"]), "project.json"))


def projects():
    out = []
    if os.path.isdir(STUDIO_DIR):
        for name in os.listdir(STUDIO_DIR):
            p = load(name)
            if p and p.get("versions"):
                v = p["versions"][-1]
                out.append({"id": p["id"], "title": p["title"], "kind": v["kind"], "versions": len(p["versions"]), "updated": v["t"],
                            "file": os.path.join(_pdir(p["id"]), v["file"]), "thumb": os.path.join(_pdir(p["id"]), v["thumb"]) if v.get("thumb") else None})
    out.sort(key=lambda x: x["updated"], reverse=True)
    return out[:200]


def delete(pid):
    import shutil
    if load(pid):
        shutil.rmtree(_pdir(pid), ignore_errors=True)
        return True
    return False


def save_upload(name, body):
    os.makedirs(UPLOADS, exist_ok=True)
    safe = re.sub(r'[\\/:*?"<>|]+', "_", os.path.basename(name or "upload"))[-80:] or "upload"
    path = os.path.join(UPLOADS, f"{secrets.token_hex(4)}_{safe}")
    with open(path, "wb") as f:
        f.write(body)
    if path.lower().endswith(".heic"):          # iPhone photos -> JPEG (Pillow can't read HEIC without extra plugins)
        try:
            from PIL import Image
            Image.open(path).convert("RGB").save(path[:-5] + ".jpg", quality=92)
            path = path[:-5] + ".jpg"
        except Exception:
            pass
    return {"id": os.path.basename(path), "kind": "video" if path.lower().endswith(VIDEO_EXT) else "image", "name": safe}


def upload_path(uid):
    path = os.path.join(UPLOADS, os.path.basename(str(uid)))
    return path if os.path.isfile(path) else None


# ------------------------------------------------------------------ the planner (what to make, exactly)
KINDS = """KINDS and their "spec":
- image: AI picture. {"prompt": "rich, specific visual description", "width": 1024, "height": 1024, "seed": number}
- photo_edit: change the CURRENT image / an uploaded photo. {"ops": [ops in order], "ai_edit": "" or "a change only AI can do (add/remove/replace things,
  change style, background to a beach...)"}. ops: {"op":"crop","aspect":"1:1|4:5|9:16|16:9"} or {"op":"crop","x":0-1,"y":0-1,"w":0-1,"h":0-1};
  {"op":"rotate","deg":90}; {"op":"flip","dir":"h|v"}; {"op":"resize","width":px}; {"op":"brightness|contrast|saturation|sharpness","amount":0.5-2};
  {"op":"warmth","amount":-1..1}; {"op":"filter","name":"grayscale|sepia|vintage|cinematic|cool|warm|vivid|noir|fade"};
  {"op":"blur","radius":2-30}; {"op":"vignette","strength":0-1}; {"op":"background_blur","strength":5-40}; {"op":"remove_background"};
  {"op":"text","text":"...","position":"top|center|bottom","size":0.03-0.2,"color":"#fff"}; {"op":"border","color":"#fff","width":0.01-0.1};
  {"op":"circle"}; {"op":"pixelate_faces"}; {"op":"upscale","factor":2}; {"op":"denoise"}
- chart: {"type":"bar|horizontal_bar|stacked_bar|line|area|pie|donut|scatter|histogram|radar", "title":"", "labels":[...],
  "series":[{"name":"","values":[numbers]}], "xlabel":"", "ylabel":"", "theme":"dark|light", "colors":["#hex"...] or [], "note":"source / 'approximate'"}
- diagram: {"mermaid": "valid Mermaid code (flowchart, mindmap, timeline, sequenceDiagram, gantt, pie, classDiagram...); put EVERY node label in double quotes, e.g. A[\"Give adrenaline 0.5 mg IM\"]"}
- document: {"title":"", "sections":[{"heading":"", "paragraphs":["..."], "bullets":["..."], "table": [["h1","h2"],["a","b"]] or []}]}
- slides: {"title":"", "subtitle":"", "slides":[{"title":"", "bullets":["..."], "notes":"", "image_prompt":"" (optional AI picture)}]}
- spreadsheet: {"sheets":[{"name":"", "columns":["..."], "rows":[[...]] (numbers as numbers, formulas like "=SUM(B2:B9)")}]}
- webpage: {"html": "<!doctype html> complete, self-contained, beautiful, responsive page (inline CSS/JS, no external files)"}
- svg: {"svg": "<svg ...> complete vector art (logo/icon/illustration), viewBox set"}
- qr: {"data": "text or URL", "color": "#000", "background": "#fff"}
- audio: {"text": "what to say", "language": "English|Urdu|Arabic|Hindi|French|Spanish|German|Chinese|..."}
- text: {"filename": "name.ext (.md for writing, .py/.js/... for code)", "content": "the full text"}
- video: NEW video. {"aspect":"16:9|9:16|1:1", "voice_language":"English", "captions": true, "ai_clips": false,
  "scenes":[{"narration":"what AURA says (1-2 sentences)", "image_prompt":"rich visual description", "seconds": 4-8}]} (3-10 scenes)
- video_edit: change the CURRENT video / uploaded video(s). {"ops":[...]} ops: {"op":"trim","start":s,"end":s}; {"op":"speed","factor":0.25-4};
  {"op":"reverse"}; {"op":"crop","aspect":"9:16|1:1|16:9|4:5"}; {"op":"rotate","deg":90|180|270}; {"op":"resize","width":px};
  {"op":"filter","name":"grayscale|sepia|vivid|cinematic|blur|bright|dark"}; {"op":"text","text":"...","start":s,"end":s,"position":"top|center|bottom"};
  {"op":"fade","in":s,"out":s}; {"op":"mute"}; {"op":"volume","factor":0-3}; {"op":"narration","text":"...","language":"English","mode":"replace|mix"};
  {"op":"stabilize"}; {"op":"merge"} (joins all uploaded videos in order); {"op":"gif","fps":12,"width":480}; {"op":"frame","time":s} (-> a picture)"""


def plan(request, base, attachments):
    now = datetime.datetime.now().strftime("%A %d %B %Y")
    if base:
        ctx = (f"The user is EDITING an existing {base['kind']} (version {base['v']}). Its current spec:\n"
               f"{json.dumps(base.get('spec') or {}, ensure_ascii=False)[:9000]}\n"
               "Return the FULL updated spec (keep everything they didn't ask to change). If they want precise changes to a picture "
               "(colours, crop, text, brightness, background...) use kind photo_edit (it edits the current picture); if they want a new "
               "picture, use image with an updated prompt and the SAME seed. For a video, use video_edit to change it.")
    else:
        ctx = "This is a NEW creation."
    if attachments:
        ctx += "\nThe user attached: " + ", ".join(f"{a['kind']} '{a['name']}'" for a in attachments) + \
               " - to change an attached photo use photo_edit, an attached video use video_edit (or video ops like gif / frame)."
    raw = ask_llm([{"role": "user", "content": f"""You are AURA STUDIO's director. Today is {now}. Turn the user's request into ONE precise creation.
{ctx}
User: "{request}"

{KINDS}

Rules: be precise and do exactly what was asked - real numbers when given (never invent data silently; if you must estimate, say so in the
chart note), sensible defaults otherwise, beautiful results. Pick the single best kind.
Respond ONLY with JSON: {{"kind": "...", "title": "short title", "spec": {{...}}, "message": "one friendly line describing what you made / changed"}}"""}],
        temperature=0.4, reasoning_effort="low", max_tokens=6000)
    data = extract_json(raw)
    if not isinstance(data, dict) or data.get("kind") not in EXECUTORS:
        raise RuntimeError("I couldn't work out what to make - try saying it a bit differently.")
    return data


# ------------------------------------------------------------------ makers
_last_picture = [0.0]


def _pollinations(prompt, width, height, seed):
    """An AI picture (JPEG bytes): FLUX through Hugging Face when you've added a token (sharp), else the free
    no-account service (its watermark strip is trimmed off). Requests are paced - the free service rate-limits."""
    try:
        import hf
        if hf.ready():
            from PIL import Image
            p = hf.generate_image(prompt, min(1344, int(width) // 16 * 16), min(1344, int(height) // 16 * 16), seed)
            buf = io.BytesIO(); Image.open(p).convert("RGB").save(buf, "JPEG", quality=93)
            return buf.getvalue()
    except Exception as e:
        print(f"(studio: FLUX unavailable, using the free service: {e})")
    import requests
    from PIL import Image
    tall = int(int(height) * 1.08)                       # extra strip at the bottom for the watermark, cut off below
    url = ("https://image.pollinations.ai/prompt/" + urllib.parse.quote(prompt[:900]) +
           f"?width={int(width)}&height={tall}&seed={int(seed)}&nologo=true&enhance=true")
    for attempt in range(6):
        wait = 6 - (time.time() - _last_picture[0])
        if wait > 0:
            time.sleep(wait)
        _last_picture[0] = time.time()
        try:
            r = requests.get(url, timeout=120)
            if r.ok and r.headers.get("content-type", "").startswith("image"):
                im = Image.open(io.BytesIO(r.content)).convert("RGB")
                im = im.crop((0, 0, im.width, int(im.height / 1.08)))
                buf = io.BytesIO(); im.save(buf, "JPEG", quality=92)
                return buf.getvalue()
        except Exception:
            pass
        time.sleep(4 + attempt * 5)
    raise RuntimeError("The free picture service is busy - try again in a minute.")


def _title_card(text, W, H):
    """A styled card (AURA colours) for a video scene whose picture couldn't be made."""
    from PIL import Image, ImageDraw
    import numpy as np
    yy = np.linspace(0, 1, H)[:, None]
    grad = (np.stack([5 + 20 * yy, 11 + 40 * yy, 18 + 60 * yy], -1) * np.ones((1, W, 1))).astype(np.uint8)
    img = Image.fromarray(grad).convert("RGBA")
    d = ImageDraw.Draw(img)
    for r in range(3):
        d.ellipse((W / 2 - 160 - r * 40, H / 2 - 160 - r * 40, W / 2 + 160 + r * 40, H / 2 + 160 + r * 40), outline=(58, 214, 255, 90 - r * 25), width=2)
    return _draw_caption(img, text, "center", 0.06, "#9ef3ff").convert("RGB")


def make_image(spec, job):
    seed = int(spec.get("seed") or secrets.randbelow(10 ** 6))
    spec["seed"] = seed
    w, h = int(spec.get("width") or 1024), int(spec.get("height") or 1024)
    data = _pollinations(spec.get("prompt") or "abstract art", min(w, 1536), min(h, 1536), seed)
    return job.write("jpg", data)


# ---- photo edits (Pillow / OpenCV): precise and repeatable
def _ratio(a):
    try:
        x, y = [float(v) for v in str(a).split(":")]
        return x / y
    except Exception:
        return None


def edit_photo(spec, job):
    from PIL import Image, ImageEnhance, ImageFilter, ImageOps, ImageDraw, ImageFont
    import numpy as np
    src = job.input_image()
    img = ImageOps.exif_transpose(Image.open(src)).convert("RGBA")
    for op in spec.get("ops") or []:
        name = op.get("op")
        W, H = img.size
        if name == "crop":
            r = _ratio(op.get("aspect"))
            if r:
                if W / H > r:
                    nw = int(H * r); img = img.crop(((W - nw) // 2, 0, (W - nw) // 2 + nw, H))
                else:
                    nh = int(W / r); img = img.crop((0, (H - nh) // 2, W, (H - nh) // 2 + nh))
            elif op.get("w"):
                x, y, w, h = [float(op.get(k) or 0) for k in ("x", "y", "w", "h")]
                img = img.crop((int(x * W), int(y * H), int((x + w) * W), int((y + h) * H)))
        elif name == "rotate":
            img = img.rotate(-float(op.get("deg") or 90), expand=True)
        elif name == "flip":
            img = ImageOps.mirror(img) if op.get("dir", "h") == "h" else ImageOps.flip(img)
        elif name == "resize":
            nw = int(op.get("width") or W); img = img.resize((nw, int(H * nw / W)), Image.LANCZOS)
        elif name == "upscale":
            f = min(4, float(op.get("factor") or 2)); img = img.resize((int(W * f), int(H * f)), Image.LANCZOS).filter(ImageFilter.UnsharpMask(2, 80, 2))
        elif name in ("brightness", "contrast", "saturation", "sharpness"):
            enh = {"brightness": ImageEnhance.Brightness, "contrast": ImageEnhance.Contrast, "saturation": ImageEnhance.Color, "sharpness": ImageEnhance.Sharpness}[name]
            a = img.getchannel("A"); img = enh(img.convert("RGB")).enhance(float(op.get("amount") or 1.2)).convert("RGBA"); img.putalpha(a)
        elif name == "warmth":
            amt = max(-1, min(1, float(op.get("amount") or 0.3)))
            arr = np.asarray(img).astype(np.float32)
            arr[..., 0] *= 1 + 0.18 * amt; arr[..., 2] *= 1 - 0.18 * amt
            img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGBA")
        elif name == "filter":
            img = _photo_filter(img, op.get("name", "vivid"))
        elif name == "blur":
            img = img.filter(ImageFilter.GaussianBlur(float(op.get("radius") or 6)))
        elif name == "vignette":
            s = float(op.get("strength") or 0.5)
            yy, xx = np.ogrid[:H, :W]
            d = np.sqrt(((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2)
            mask = np.clip(1 - s * np.clip(d - 0.45, 0, 1) * 1.6, 0, 1)
            arr = np.asarray(img).astype(np.float32); arr[..., :3] *= mask[..., None]
            img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGBA")
        elif name in ("background_blur", "remove_background"):
            mask = _subject_mask(img)
            if name == "remove_background":
                img.putalpha(Image.fromarray(mask))
            else:
                blurred = img.filter(ImageFilter.GaussianBlur(float(op.get("strength") or 18)))
                img = Image.composite(img, blurred, Image.fromarray(mask).filter(ImageFilter.GaussianBlur(6)))
        elif name == "text":
            img = _draw_caption(img, str(op.get("text") or ""), op.get("position", "bottom"), float(op.get("size") or 0.07), op.get("color") or "#ffffff")
        elif name == "border":
            bw = max(2, int(min(W, H) * float(op.get("width") or 0.03)))
            img = ImageOps.expand(img, border=bw, fill=op.get("color") or "#ffffff")
        elif name == "circle":
            side = min(W, H); img = img.crop(((W - side) // 2, (H - side) // 2, (W - side) // 2 + side, (H - side) // 2 + side))
            m = Image.new("L", (side, side), 0); ImageDraw.Draw(m).ellipse((0, 0, side, side), fill=255); img.putalpha(m)
        elif name == "pixelate_faces":
            img = _pixelate_faces(img)
        elif name == "denoise":
            import cv2
            arr = cv2.fastNlMeansDenoisingColored(np.asarray(img.convert("RGB"))[..., ::-1], None, 6, 6, 7, 21)
            a = img.getchannel("A"); img = Image.fromarray(arr[..., ::-1]).convert("RGBA"); img.putalpha(a)
    if (spec.get("ai_edit") or "").strip():
        import hf
        job.step("AI edit (Hugging Face)…")
        tmp = job.temp("png"); img.save(tmp)
        img = Image.open(hf.edit_image(tmp, spec["ai_edit"])).convert("RGBA")
    transparent = img.getchannel("A").getextrema()[0] < 255
    buf = io.BytesIO()
    if transparent:
        img.save(buf, "PNG"); return job.write("png", buf.getvalue())
    img.convert("RGB").save(buf, "JPEG", quality=93); return job.write("jpg", buf.getvalue())


def _photo_filter(img, name):
    from PIL import ImageEnhance, ImageOps, Image
    import numpy as np
    a = img.getchannel("A"); rgb = img.convert("RGB")
    if name in ("grayscale", "noir"):
        rgb = ImageOps.grayscale(rgb).convert("RGB")
        if name == "noir":
            rgb = ImageEnhance.Contrast(rgb).enhance(1.5)
    elif name in ("sepia", "vintage"):
        arr = np.asarray(rgb).astype(np.float32)
        m = np.array([[.393, .769, .189], [.349, .686, .168], [.272, .534, .131]])
        arr = arr @ m.T
        if name == "vintage":
            arr = arr * 0.9 + 20
        rgb = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    elif name == "cinematic":
        rgb = ImageEnhance.Contrast(ImageEnhance.Color(rgb).enhance(0.8)).enhance(1.2)
        arr = np.asarray(rgb).astype(np.float32); arr[..., 2] *= 1.06; arr[..., 0] *= 0.97
        rgb = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    elif name in ("cool", "warm"):
        arr = np.asarray(rgb).astype(np.float32); k = 1 if name == "warm" else -1
        arr[..., 0] *= 1 + 0.12 * k; arr[..., 2] *= 1 - 0.12 * k
        rgb = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    elif name == "fade":
        arr = np.asarray(rgb).astype(np.float32) * 0.82 + 35
        rgb = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    else:      # vivid
        rgb = ImageEnhance.Contrast(ImageEnhance.Color(rgb).enhance(1.4)).enhance(1.1)
    out = rgb.convert("RGBA"); out.putalpha(a)
    return out


def _subject_mask(img):
    """Foreground mask (0/255): OpenCV GrabCut seeded with the centre of the photo (or the faces found)."""
    import cv2
    import numpy as np
    rgb = np.asarray(img.convert("RGB"))[..., ::-1].copy()
    H, W = rgb.shape[:2]
    scale = 640 / max(H, W) if max(H, W) > 640 else 1
    small = cv2.resize(rgb, (int(W * scale), int(H * scale))) if scale < 1 else rgb
    h, w = small.shape[:2]
    rect = (int(w * 0.08), int(h * 0.05), int(w * 0.84), int(h * 0.92))
    mask = np.zeros((h, w), np.uint8)
    cv2.grabCut(small, mask, rect, np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64), 5, cv2.GC_INIT_WITH_RECT)
    m = np.where((mask == 1) | (mask == 3), 255, 0).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    return cv2.resize(m, (W, H), interpolation=cv2.INTER_LINEAR)


def _pixelate_faces(img):
    import cv2
    import numpy as np
    from PIL import Image
    arr = np.asarray(img.convert("RGB"))[..., ::-1].copy()
    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    for (x, y, w, h) in cascade.detectMultiScale(cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY), 1.1, 5, minSize=(30, 30)):
        roi = arr[y:y + h, x:x + w]
        roi = cv2.resize(cv2.resize(roi, (10, 10), interpolation=cv2.INTER_LINEAR), (w, h), interpolation=cv2.INTER_NEAREST)
        arr[y:y + h, x:x + w] = roi
    a = img.getchannel("A"); out = Image.fromarray(arr[..., ::-1]).convert("RGBA"); out.putalpha(a)
    return out


def _font(px):
    from PIL import ImageFont
    try:
        return ImageFont.truetype(FONT, max(10, int(px)))
    except Exception:
        return ImageFont.load_default()


def _draw_caption(img, text, position="bottom", size=0.07, color="#ffffff"):
    from PIL import ImageDraw
    import textwrap
    W, H = img.size
    font = _font(H * size)
    d = ImageDraw.Draw(img)
    chars = max(8, int(W / (H * size * 0.55)))
    lines = textwrap.wrap(text, chars) or [""]
    lh = int(H * size * 1.25)
    total = lh * len(lines)
    y0 = int(H * 0.05) if position == "top" else (H - total) // 2 if position == "center" else H - total - int(H * 0.06)
    for i, line in enumerate(lines):
        tw = d.textlength(line, font=font)
        x, y = (W - tw) / 2, y0 + i * lh
        for dx, dy in ((2, 2), (-2, 2), (2, -2), (-2, -2), (0, 3)):
            d.text((x + dx, y + dy), line, font=font, fill=(0, 0, 0, 200))
        d.text((x, y), line, font=font, fill=color)
    return img


# ---- charts (matplotlib, AURA's look)
def make_chart(spec, job):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    dark = spec.get("theme", "dark") != "light"
    fg, bg = ("#d6f6ff", "#050b12") if dark else ("#0b2833", "#ffffff")
    palette = spec.get("colors") or ["#3ad6ff", "#a78bfa", "#ffb547", "#4ef0a3", "#ff5a6a", "#60a5fa", "#f472b6", "#facc15", "#34d399", "#c084fc"]
    labels = [str(x) for x in spec.get("labels") or []]
    series = [s for s in spec.get("series") or [] if isinstance(s, dict)]
    kind = spec.get("type", "bar")
    fig = plt.figure(figsize=(10, 6.25), dpi=160, facecolor=bg)
    polar = kind == "radar"
    ax = fig.add_subplot(111, polar=polar, facecolor=bg)
    vals = lambda s: [float(v) if v is not None else 0 for v in s.get("values") or []]
    if kind in ("pie", "donut"):
        s = series[0] if series else {"values": []}
        wedges, texts, autotexts = ax.pie(vals(s), labels=labels, autopct="%1.1f%%", startangle=90, colors=palette[:len(labels)],
                                          wedgeprops={"width": 0.42, "edgecolor": bg} if kind == "donut" else {"edgecolor": bg},
                                          textprops={"color": fg, "fontsize": 11}, pctdistance=0.78 if kind == "donut" else 0.62)
        for t in autotexts:
            t.set_color("#02121c" if dark else "#ffffff"); t.set_fontweight("bold")
        ax.axis("equal")
    elif kind == "radar":
        n = len(labels)
        angles = [i / n * 2 * math.pi for i in range(n)] + [0]
        for i, s in enumerate(series):
            v = vals(s) + vals(s)[:1]
            ax.plot(angles, v, color=palette[i % len(palette)], linewidth=2, label=s.get("name"))
            ax.fill(angles, v, color=palette[i % len(palette)], alpha=0.18)
        ax.set_xticks(angles[:-1]); ax.set_xticklabels(labels, color=fg)
        ax.tick_params(colors=fg); ax.grid(color=fg, alpha=0.2)
    else:
        import numpy as np
        x = np.arange(len(labels))
        width = 0.8 / max(1, len(series)) if kind == "bar" else 0.6
        bottom = np.zeros(len(labels))
        for i, s in enumerate(series):
            v = np.array(vals(s)[:len(labels)] + [0] * max(0, len(labels) - len(vals(s))))
            c = palette[i % len(palette)]
            if kind == "bar":
                ax.bar(x - 0.4 + width * (i + 0.5), v, width, color=c, label=s.get("name"))
            elif kind == "stacked_bar":
                ax.bar(x, v, 0.6, bottom=bottom, color=c, label=s.get("name")); bottom += v
            elif kind == "horizontal_bar":
                ax.barh(x + i * (0.8 / max(1, len(series))), v, 0.8 / max(1, len(series)), color=c, label=s.get("name"))
            elif kind == "area":
                ax.fill_between(x, v, alpha=0.35, color=c); ax.plot(x, v, color=c, linewidth=2, label=s.get("name"))
            elif kind == "scatter":
                ax.scatter(x if not s.get("x") else s["x"], v, color=c, s=60, label=s.get("name"))
            elif kind == "histogram":
                ax.hist(v, bins=min(20, max(5, len(v) // 2)), color=c, alpha=0.85, label=s.get("name"))
            else:
                ax.plot(x, v, color=c, linewidth=2.5, marker="o", label=s.get("name"))
        if kind == "horizontal_bar":
            ax.set_yticks(x); ax.set_yticklabels(labels)
        elif kind != "histogram" and labels:
            ax.set_xticks(x); ax.set_xticklabels(labels, rotation=30 if max((len(l) for l in labels), default=0) > 6 else 0, ha="right" if max((len(l) for l in labels), default=0) > 6 else "center")
        ax.set_xlabel(spec.get("xlabel", ""), color=fg); ax.set_ylabel(spec.get("ylabel", ""), color=fg)
        ax.tick_params(colors=fg)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            ax.spines[side].set_color(fg); ax.spines[side].set_alpha(0.4)
        ax.grid(axis="y" if kind != "horizontal_bar" else "x", color=fg, alpha=0.12)
    if len(series) > 1 or (kind not in ("pie", "donut") and series and series[0].get("name")):
        leg = ax.legend(frameon=False, labelcolor=fg)
    ax.set_title(spec.get("title", ""), color=fg, fontsize=16, fontweight="bold", pad=16)
    if spec.get("note"):
        fig.text(0.99, 0.01, spec["note"], ha="right", va="bottom", color=fg, alpha=0.55, fontsize=8)
    fig.tight_layout()
    buf = io.BytesIO(); fig.savefig(buf, format="png", facecolor=bg); plt.close(fig)
    return job.write("png", buf.getvalue())


def _quote_labels(code):
    """Mermaid breaks on brackets / punctuation inside node labels: A[Give adrenaline (IM)] -> A["Give adrenaline (IM)"]."""
    shapes = {"[": "]", "(": ")", "{": "}"}

    def fix_line(line):
        out, i = [], 0
        while i < len(line):
            m = re.match(r"[A-Za-z][\w-]*", line[i:])
            if m and (i == 0 or not (line[i - 1].isalnum() or line[i - 1] == "_")):
                j = i + m.end()
                if j < len(line) and line[j] in shapes and (j + 1 >= len(line) or line[j + 1] != '"'):
                    close, depth, k = shapes[line[j]], 0, j
                    while k < len(line):                       # find the matching close bracket (labels may contain brackets)
                        if line[k] == line[j]:
                            depth += 1
                        elif line[k] == close:
                            depth -= 1
                            if depth == 0:
                                break
                        k += 1
                    if k < len(line):
                        double = j + 1 < k and line[j + 1] == line[j] and line[k - 1] == close     # (( circle )), [[ ]], {{ }}
                        label = (line[j + 2:k - 1] if double else line[j + 1:k]).replace('"', "'")
                        if label.startswith('"') or not label.strip():
                            out.append(line[i:k + 1])
                        else:
                            o, c = (line[j] * 2, close * 2) if double else (line[j], close)
                            out.append(f'{m.group(0)}{o}"{label}"{c}')
                        i = k + 1
                        continue
                out.append(m.group(0)); i = j
                continue
            out.append(line[i]); i += 1
        return "".join(out)
    head = code.lstrip().split("\n", 1)[0].lower()
    if not head.startswith(("graph", "flowchart")):
        return code                                            # only flowcharts use this label syntax
    return "\n".join(fix_line(l) if not l.strip().startswith(("%%", "classDef", "style", "linkStyle", "class ")) else l for l in code.split("\n"))


def make_diagram(spec, job):
    import requests
    code = _quote_labels(spec.get("mermaid") or "graph TD; A-->B")
    for attempt in range(6):
        r = None
        try:
            r = requests.post("https://kroki.io/mermaid/png", data=code.encode("utf-8"), headers={"Content-Type": "text/plain"}, timeout=60)
        except Exception:
            pass
        if r is not None and r.ok:
            break
        if attempt == 5:
            raise RuntimeError("The diagram didn't render - " + (r.text[:120] if r is not None else "the diagram service didn't answer"))
        if r is None or r.status_code >= 500:            # the free renderer hiccupped - just try again
            time.sleep(2 + attempt * 2)
            continue
        job.step("Fixing the diagram…")
        fixed = ask_llm([{"role": "user", "content": f"This Mermaid code fails with:\n{r.text[:600]}\n\nCode:\n{code}\n\n"
                          "Return ONLY the corrected Mermaid code (no fences). Put every node label in double quotes."}],
                        temperature=0, reasoning_effort="low", max_tokens=3000)
        code = _quote_labels(re.sub(r"^```(?:mermaid)?\s*|\s*```$", "", fixed.strip()))
    spec["mermaid"] = code
    with open(job.path("mmd"), "w", encoding="utf-8") as f:
        f.write(code)
    return job.write("png", r.content)


# ---- office files
def make_document(spec, job):
    import docx
    from docx.shared import Pt, RGBColor
    d = docx.Document()
    d.add_heading(spec.get("title") or "Document", 0)
    for sec in spec.get("sections") or []:
        if sec.get("heading"):
            d.add_heading(sec["heading"], 1)
        for p in sec.get("paragraphs") or []:
            d.add_paragraph(str(p))
        for b in sec.get("bullets") or []:
            d.add_paragraph(str(b), style="List Bullet")
        table = sec.get("table") or []
        if table and isinstance(table[0], list):
            t = d.add_table(rows=len(table), cols=max(len(r) for r in table)); t.style = "Light Grid Accent 1"
            for i, row in enumerate(table):
                for j, cell in enumerate(row):
                    t.cell(i, j).text = str(cell)
    path = job.path("docx"); d.save(path)
    return path


def make_slides(spec, job):
    from pptx import Presentation
    from pptx.util import Inches, Pt
    from pptx.dml.color import RGBColor
    prs = Presentation(); prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    bg, fg, accent = RGBColor(5, 11, 18), RGBColor(214, 246, 255), RGBColor(58, 214, 255)

    def paint(slide):
        fill = slide.background.fill; fill.solid(); fill.fore_color.rgb = bg

    def text(slide, txt, x, y, w, h, size, color, bold=False):
        tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h)); tf = tb.text_frame; tf.word_wrap = True
        p = tf.paragraphs[0]; p.text = txt; p.font.size = Pt(size); p.font.bold = bold; p.font.color.rgb = color
        return tf

    s = prs.slides.add_slide(prs.slide_layouts[6]); paint(s)
    text(s, spec.get("title") or "Presentation", 0.8, 2.5, 11.7, 1.4, 48, accent, True)
    text(s, spec.get("subtitle") or "", 0.8, 3.9, 11.7, 1, 22, fg)
    slides = spec.get("slides") or []
    for i, sl in enumerate(slides):
        job.step(f"Slide {i + 1}/{len(slides)}…")
        s = prs.slides.add_slide(prs.slide_layouts[6]); paint(s)
        text(s, sl.get("title") or "", 0.6, 0.4, 12, 1, 32, accent, True)
        has_img = bool((sl.get("image_prompt") or "").strip())
        tf = text(s, "", 0.7, 1.6, 6.6 if has_img else 12, 5.4, 20, fg)
        for j, b in enumerate(sl.get("bullets") or []):
            p = tf.paragraphs[0] if j == 0 else tf.add_paragraph()
            p.text = "•  " + str(b); p.font.size = Pt(20); p.font.color.rgb = fg; p.space_after = Pt(10)
        if has_img:
            try:
                img = job.temp("jpg")
                with open(img, "wb") as f:
                    f.write(_pollinations(sl["image_prompt"], 1024, 768, secrets.randbelow(10 ** 6)))
                s.shapes.add_picture(img, Inches(7.6), Inches(1.6), width=Inches(5.2))
            except Exception:
                pass
        if sl.get("notes"):
            s.notes_slide.notes_text_frame.text = sl["notes"]
    path = job.path("pptx"); prs.save(path)
    return path


def make_spreadsheet(spec, job):
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    wb = openpyxl.Workbook(); wb.remove(wb.active)
    for sh in spec.get("sheets") or [{"name": "Sheet1", "columns": [], "rows": []}]:
        ws = wb.create_sheet((sh.get("name") or "Sheet")[:31])
        cols = sh.get("columns") or []
        if cols:
            ws.append(cols)
            for c in ws[1]:
                c.font = Font(bold=True, color="FFFFFF"); c.fill = PatternFill("solid", fgColor="0A8FB8")
        for row in sh.get("rows") or []:
            ws.append(row)
        for i, col in enumerate(ws.columns, 1):
            width = max((len(str(c.value)) for c in col if c.value is not None), default=8)
            ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = min(48, width + 3)
    path = job.path("xlsx"); wb.save(path)
    return path


def make_webpage(spec, job):
    return job.write("html", (spec.get("html") or "<!doctype html><p>Empty</p>").encode("utf-8"))


def make_svg(spec, job):
    svg = spec.get("svg") or ""
    if "<svg" not in svg:
        raise RuntimeError("That vector art came out empty - try again.")
    return job.write("svg", svg.encode("utf-8"))


def make_qr(spec, job):
    import qrcode
    q = qrcode.QRCode(border=2, box_size=16, error_correction=qrcode.constants.ERROR_CORRECT_M)
    q.add_data(spec.get("data") or "AURA"); q.make(fit=True)
    img = q.make_image(fill_color=spec.get("color") or "#000000", back_color=spec.get("background") or "#ffffff")
    buf = io.BytesIO(); img.save(buf, "PNG")
    return job.write("png", buf.getvalue())


def make_audio(spec, job):
    import nexus
    b64, ctype = nexus.speech(spec.get("text") or "", spec.get("language") or "English")
    if not b64:
        raise RuntimeError("Couldn't make the audio.")
    return job.write("mp3", base64.b64decode(b64))


def make_text(spec, job):
    name = os.path.basename(spec.get("filename") or "text.md")
    ext = (os.path.splitext(name)[1].lstrip(".") or "md")[:6]
    return job.write(ext, (spec.get("content") or "").encode("utf-8"))


# ---- video: narrated scenes from AI pictures (Ken Burns motion + captions), optionally AI clips
def make_video(spec, job):
    import cv2
    import numpy as np
    from PIL import Image
    import nexus
    aspect = spec.get("aspect", "16:9")
    W, H = {"9:16": (720, 1280), "1:1": (1080, 1080)}.get(aspect, (1280, 720))
    fps = 24
    scenes = (spec.get("scenes") or [])[:12]
    if not scenes:
        raise RuntimeError("No scenes to make.")
    silent = job.temp("mp4")
    out = cv2.VideoWriter(silent, cv2.VideoWriter_fourcc(*"mp4v"), fps, (W, H))
    audio_parts, durations = [], []
    try:
        for i, sc in enumerate(scenes):
            job.step(f"Scene {i + 1}/{len(scenes)}: picture + voice…")
            narr = (sc.get("narration") or "").strip()
            dur = float(sc.get("seconds") or 5)
            if narr:
                b64, _ = nexus.speech(narr, spec.get("voice_language") or "English")
                if b64:
                    ap = job.temp("mp3")
                    with open(ap, "wb") as f:
                        f.write(base64.b64decode(b64))
                    dur = max(dur, probe(ap)[0] + 0.5)
                    audio_parts.append((ap, dur))
                else:
                    audio_parts.append((None, dur))
            else:
                audio_parts.append((None, dur))
            durations.append(dur)
            clip = None
            if spec.get("ai_clips"):
                try:
                    import hf
                    job.step(f"Scene {i + 1}: AI video clip (Hugging Face)…")
                    clip = hf.text_to_video(sc.get("image_prompt") or narr)
                except Exception as e:
                    print(f"(studio: AI clip failed, using a picture: {e})")
            if clip:
                cap = cv2.VideoCapture(clip); frames = []
                while True:
                    ok, fr = cap.read()
                    if not ok:
                        break
                    frames.append(cv2.resize(fr, (W, H)))
                cap.release()
                for n in range(int(dur * fps)):
                    fr = frames[min(len(frames) - 1, int(n * len(frames) / (dur * fps)))].copy() if frames else np.zeros((H, W, 3), np.uint8)
                    if spec.get("captions", True) and narr:
                        fr = np.asarray(_draw_caption(Image.fromarray(fr[..., ::-1]).convert("RGBA"), narr, "bottom", 0.045).convert("RGB"))[..., ::-1]
                    out.write(np.ascontiguousarray(fr))
                continue
            try:
                pic = Image.open(io.BytesIO(_pollinations(sc.get("image_prompt") or narr or "abstract", int(W * 1.15), int(H * 1.15), secrets.randbelow(10 ** 6)))).convert("RGB")
            except RuntimeError:
                pic = _title_card(narr or spec.get("title") or "AURA", int(W * 1.2), int(H * 1.2))
            pic = pic.resize((int(W * 1.2), int(H * 1.2)), Image.LANCZOS)
            base = np.asarray(pic)[..., ::-1]
            cap_layer = None
            if spec.get("captions", True) and narr:
                cap_layer = np.asarray(_draw_caption(Image.new("RGBA", (W, H), (0, 0, 0, 0)), narr, "bottom", 0.045))
            n = int(dur * fps)
            zin = i % 2 == 0
            for k in range(n):
                t = k / max(1, n - 1)
                z = 1 + 0.12 * (t if zin else 1 - t)                # slow zoom in / out
                cw, ch = int(base.shape[1] / z / 1.2 * 1.0), int(base.shape[0] / z / 1.2)
                cw, ch = min(cw, base.shape[1]), min(ch, base.shape[0])
                px = int((base.shape[1] - cw) * (0.3 + 0.4 * t)); py = int((base.shape[0] - ch) * 0.5)
                fr = cv2.resize(base[py:py + ch, px:px + cw], (W, H), interpolation=cv2.INTER_LINEAR)
                if k < 8 or k > n - 8:                          # soft fade between scenes
                    a = min(k, n - 1 - k) / 8; fr = (fr * a).astype(np.uint8)
                if cap_layer is not None:
                    alpha = cap_layer[..., 3:4] / 255.0
                    fr = (fr * (1 - alpha) + cap_layer[..., :3][..., ::-1] * alpha).astype(np.uint8)
                out.write(np.ascontiguousarray(fr))
    finally:
        out.release()
    job.step("Mixing voice and video…")
    total = sum(durations)
    args, filt, labels = ["-i", silent], [], []
    idx = 1
    for ap, dur in audio_parts:
        if ap:
            args += ["-i", ap]; filt.append(f"[{idx}:a]apad=whole_dur={dur:.2f},atrim=0:{dur:.2f}[a{idx}]"); labels.append(f"[a{idx}]"); idx += 1
        else:
            args += ["-f", "lavfi", "-t", f"{dur:.2f}", "-i", "anullsrc=r=24000:cl=mono"]; labels.append(f"[{idx}:a]"); idx += 1
    filt.append("".join(labels) + f"concat=n={len(labels)}:v=0:a=1[aout]")
    path = job.path("mp4")
    _run(args + ["-filter_complex", ";".join(filt), "-map", "0:v", "-map", "[aout]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
                 "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-t", f"{total:.2f}", "-movflags", "+faststart", path])
    return path


# ---- video edits (ffmpeg): precise, one pass
def _esc(text):
    return str(text).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\u2019").replace("%", "\\%").replace(",", "\\,")


def edit_video(spec, job):
    ops = spec.get("ops") or []
    inputs = job.input_videos()
    if not inputs:
        raise RuntimeError("Attach a video first (or make one).")
    names = [o.get("op") for o in ops]
    src = inputs[0]
    if "merge" in names and len(inputs) > 1:
        job.step("Joining the videos…")
        merged = job.temp("mp4")
        W, H = probe(inputs[0])[2] or 1280, probe(inputs[0])[3] or 720
        args, parts = [], []
        for i, p in enumerate(inputs):
            args += ["-i", p]
            has_audio = probe(p)[1]
            parts.append(f"[{i}:v]scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30[v{i}]")
            if has_audio:
                parts.append(f"[{i}:a]aresample=44100,aformat=channel_layouts=stereo[a{i}]")
            else:
                dur = probe(p)[0]
                parts.append(f"anullsrc=r=44100:cl=stereo,atrim=0:{dur:.2f}[a{i}]")
        parts.append("".join(f"[v{i}][a{i}]" for i in range(len(inputs))) + f"concat=n={len(inputs)}:v=1:a=1[v][a]")
        _run(args + ["-filter_complex", ";".join(parts), "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "aac", merged])
        src = merged
    dur, has_audio, W, H = probe(src)
    pre, vf, af, extra_inputs, mode = [], [], [], [], None
    start = 0.0
    for op in ops:
        n = op.get("op")
        if n == "trim":
            s, e = float(op.get("start") or 0), op.get("end")
            pre += ["-ss", f"{s:.2f}"] + (["-to", f"{float(e):.2f}"] if e not in (None, "") else [])
            start = s; dur = (float(e) if e not in (None, "") else dur) - s
        elif n == "speed":
            f = max(0.25, min(4.0, float(op.get("factor") or 1)))
            vf.append(f"setpts=PTS/{f}")
            k = f
            while k > 2:
                af.append("atempo=2.0"); k /= 2
            while k < 0.5:
                af.append("atempo=0.5"); k /= 0.5
            af.append(f"atempo={k:.3f}"); dur /= f
        elif n == "reverse":
            vf.append("reverse"); af.append("areverse")
        elif n == "crop":
            r = _ratio(op.get("aspect") or "9:16") or 1
            vf.append(f"crop='if(gt(iw/ih,{r}),ih*{r},iw)':'if(gt(iw/ih,{r}),ih,iw/{r})'")
        elif n == "rotate":
            vf.append({90: "transpose=1", 180: "transpose=1,transpose=1", 270: "transpose=2"}.get(int(op.get("deg") or 90), "transpose=1"))
        elif n == "resize":
            vf.append(f"scale={int(op.get('width') or 720)}:-2")
        elif n == "filter":
            vf.append({"grayscale": "hue=s=0", "sepia": "colorchannelmixer=.393:.769:.189:0:.349:.686:.168:0:.272:.534:.131",
                       "vivid": "eq=saturation=1.4:contrast=1.1", "cinematic": "eq=contrast=1.15:saturation=0.85,vignette",
                       "blur": "boxblur=6", "bright": "eq=brightness=0.08", "dark": "eq=brightness=-0.08"}.get(op.get("name"), "eq=saturation=1.3"))
        elif n == "text" and FONT:
            pos = {"top": "60", "center": "(h-text_h)/2"}.get(op.get("position"), "h-text_h-70")
            s_, e_ = op.get("start"), op.get("end")
            enable = f":enable='between(t,{float(s_ or 0):.2f},{float(e_):.2f})'" if e_ not in (None, "") else ""
            font = FONT.replace("\\", "/").replace(":", "\\:")
            vf.append(f"drawtext=fontfile='{font}':text='{_esc(op.get('text'))}':fontcolor=white:fontsize=h/18:box=1:boxcolor=black@0.45:boxborderw=18"
                      f":x=(w-text_w)/2:y={pos}{enable}")
        elif n == "fade":
            if op.get("in"):
                vf.append(f"fade=t=in:st=0:d={float(op['in']):.2f}"); af.append(f"afade=t=in:st=0:d={float(op['in']):.2f}")
            if op.get("out"):
                o = float(op["out"]); vf.append(f"fade=t=out:st={max(0, dur - o):.2f}:d={o:.2f}"); af.append(f"afade=t=out:st={max(0, dur - o):.2f}:d={o:.2f}")
        elif n == "mute":
            mode = "mute"
        elif n == "volume":
            af.append(f"volume={float(op.get('factor') or 1):.2f}")
        elif n == "narration":
            import nexus
            b64, _ = nexus.speech(op.get("text") or "", op.get("language") or "English")
            if b64:
                ap = job.temp("mp3")
                with open(ap, "wb") as f:
                    f.write(base64.b64decode(b64))
                extra_inputs.append((ap, op.get("mode") or "replace"))
        elif n == "stabilize":
            vf.append("deshake")
        elif n == "gif":
            job.step("Making the GIF…")
            fps, gw = int(op.get("fps") or 10), int(op.get("width") or 400)
            chain = ",".join(vf + [f"fps={fps}", f"scale={gw}:-1:flags=lanczos"])
            path = job.path("gif")
            _run(pre + ["-i", src, "-filter_complex", f"[0:v]{chain},split[a][b];[a]palettegen[p];[b][p]paletteuse", path])
            return path
        elif n == "frame":
            path = job.path("jpg")
            _run(["-ss", f"{float(op.get('time') or 0):.2f}", "-i", src, "-frames:v", "1", "-q:v", "2", path])
            return path
    job.step("Rendering the video…")
    args = pre + ["-i", src]
    for ap, _m in extra_inputs:
        args += ["-i", ap]
    if vf:
        args += ["-vf", ",".join(vf)]
    if mode == "mute":
        args += ["-an"]
    elif extra_inputs:
        ap, m = extra_inputs[-1]
        if m == "mix" and has_audio:
            chain = (",".join(af) + "," if af else "")
            args += ["-filter_complex", f"[0:a]{chain}volume=0.35[o];[1:a]volume=1.0[n];[o][n]amix=inputs=2:duration=first[aout]", "-map", "0:v", "-map", "[aout]"]
        else:
            args += ["-map", "0:v", "-map", "1:a", "-shortest"]
    elif af and has_audio:
        args += ["-af", ",".join(af)]
    path = job.path("mp4")
    _run(args + ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", path])
    return path


EXECUTORS = {"image": make_image, "photo_edit": edit_photo, "chart": make_chart, "diagram": make_diagram, "document": make_document,
             "slides": make_slides, "spreadsheet": make_spreadsheet, "webpage": make_webpage, "svg": make_svg, "qr": make_qr,
             "audio": make_audio, "text": make_text, "video": make_video, "video_edit": edit_video}


# ------------------------------------------------------------------ jobs
class Job:
    def __init__(self, jid, proj, attachments):
        self.id, self.proj, self.attachments = jid, proj, attachments
        self.v = len(proj["versions"]) + 1
        self.temps = []

    def step(self, text):
        with _lock:
            _jobs[self.id]["step"] = text
        try:
            import phone_bridge
            phone_bridge.emit("studio", job=self.id, state="working", step=text, project=self.proj["id"])
        except Exception:
            pass

    def path(self, ext):
        os.makedirs(_pdir(self.proj["id"]), exist_ok=True)
        return os.path.join(_pdir(self.proj["id"]), f"v{self.v}.{ext}")

    def write(self, ext, data):
        p = self.path(ext)
        with open(p, "wb") as f:
            f.write(data)
        return p

    def temp(self, ext):
        os.makedirs(os.path.join(STUDIO_DIR, "_tmp"), exist_ok=True)
        p = os.path.join(STUDIO_DIR, "_tmp", f"{self.id}_{len(self.temps)}.{ext}")
        self.temps.append(p)
        return p

    def current(self):
        if self.proj["versions"]:
            v = self.proj["versions"][-1]
            return os.path.join(_pdir(self.proj["id"]), v["file"])
        return None

    def input_image(self):
        for a in self.attachments:
            if a["kind"] == "image":
                return a["path"]
        cur = self.current()
        if cur and cur.lower().endswith(IMAGE_EXT):
            return cur
        raise RuntimeError("There's no picture to edit - attach a photo or make one first.")

    def input_videos(self):
        vids = [a["path"] for a in self.attachments if a["kind"] == "video"]
        cur = self.current()
        if not vids and cur and cur.lower().endswith(VIDEO_EXT):
            vids = [cur]
        return vids

    def cleanup(self):
        for p in self.temps:
            try:
                os.remove(p)
            except OSError:
                pass


def _thumb(path, proj, v):
    """A small preview picture for the gallery (pictures, charts, diagrams, QR, video first frame)."""
    try:
        from PIL import Image
        src = path
        if path.lower().endswith(VIDEO_EXT):
            src = os.path.join(_pdir(proj["id"]), f"v{v}_frame.jpg")
            _run(["-ss", "0.5", "-i", path, "-frames:v", "1", "-q:v", "3", src], timeout=60)
        elif not path.lower().endswith(IMAGE_EXT):
            return None
        im = Image.open(src); im.thumbnail((480, 480))
        out = os.path.join(_pdir(proj["id"]), f"v{v}_thumb.jpg")
        im.convert("RGB").save(out, quality=82)
        return os.path.basename(out)
    except Exception as e:
        print(f"(studio thumb: {e})")
        return None


def start(request, project_id=None, attachments=None, device=None):
    """Starts a creation / edit in the background. Returns the job id."""
    request = (request or "").strip()[:3000]
    if not request:
        raise ValueError("Say what to make.")
    atts = []
    for uid in attachments or []:
        p = upload_path(uid)
        if p:
            atts.append({"path": p, "kind": "video" if p.lower().endswith(VIDEO_EXT) else "image", "name": os.path.basename(p).split("_", 1)[-1]})
    proj = load(project_id) if project_id else None
    if not proj:
        proj = {"id": secrets.token_hex(5), "title": "New creation", "created": time.time(), "versions": []}
    jid = secrets.token_hex(5)
    with _lock:
        _jobs[jid] = {"id": jid, "state": "working", "step": "Thinking about it…", "project": proj["id"], "t": time.time()}

    def run():
        job = Job(jid, proj, atts)
        try:
            base = proj["versions"][-1] if proj["versions"] else None
            p = plan(request, base, atts)
            kind, spec = p["kind"], p.get("spec") or {}
            job.step({"image": "Painting the picture…", "video": "Directing the video…", "chart": "Drawing the chart…"}.get(kind, "Making it…"))
            if base and kind == "image" and base.get("kind") == "image" and base.get("spec", {}).get("seed") and not spec.get("seed"):
                spec["seed"] = base["spec"]["seed"]
            path = EXECUTORS[kind](spec, job)
            if not proj["versions"]:
                proj["title"] = str(p.get("title") or request[:40])[:60]
            version = {"v": job.v, "kind": kind, "spec": spec, "file": os.path.basename(path), "request": request, "t": time.time(),
                       "message": str(p.get("message") or "Done.")[:300], "attachments": [a["name"] for a in atts]}
            version["thumb"] = _thumb(path, proj, job.v)
            proj["versions"].append(version)
            save(proj)
            result = {"state": "done", "project": proj["id"], "version": job.v, "kind": kind, "file": path, "message": version["message"], "title": proj["title"]}
        except Exception as e:
            print(f"(studio: {e!r})")
            result = {"state": "error", "error": str(e)[:300], "project": proj["id"]}
        finally:
            job.cleanup()
        with _lock:
            _jobs[jid].update(result)
        try:
            import phone_bridge
            phone_bridge.emit("studio", job=jid, **{k: v for k, v in result.items() if k != "file"})
            if device and result["state"] == "done":
                phone_bridge.emit("do", action="tool", tool="studio", arg={"project": proj["id"]}, device=device)
        except Exception:
            pass
    threading.Thread(target=run, daemon=True, name="studio").start()
    return jid


def job(jid):
    with _lock:
        j = dict(_jobs.get(jid) or {})
    j.pop("file", None)
    return j or {"state": "missing"}


def wait(jid, timeout=600):
    t0 = time.time()
    while time.time() - t0 < timeout:
        with _lock:
            j = _jobs.get(jid) or {}
        if j.get("state") in ("done", "error"):
            return dict(j)
        time.sleep(0.5)
    return {"state": "error", "error": "Took too long."}
