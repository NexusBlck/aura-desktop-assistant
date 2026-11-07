# spatial.py - AURA UNIVERSAL 3D / SPATIAL EXPLORER (the PC side).
# "Bring up <anything>" -> UNDERSTAND (what is it, which aspect, which representation) -> SEARCH every source that fits
# (in parallel) -> EVALUATE the candidates honestly (an AI judge rejects unrelated ones) -> FETCH + VALIDATE + CACHE ->
# hand the phone / iPad / PC window a scene to render (mobile/src/spatial/*). Then COMMAND ("show the left ventricle",
# "explode it") and EXPLAIN ("what am I looking at?") with the live spatial context the viewer sends back.
#
# Representation ladder (never faked - every scene says exactly where it came from):
#   L1  a real 3D model that IS the thing          (your library, cache, Sketchfab, Smithsonian, NASA, Wikimedia)
#   L2  another real model of it                    (next-best candidate)
#   L3  a converted model                           (STL / OBJ / PLY / FBX / zipped glTF -> loaded by the viewer)
#   L4  an AI-generated 3D model                    (Hugging Face: TRELLIS / Hunyuan3D - needs the free token)
#   L5  an AI-built structural model                (named parts from primitives - approximate, labelled as such)
#       + exact scientific structures               (PubChem molecules, RCSB proteins / DNA - real coordinates)
#   L6  interactive 2D / external viewer            (a reference picture, Sketchfab's own viewer)
#   L7  an honest explanation of what's unavailable
#
# Security: downloads are size-capped, type-checked by their bytes (not their names), zip entries are sanitised and
# only model / texture files are kept, glTF files may only reference files next to them, nothing downloaded is ever run,
# and API keys stay on this PC.
import concurrent.futures
import io
import json
import math
import os
import re
import secrets
import threading
import time
import urllib.parse
import zipfile

import requests

from ai import ask_llm, extract_json

AURA_DIR = os.path.dirname(os.path.abspath(__file__))
STORE = os.path.join(os.path.expanduser("~"), "Documents", "AURA Spatial")
CACHE = os.path.join(STORE, "cache")
LIBRARY = os.path.join(STORE, "library")          # drop your own .glb / .gltf / .obj / .stl / .ply / .fbx files here
SETTINGS_FILE = os.path.join(AURA_DIR, "aura_spatial.json")
UA = {"User-Agent": "AURA-Spatial/1.0 (personal desktop assistant)"}

MODEL_EXT = (".glb", ".gltf", ".obj", ".stl", ".ply", ".fbx")
TEXTURE_EXT = (".bin", ".png", ".jpg", ".jpeg", ".webp", ".ktx2", ".basis", ".mtl")
MAX_DOWNLOAD = 150 * 1024 * 1024
MAX_UNZIPPED = 400 * 1024 * 1024
MAX_FACES = 1_500_000            # heavier models crush phones; they're skipped when lighter ones exist

LEVELS = {1: "Real 3D model", 2: "Alternative real 3D model", 3: "Converted real 3D model", 4: "AI-generated 3D model",
          5: "AI-built structural model", 6: "Interactive 2D / external view", 7: "Not available"}

_lock = threading.Lock()
_jobs = {}


# ------------------------------------------------------------------ settings (keys stay on the PC)
def settings():
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def set_setting(key, value):
    s = settings()
    s[key] = value
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(s, f)


def sketchfab_token():
    t = settings().get("sketchfab_token") or ""
    if not t:
        import config
        t = getattr(config, "SKETCHFAB_TOKEN", "") or ""
    return t.strip()


def status():
    """Which sources are connected (no secrets)."""
    try:
        import hf
        hf_ready = hf.ready()
    except Exception:
        hf_ready = False
    return {"sketchfab": bool(sketchfab_token()), "hf": hf_ready, "library": LIBRARY,
            "providers": [p.label for p in PROVIDERS]}


# ------------------------------------------------------------------ helpers
def _slug(text, n=60):
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")[:n] or "x"


def _tokens(text):
    stop = {"a", "an", "the", "of", "and", "with", "in", "on", "for", "to", "me", "show", "bring", "up", "model", "3d", "its", "my"}
    return {w for w in re.findall(r"[a-z0-9]+", str(text).lower()) if w not in stop and len(w) > 1}


def _overlap(a, b):
    ta, tb = _tokens(a), _tokens(b)
    return len(ta & tb) / max(1, len(ta)) if ta else 0.0


def _get(url, **kw):
    kw.setdefault("timeout", 25)
    headers = dict(UA)
    headers.update(kw.pop("headers", {}) or {})
    return requests.get(url, headers=headers, **kw)


def download(url, dest, max_bytes=MAX_DOWNLOAD, headers=None):
    """Streams a file to disk, refusing anything bigger than max_bytes."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    h = dict(UA)
    h.update(headers or {})
    with requests.get(url, headers=h, stream=True, timeout=(15, 120)) as r:
        r.raise_for_status()
        if int(r.headers.get("Content-Length") or 0) > max_bytes:
            raise RuntimeError("That model is too big to load safely.")
        got = 0
        with open(dest + ".part", "wb") as f:
            for chunk in r.iter_content(1 << 16):
                got += len(chunk)
                if got > max_bytes:
                    raise RuntimeError("That model is too big to load safely.")
                f.write(chunk)
    os.replace(dest + ".part", dest)
    return dest


def sniff(path):
    """The real type of a model file from its bytes - None if it isn't a model we accept."""
    with open(path, "rb") as f:
        head = f.read(4096)
    low = head.lower()
    if head[:4] == b"glTF":
        return "glb"
    if head[:2] == b"PK":
        return "zip"
    if head[:3] == b"ply":
        return "ply"
    if head.startswith(b"Kaydara FBX Binary") or b"fbxheaderextension" in low:
        return "fbx"
    if head.lstrip()[:1] == b"{" and b'"asset"' in head + open(path, "rb").read(200_000):
        return "gltf"
    if low.lstrip().startswith(b"solid") and b"facet" in low:
        return "stl"
    if re.search(rb"(^|\n)v\s+-?[\d.]", head) and (b"\nf " in head or b"\nvn " in head or b"\nvt " in head or os.path.getsize(path) > 4096):
        return "obj"
    size = os.path.getsize(path)
    if size > 84:                                   # binary STL: 80-byte header + count + 50 bytes per triangle
        n = int.from_bytes(head[80:84], "little")
        if 84 + n * 50 == size:
            return "stl"
    return None


def _safe_gltf(path):
    """A .gltf may only point at files next to it (or embedded data) - never at web addresses or other folders."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    for key in ("buffers", "images"):
        for item in data.get(key) or []:
            uri = str(item.get("uri") or "")
            if not uri or uri.startswith("data:"):
                continue
            u = urllib.parse.unquote(uri)
            if re.match(r"^[a-z][a-z0-9+.-]*:", u, re.I) or u.startswith(("/", "\\")) or ".." in u.replace("\\", "/").split("/"):
                raise RuntimeError("That model points at files outside itself - not loaded.")
    return True


def safe_unzip(zpath, dest):
    """Extracts only model / texture files, with clean relative paths, within a size budget. Returns the main model file."""
    total, best = 0, None
    with zipfile.ZipFile(zpath) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            name = info.filename.replace("\\", "/")
            parts = [p for p in name.split("/") if p not in ("", ".", "..")]
            if not parts or not parts[-1].lower().endswith(MODEL_EXT + TEXTURE_EXT):
                continue
            total += info.file_size
            if total > MAX_UNZIPPED:
                raise RuntimeError("That model unpacks to something far too big.")
            clean = [re.sub(r'[<>:"|?*\x00-\x1f]', "_", p)[:120] for p in parts]
            out = os.path.realpath(os.path.join(dest, *clean))
            if not out.startswith(os.path.realpath(dest) + os.sep):
                continue
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with z.open(info) as src, open(out, "wb") as f:
                while True:
                    b = src.read(1 << 16)
                    if not b:
                        break
                    f.write(b)
            if out.lower().endswith(MODEL_EXT):
                rank = MODEL_EXT.index(os.path.splitext(out)[1].lower())
                if best is None or rank < best[0] or (rank == best[0] and len(out) < len(best[1])):
                    best = (rank, out)
    if not best:
        raise RuntimeError("That download had no 3D model inside.")
    return best[1]


def finalize(folder, path):
    """Checks a fetched file really is a model and returns (format, entry file relative to folder)."""
    kind = sniff(path)
    if kind == "zip":
        inner = safe_unzip(path, folder)
        os.remove(path)
        path, kind = inner, sniff(inner)
    if kind not in ("glb", "gltf", "obj", "stl", "ply", "fbx"):
        raise RuntimeError("The downloaded file isn't a 3D model I can open safely.")
    if kind == "gltf":
        _safe_gltf(path)
    ext = "." + kind
    if not path.lower().endswith(ext):            # name it by what it really is
        new = os.path.splitext(path)[0] + ext
        os.replace(path, new)
        path = new
    return kind, os.path.relpath(path, folder).replace("\\", "/")


def _cache_dir(provider, ident):
    return os.path.join(CACHE, f"{provider}-{_slug(ident, 80)}")


def _cached(provider, ident):
    meta = os.path.join(_cache_dir(provider, ident), "meta.json")
    try:
        with open(meta, encoding="utf-8") as f:
            m = json.load(f)
        if os.path.isfile(os.path.join(_cache_dir(provider, ident), m["entry"])):
            return m
    except Exception:
        pass
    return None


ALLOWED_HOSTS = {"smithsonian": ("3d-api.si.edu", "cdn.3d-api.si.edu"), "nasa": ("raw.githubusercontent.com",),
                 "wikimedia": ("upload.wikimedia.org",), "sketchfab": None}


def _host_ok(provider, url):
    """Downloads only come from the source's own servers (a chosen alternative can't point AURA at anything else)."""
    hosts = ALLOWED_HOSTS.get(provider, ())
    if hosts is None:
        return True                                   # Sketchfab: the address comes from Sketchfab's own API, never from the client
    u = urllib.parse.urlparse(str(url or ""))
    return u.scheme == "https" and u.hostname in hosts and (provider != "nasa" or u.path.startswith("/nasa/NASA-3D-Resources/"))


def _store(provider, ident, url, filename, cand, headers=None):
    """Downloads a model into its own cache folder, validates it and remembers where it came from."""
    hit = _cached(provider, ident)
    if hit:
        return hit
    if not _host_ok(provider, url):
        raise RuntimeError("That download address isn't from the source's own servers - not fetched.")
    folder = _cache_dir(provider, ident)
    os.makedirs(folder, exist_ok=True)
    path = download(url, os.path.join(folder, re.sub(r"[^\w.\-]+", "_", filename)[-80:] or "model"), headers=headers)
    fmt, entry = finalize(folder, path)
    meta = {"provider": provider, "id": ident, "format": fmt, "entry": entry, "title": cand.get("title"), "author": cand.get("author"),
            "license": cand.get("license"), "source_url": cand.get("source_url"), "t": time.time()}
    with open(os.path.join(folder, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f)
    return meta


def _model_scene(meta, cand):
    return {"kind": "model", "format": meta["format"], "dir": _cache_dir(meta["provider"], meta["id"]) if meta["provider"] != "library" else cand["dir"],
            "entry": meta["entry"], "source": _source(cand)}


def _source(c, note=None):
    return {"provider": c.get("provider"), "label": c.get("provider_label") or c.get("provider"), "title": c.get("title"),
            "author": c.get("author"), "license": c.get("license"), "url": c.get("source_url"), "note": note or c.get("note")}


# ------------------------------------------------------------------ 1. UNDERSTAND
UNDERSTAND_PROMPT = """You are the understanding stage of AURA's universal 3D explorer. The user asked to see: "{query}"
{context}
Work out exactly what they want to SEE. Respond ONLY with JSON:
{{"subject": "the thing itself, precise (e.g. 'human heart', 'Pratt & Whitney JT9D turbofan engine', 'BJJ gi')",
 "display_name": "Title Case name to show",
 "category": "anatomy|zoology|botany|microbiology|machine|engine|vehicle|aircraft|spacecraft|electronics|weapon|tool|clothing|product|architecture|building|geography|astronomy|geology|chemistry|molecule|protein|nucleic_acid|artifact|furniture|food|art|mathematics|abstract|fictional|other",
 "aspect": "whole|exterior|internal|cutaway|skeleton|muscles|organs|mechanism|cross_section|exploded",
 "search_terms": ["2-4 short searches a 3D model library would match, most specific first, e.g. 'human heart anatomy', 'heart'"],
 "science": {{"type": "molecule|protein|nucleic_acid|none", "query": "PubChem compound name or protein/DNA search words", "pdb_id": "4-char PDB id if you are sure of a classic one, else ''"}},
 "concrete": true if it is a physical thing that could be photographed as one object,
 "needs_parts": true if they will want to explore named components,
 "expected_parts": ["8-25 real named components/structures of THIS subject in THIS aspect, correct technical/anatomical names"],
 "image_prompt": "a prompt for ONE clean product-style photo of it: single object, centered, whole, plain white background, three-quarter view",
 "parts_search": "a 3D-library search phrase likely to find a version made of SEPARATE parts (e.g. 'human anatomy organs skeleton muscles', 'v8 engine exploded parts', 'jiu jitsu gi jacket pants belt')",
 "has_internals": true if the real thing has internal structure worth seeing inside an outer shell (bodies, animals, organs, engines, devices), false for things that are just their surface (clothing, a sword, a rock),
 "notes": "anything ambiguous in one short line, or ''"}}
Rules: aspect is "whole" unless the user explicitly asks for the inside / internals / skeleton / muscles / a cutaway / how it works inside.
molecules/drugs/compounds -> science.type molecule; proteins, enzymes, antibodies, viruses' capsids -> protein; DNA/RNA structures -> nucleic_acid.
Never invent a different object; if it is fictional or abstract, still describe it so it can be built."""


def understand(query, context=None):
    q = str(query or "").strip()[:500]
    ctx = ""
    if context and context.get("subject"):
        ctx = f"Currently on screen: {context.get('subject')} (selected: {context.get('selection') or 'nothing'}). Use it to resolve words like 'its' or 'this'."
    try:
        u = extract_json(ask_llm([{"role": "user", "content": UNDERSTAND_PROMPT.format(query=q, context=ctx)}],
                                 temperature=0.2, reasoning_effort="low", max_tokens=1800))
        if isinstance(u, dict) and u.get("subject"):
            u.setdefault("display_name", u["subject"].title())
            u["search_terms"] = [str(s) for s in (u.get("search_terms") or [u["subject"]]) if str(s).strip()][:4] or [u["subject"]]
            u["science"] = u.get("science") if isinstance(u.get("science"), dict) else {"type": "none"}
            u["expected_parts"] = [str(p) for p in u.get("expected_parts") or []][:30]
            u["query"] = q
            if not re.search(r"(inside|internal|interior|insides|skeleton|bones?|muscles?|organs?|cut ?away|cross[- ]?section|section|mechanism|"
                             r"how (it|they|does it) works?|exploded|anatomy|structure|parts?|components?|guts|working)", q, re.I):
                u["aspect"] = "whole"            # only what was asked for: a plain "bring up X" gets the real thing first
            return u
    except Exception as e:
        print(f"(spatial understand: {e})")
    subject = re.sub(r"^(please\s+)?(bring up|show me|show|display|visuali[sz]e|open|load|give me|let me see)\s+(a|an|the)?\s*", "", q, flags=re.I).strip(" .?!") or q
    return {"subject": subject, "display_name": subject.title(), "category": "other", "aspect": "whole", "search_terms": [subject],
            "science": {"type": "none"}, "concrete": True, "needs_parts": True, "expected_parts": [], "image_prompt": f"{subject}, single object, white background",
            "notes": "", "query": q, "offline": True}


# ------------------------------------------------------------------ 2. PROVIDERS
class AssetProvider:
    name, label = "base", "Base"
    categories = None             # None = any; else only for these categories

    def applies(self, u):
        return self.categories is None or u.get("category") in self.categories

    def search(self, u):
        return []

    def fetch(self, cand, job):
        raise NotImplementedError


class LocalAssetProvider(AssetProvider):
    """Your own files (Documents/AURA Spatial/library) and everything fetched before (the cache)."""
    name, label = "library", "Your library"

    def search(self, u):
        out = []
        os.makedirs(LIBRARY, exist_ok=True)
        for root, _d, files in os.walk(LIBRARY):
            for fn in files:
                if fn.lower().endswith(MODEL_EXT):
                    title = os.path.splitext(fn)[0].replace("_", " ").replace("-", " ")
                    score = max(_overlap(t, title + " " + os.path.basename(root)) for t in [u["subject"]] + u["search_terms"])
                    if score >= 0.5:
                        out.append({"provider": self.name, "provider_label": self.label, "id": os.path.join(root, fn), "title": title,
                                    "author": "you", "license": "your file", "format": fn.rsplit(".", 1)[-1].lower(), "dir": root,
                                    "entry": fn, "downloadable": True, "pre": score})
        if os.path.isdir(CACHE):
            for d in os.listdir(CACHE):
                meta = os.path.join(CACHE, d, "meta.json")
                if not os.path.isfile(meta):
                    continue
                try:
                    with open(meta, encoding="utf-8") as f:
                        m = json.load(f)
                except Exception:
                    continue
                score = max(_overlap(t, m.get("title") or "") for t in [u["subject"]] + u["search_terms"])
                if score >= 0.6 and m.get("provider") in PROVIDER_BY_NAME:
                    out.append({"provider": m["provider"], "provider_label": PROVIDER_BY_NAME[m["provider"]].label, "id": m["id"],
                                "title": m.get("title"), "author": m.get("author"), "license": m.get("license"), "source_url": m.get("source_url"),
                                "format": m.get("format"), "downloadable": True, "cached": True, "pre": score})
        return out

    def fetch(self, cand, job):
        lib = os.path.realpath(LIBRARY)
        d = os.path.realpath(str(cand.get("dir") or ""))
        f = os.path.realpath(os.path.join(d, str(cand.get("entry") or "")))
        if not (d == lib or d.startswith(lib + os.sep)) or not f.startswith(d + os.sep) or not os.path.isfile(f):
            raise RuntimeError("That file isn't in your Spatial library.")          # only your library folder, nothing else on the PC
        cand = {**cand, "dir": d, "entry": os.path.relpath(f, d).replace("\\", "/")}
        fmt = sniff(os.path.join(cand["dir"], cand["entry"]))
        if fmt not in ("glb", "gltf", "obj", "stl", "ply", "fbx"):
            raise RuntimeError("That library file isn't a model I can open.")
        if fmt == "gltf":
            _safe_gltf(os.path.join(cand["dir"], cand["entry"]))
        return {"kind": "model", "format": fmt, "dir": cand["dir"], "entry": cand["entry"], "source": _source(cand)}


class SketchfabProvider(AssetProvider):
    """The biggest public 3D library. Search is open; downloading needs your free Sketchfab API token
    (without it, models open in Sketchfab's own viewer)."""
    name, label = "sketchfab", "Sketchfab"

    def search(self, u):
        token = sketchfab_token()
        seen, out = set(), []
        terms = u["search_terms"][:2] + ([u["parts_search"]] if u.get("needs_parts") and u.get("parts_search") else [])
        for term in terms:
            params = {"type": "models", "q": term, "count": 12, "archives_flavours": "false"}
            if token:
                params["downloadable"] = "true"
            r = _get("https://api.sketchfab.com/v3/search", params=params)
            if r.status_code != 200:
                continue
            for m in r.json().get("results", []):
                if m["uid"] in seen:
                    continue
                seen.add(m["uid"])
                thumbs = sorted((m.get("thumbnails") or {}).get("images") or [], key=lambda i: abs((i.get("width") or 0) - 400))
                lic = (m.get("license") or {}).get("label") if isinstance(m.get("license"), dict) else m.get("license")
                out.append({"provider": self.name, "provider_label": self.label, "id": m["uid"], "title": m.get("name"),
                            "author": (m.get("user") or {}).get("displayName") or (m.get("user") or {}).get("username"),
                            "license": lic, "source_url": m.get("viewerUrl"), "faces": m.get("faceCount") or 0,
                            "thumb": thumbs[0]["url"] if thumbs else None, "tags": [t.get("name") for t in (m.get("tags") or [])][:8],
                            "downloadable": bool(token and m.get("isDownloadable")), "animated": bool(m.get("animationCount")),
                            "desc": re.sub(r"\s+", " ", m.get("description") or "")[:160]})
        return out

    def fetch(self, cand, job):
        token = sketchfab_token()
        if not re.fullmatch(r"[0-9a-f]{32}", str(cand.get("id") or "")):
            raise RuntimeError("That isn't a Sketchfab model id.")
        if not (token and cand.get("downloadable")):
            return {"kind": "embed", "embed": f"https://sketchfab.com/models/{cand['id']}/embed?autostart=1&ui_theme=dark&transparent=1&ui_infos=0&ui_watermark_link=0",
                    "source": _source(cand, "Shown in Sketchfab's own viewer - connect Sketchfab (free) so AURA can load it and control every part.")}
        hit = _cached(self.name, cand["id"])
        if not hit:
            job.step("Downloading the model…")
            r = _get(f"https://api.sketchfab.com/v3/models/{cand['id']}/download", headers={"Authorization": f"Token {token}"})
            if r.status_code == 401:
                raise RuntimeError("Your Sketchfab token was refused - paste a new one in SPATIAL → Sources.")
            r.raise_for_status()
            d = r.json()
            pick = d.get("glb") or d.get("gltf")
            if not pick or not pick.get("url"):
                raise RuntimeError("Sketchfab didn't offer a glTF download for that model.")
            if (pick.get("size") or 0) > MAX_DOWNLOAD:
                raise RuntimeError("That model is too big to load safely.")
            hit = _store(self.name, cand["id"], pick["url"], "model.glb" if d.get("glb") else "model.zip", cand)
        return _model_scene(hit, cand)


class SmithsonianProvider(AssetProvider):
    """Smithsonian 3D Digitization - real museum scans (fossils, skulls, aircraft, spacecraft, artifacts), no key needed."""
    name, label = "smithsonian", "Smithsonian 3D"

    def search(self, u):
        out, seen = [], set()
        for term in u["search_terms"][:2]:
            r = _get("https://3d-api.si.edu/api/v1.0/content/file/search", params={"q": term, "file_type": "glb", "rows": 60})
            if r.status_code != 200:
                continue
            groups = {}
            for row in r.json().get("rows", []):
                c = row.get("content") or {}
                if c.get("usage") != "Web3D":
                    continue
                groups.setdefault(c.get("model_url"), {"title": row.get("title"), "files": {}})["files"][c.get("quality")] = c.get("uri")
            for pkg, g in groups.items():
                if pkg in seen:
                    continue
                seen.add(pkg)
                uri = g["files"].get("Medium") or g["files"].get("Low") or g["files"].get("High") or g["files"].get("Thumb")
                if uri and uri.rfind("https://") > 0:          # the API sometimes glues a CDN address onto its own
                    uri = uri[uri.rfind("https://"):]
                if uri:
                    out.append({"provider": self.name, "provider_label": self.label, "id": pkg, "title": g["title"], "author": "Smithsonian Institution",
                                "license": "Smithsonian Open Access (check the source for rights)", "source_url": uri, "url": uri, "downloadable": True})
        return out[:12]

    def fetch(self, cand, job):
        job.step("Downloading the museum scan…")
        return _model_scene(_store(self.name, cand["id"], cand.get("url"), "model.glb", cand), cand)


class NasaProvider(AssetProvider):
    """NASA 3D Resources - spacecraft, rovers, telescopes, asteroids, planets' moons (about 600 models)."""
    name, label = "nasa", "NASA 3D Resources"
    categories = {"spacecraft", "astronomy", "aircraft", "vehicle", "machine", "geology", "other", "artifact", "product", "engine"}
    INDEX = os.path.join(CACHE, "nasa_index.json")

    def _index(self):
        try:
            if time.time() - os.path.getmtime(self.INDEX) < 14 * 86400:
                with open(self.INDEX, encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        r = _get("https://api.github.com/repos/nasa/NASA-3D-Resources/git/trees/master?recursive=1", timeout=40)
        r.raise_for_status()
        files = [{"path": t["path"], "size": t.get("size") or 0} for t in r.json().get("tree", [])
                 if t["path"].lower().endswith((".glb", ".stl")) and t["path"].startswith("3D Models/")]
        os.makedirs(CACHE, exist_ok=True)
        with open(self.INDEX, "w", encoding="utf-8") as f:
            json.dump(files, f)
        return files

    def search(self, u):
        out = []
        terms = [u["subject"]] + u["search_terms"]
        for f in self._index():
            folder = f["path"].split("/")[1]
            score = max(_overlap(t, folder) for t in terms)
            if score >= 0.5 and f["size"] < MAX_DOWNLOAD:
                out.append({"provider": self.name, "provider_label": self.label, "id": f["path"], "title": folder, "author": "NASA",
                            "license": "NASA media usage guidelines", "url": "https://raw.githubusercontent.com/nasa/NASA-3D-Resources/master/" + urllib.parse.quote(f["path"]),
                            "source_url": "https://github.com/nasa/NASA-3D-Resources/tree/master/" + urllib.parse.quote(f["path"].rsplit("/", 1)[0]),
                            "format": f["path"].rsplit(".", 1)[-1].lower(), "downloadable": True, "pre": score,
                            "size": f["size"]})
        out.sort(key=lambda c: (-c["pre"], c["format"] != "glb"))
        dedup, seen = [], set()
        for c in out:
            if c["title"] not in seen:
                seen.add(c["title"])
                dedup.append(c)
        return dedup[:8]

    def fetch(self, cand, job):
        job.step("Downloading NASA's model…")
        return _model_scene(_store(self.name, cand["id"], cand.get("url"), os.path.basename(cand["id"]), cand), cand)


class WikimediaProvider(AssetProvider):
    """Wikimedia Commons STL models (scans, anatomy, artifacts, printable objects)."""
    name, label = "wikimedia", "Wikimedia Commons"

    def search(self, u):
        out = []
        r = _get("https://commons.wikimedia.org/w/api.php", timeout=15,
                 params={"action": "query", "generator": "search", "gsrnamespace": 6, "gsrsearch": f"{u['search_terms'][0]} filemime:model/stl",
                         "gsrlimit": 8, "prop": "imageinfo", "iiprop": "url|size|extmetadata", "format": "json"})
        for p in (r.json().get("query") or {}).get("pages", {}).values():
            ii = (p.get("imageinfo") or [{}])[0]
            meta = ii.get("extmetadata") or {}
            if ii.get("url") and (ii.get("size") or 0) < MAX_DOWNLOAD:
                out.append({"provider": self.name, "provider_label": self.label, "id": p["title"], "title": p["title"].replace("File:", "").rsplit(".", 1)[0],
                            "author": re.sub("<[^>]+>", "", (meta.get("Artist") or {}).get("value", ""))[:80] or None,
                            "license": (meta.get("LicenseShortName") or {}).get("value"), "url": ii["url"], "source_url": ii.get("descriptionurl"),
                            "format": "stl", "downloadable": True})
        return out

    def fetch(self, cand, job):
        job.step("Downloading from Wikimedia…")
        return _model_scene(_store(self.name, cand["id"], cand.get("url"), "model.stl", cand), cand)


class ScientificProvider(AssetProvider):
    """Exact structures from real data: PubChem (any molecule by name, 3D conformer) and RCSB PDB (proteins, DNA, RNA, viruses)."""
    name, label = "science", "PubChem / RCSB PDB"

    def applies(self, u):
        return (u.get("science") or {}).get("type") in ("molecule", "protein", "nucleic_acid") or u.get("category") in ("molecule", "protein", "nucleic_acid", "chemistry")

    def search(self, u):
        sci = u.get("science") or {}
        kind = sci.get("type") if sci.get("type") != "none" else ("protein" if u.get("category") == "protein" else "molecule")
        q = sci.get("query") or u["subject"]
        if kind == "molecule":
            return [{"provider": self.name, "provider_label": "PubChem", "id": "pubchem:" + q, "title": q, "kind": "molecule", "query": q,
                     "author": "PubChem (NIH)", "license": "Public domain data", "downloadable": True, "pre": 1.0}]
        ids = []
        if re.fullmatch(r"[0-9][A-Za-z0-9]{3}", str(sci.get("pdb_id") or "")):
            ids.append(sci["pdb_id"].upper())
        body = {"query": {"type": "terminal", "service": "full_text", "parameters": {"value": q}}, "return_type": "entry",
                "request_options": {"paginate": {"start": 0, "rows": 5}}}
        try:
            r = requests.post("https://search.rcsb.org/rcsbsearch/v2/query", json=body, headers=UA, timeout=20)
            ids += [x["identifier"] for x in r.json().get("result_set", [])]
        except Exception:
            pass
        out = []
        for pid in list(dict.fromkeys(ids))[:4]:
            try:
                title = _get(f"https://data.rcsb.org/rest/v1/core/entry/{pid}", timeout=15).json().get("struct", {}).get("title", pid)
            except Exception:
                title = pid
            out.append({"provider": self.name, "provider_label": "RCSB Protein Data Bank", "id": "pdb:" + pid, "pdb": pid, "kind": kind,
                        "title": f"{pid} - {str(title).capitalize()}", "author": "RCSB PDB (wwPDB)", "license": "CC0 data",
                        "source_url": f"https://www.rcsb.org/structure/{pid}", "downloadable": True})
        return out

    def fetch(self, cand, job):
        if cand.get("kind") != "molecule" and not re.fullmatch(r"[0-9][A-Za-z0-9]{3}", str(cand.get("pdb") or "")):
            raise RuntimeError("That isn't a PDB id.")
        if cand.get("kind") == "molecule":
            job.step("Getting the molecule's 3D structure from PubChem…")
            return {"kind": "molecule", "spec": pubchem_molecule(cand["query"]), "source": _source(cand)}
        job.step(f"Getting structure {cand['pdb']} from the Protein Data Bank…")
        return {"kind": "macromolecule", "spec": pdb_structure(cand["pdb"], cand["title"]), "source": _source(cand)}


class GeneratedAssetProvider(AssetProvider):
    """Text -> picture -> 3D model with free Hugging Face spaces (TRELLIS, Hunyuan3D-2). An AI reconstruction - labelled so."""
    name, label = "generated", "AI 3D generation"

    blocked_until = 0.0            # the free GPU allowance ran out: don't spend a minute failing again until it has had time to refill

    def available(self):
        if time.time() < self.blocked_until:
            return False
        try:
            import hf
            return hf.ready()
        except Exception:
            return False

    def _quota(self, e):
        if re.search(r"quota|exceeded|zerogpu", str(e), re.I):
            self.blocked_until = time.time() + 3 * 3600
            return True
        return False

    def fetch(self, cand, job):
        import hf
        from gradio_client import handle_file
        u = cand["understanding"]
        ident = "gen-" + _slug(u["subject"]) + "-" + secrets.token_hex(3)
        folder = _cache_dir(self.name, ident)
        os.makedirs(folder, exist_ok=True)
        errors = []
        job.step("Imagining it as a picture first…")
        img = os.path.join(folder, "reference.png")
        try:
            prompt = (u.get("image_prompt") or f"{u['subject']}, single object") + ", isolated on a plain white background, whole object in frame"
            try:
                with open(hf.generate_image(prompt, 1024, 1024, 0), "rb") as src:
                    data = src.read()
            except Exception:
                import studio
                data = studio._pollinations(prompt, 1024, 1024, 7)
            with open(img, "wb") as f:
                f.write(data)
            job.step("Generating a 3D model from it (TRELLIS, ~1-3 min)…")
            c = hf._client("trellis-community/TRELLIS")
            try:
                c.predict(api_name="/start_session")
            except Exception:
                pass
            res = c.predict(image=handle_file(img), multiimages=[], seed=0, ss_guidance_strength=7.5, ss_sampling_steps=12,
                            slat_guidance_strength=3.0, slat_sampling_steps=12, multiimage_algo="stochastic", mesh_simplify=0.95,
                            texture_size=1024, api_name="/generate_and_extract_glb")
            glb = res[2] or res[1]
            engine = "TRELLIS"
        except Exception as e:
            if self._quota(e):
                raise RuntimeError("Today's free AI 3D generation allowance (Hugging Face GPU) is used up - it refills over the day.")
            errors.append(f"TRELLIS: {hf._friendly(e) if hasattr(hf, '_friendly') else e}")
            job.step("Trying another generator (Hunyuan3D-2)…")
            try:
                if not os.path.isfile(img):
                    raise RuntimeError("no reference picture")
                res = hf._client("tencent/Hunyuan3D-2").predict(caption=None, image=handle_file(img), mv_image_front=None,
                                                                  mv_image_back=None, mv_image_left=None, mv_image_right=None, steps=30,
                                                                  guidance_scale=5.0, seed=1234, octree_resolution=256, check_box_rembg=True,
                                                                  num_chunks=8000, randomize_seed=True, api_name="/shape_generation")
                glb = res[0]
                engine = "Hunyuan3D-2 (shape only, no colours)"
            except Exception as e2:
                if self._quota(e2):
                    raise RuntimeError("Today's free AI 3D generation allowance (Hugging Face GPU) is used up - it refills over the day.")
                errors.append(f"Hunyuan3D: {hf._friendly(e2) if hasattr(hf, '_friendly') else e2}")
                raise RuntimeError("AI 3D generation isn't available right now - " + "; ".join(errors)[:300])
        path = glb if isinstance(glb, str) else (glb or {}).get("path") if isinstance(glb, dict) else None
        if not path or not os.path.isfile(path):
            raise RuntimeError("The AI generator returned no model.")
        dest = os.path.join(folder, "model" + os.path.splitext(path)[1].lower())
        with open(path, "rb") as src, open(dest, "wb") as f:
            f.write(src.read())
        fmt, entry = finalize(folder, dest)
        meta = {"provider": self.name, "id": ident, "format": fmt, "entry": entry, "title": f"{u['display_name']} (AI-generated)",
                "author": engine, "license": "AI-generated", "t": time.time()}
        with open(os.path.join(folder, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f)
        c2 = {"provider": self.name, "provider_label": f"AI-generated · {engine}", "title": meta["title"], "author": engine, "license": "AI-generated",
              "note": "Generated by AI from a description - a reconstruction, not a real scan. One solid piece (no separate parts)."}
        return {"kind": "model", "format": fmt, "dir": folder, "entry": entry, "source": _source(c2)}


class ProceduralProvider(AssetProvider):
    """AURA's own AI builds the thing from named 3D primitives - always available, every part selectable, approximate."""
    name, label = "procedural", "AI-built structural model"

    def fetch(self, cand, job):
        job.step("Building a structural model part by part…")
        spec = procedural_spec(cand["understanding"])
        c = {"provider": self.name, "provider_label": "AI-built structural model", "title": spec.get("title"), "author": "AURA",
             "license": "made for you", "note": "Built by AURA's AI from named primitives - a simplified, approximate representation, not a real scan or CAD model."}
        return {"kind": "procedural", "spec": spec, "source": _source(c)}


class ImageProvider(AssetProvider):
    """Level 6: a real reference picture (Wikipedia / Wikimedia) shown in the spatial view with its parts listed."""
    name, label = "image", "Reference picture"

    def fetch(self, cand, job):
        u = cand["understanding"]
        job.step("Finding a reference picture…")
        page = wiki_page(u["subject"])
        if not page or not page.get("image"):
            raise RuntimeError("No reference picture found.")
        folder = _cache_dir(self.name, _slug(page["title"]))
        os.makedirs(folder, exist_ok=True)
        ext = os.path.splitext(urllib.parse.urlparse(page["image"]).path)[1].lower()
        if ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
            raise RuntimeError("No usable reference picture.")
        path = download(page["image"], os.path.join(folder, "picture" + ext), max_bytes=25 * 1024 * 1024)
        c = {"provider": self.name, "provider_label": "Wikipedia picture", "title": page["title"], "author": "Wikipedia / Wikimedia Commons",
             "license": "see source", "source_url": page["url"], "note": "No suitable 3D model was found - this is a real 2D picture."}
        return {"kind": "image", "dir": folder, "entry": os.path.basename(path), "caption": page.get("extract", "")[:400], "source": _source(c)}


PROVIDERS = [LocalAssetProvider(), SketchfabProvider(), SmithsonianProvider(), NasaProvider(), WikimediaProvider(), ScientificProvider()]
PROVIDER_BY_NAME = {p.name: p for p in PROVIDERS}
GENERATED, PROCEDURAL, IMAGE = GeneratedAssetProvider(), ProceduralProvider(), ImageProvider()
for _p in (GENERATED, PROCEDURAL, IMAGE):
    PROVIDER_BY_NAME[_p.name] = _p


# ------------------------------------------------------------------ 3. EVALUATE
def gather(u, job=None):
    """Asks every applicable provider at once; a slow or broken source never blocks the others."""
    active = [p for p in PROVIDERS if p.applies(u)]
    found, errors = [], {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(active) or 1) as pool:
        futs = {pool.submit(p.search, u): p for p in active}
        for fut in concurrent.futures.as_completed(futs, timeout=60):
            p = futs[fut]
            try:
                found += fut.result() or []
            except Exception as e:
                errors[p.name] = str(e)[:120]
    if errors:
        print(f"(spatial sources unavailable: {errors})")
    return found, errors


def rank(u, cands):
    """An AI judge scores how well each candidate IS what the user asked for (unrelated things score 0), plus quality signals."""
    if not cands:
        return []
    sci = [c for c in cands if c["provider"] == "science" and c.get("kind") == "molecule"]       # PubChem: looked up by exact name
    order = {"library": 0, "science": 1, "smithsonian": 2, "nasa": 3, "wikimedia": 4, "sketchfab": 5}
    rest = sorted((c for c in cands if c not in sci), key=lambda c: order.get(c["provider"], 9))[:36]   # specialist sources are never crowded out
    if rest:
        lines = "\n".join(f"{i}. [{c['provider_label']}] {c.get('title')} | by {c.get('author') or '?'}"
                          f"{' | tags: ' + ', '.join(t for t in c.get('tags') or [] if t) if c.get('tags') else ''}"
                          f"{' | ' + c['desc'] if c.get('desc') else ''}" for i, c in enumerate(rest))
        prompt = (f"The user wants to see: \"{u['query']}\" -> subject: {u['subject']} (aspect: {u.get('aspect')}).\n"
                  f"Candidate 3D models:\n{lines}\n\nScore EACH candidate 0-10 for how well it IS the requested subject in the requested aspect "
                  "(10 = exactly it; 6 = clearly the same thing but a different variant/aspect; 3 = related but a different object; "
                  "0 = unrelated, a joke, a scene, a toy/cartoon when realism is expected, or only a part of it). Be strict: "
                  "never give 6+ to a different object. Respond ONLY with JSON {\"scores\": [{\"i\": 0, \"s\": 7}]}")
        try:
            data = extract_json(ask_llm([{"role": "user", "content": prompt}], temperature=0, reasoning_effort="low", max_tokens=1500))
            for item in (data or {}).get("scores", []):
                i = int(item.get("i", -1))
                if 0 <= i < len(rest):
                    rest[i]["score"] = float(item.get("s") or 0)
        except Exception as e:
            print(f"(spatial rank: {e})")
            for c in rest:                                  # no judge available: plain word overlap, kept conservative
                c["score"] = 7.0 * max(_overlap(u["subject"], c.get("title") or ""), c.get("pre", 0))
    for c in rest:
        s = c.get("score", 0)
        if c.get("cached") or c["provider"] == "library":
            s += 0.5
        if c["provider"] in ("smithsonian", "nasa", "science"):
            s += 0.3                                        # real scans / official models / measured structures
        if c["provider"] == "science" and c.get("pdb") and c["pdb"] == str((u.get("science") or {}).get("pdb_id") or "").upper():
            s += 0.5                                        # the classic structure for this subject
        if c.get("faces", 0) > MAX_FACES:
            s -= 2
        elif c.get("faces", 0) > 500_000:
            s -= 1.0                                        # heavy: slow to download and to render on a phone
        elif 2_000 < c.get("faces", 0) < 300_000:
            s += 0.5
        if c["provider"] == "sketchfab" and not c.get("downloadable"):
            s -= 1.5                                        # only viewable in Sketchfab's player
        if u.get("needs_parts") and re.search(r"\b(parts?|anatomy|anatomical|organs?|skeleton|systems?|exploded|cutaway|assembly|components?|separated|layers?|detailed|rigged)\b",
                                             f"{c.get('title')} {' '.join(t or '' for t in c.get('tags') or [])}", re.I):
            s += 0.8                                        # likely built from separate pieces
        c["score"] = round(s, 2)
    for c in sci:
        c["score"] = 9.5
    return sorted(sci + rest, key=lambda c: -c.get("score", 0))


def _prefers_structure(u):
    return u.get("aspect") in ("internal", "cutaway", "mechanism", "cross_section", "exploded", "skeleton", "muscles", "organs") or \
        u.get("category") in ("mathematics", "abstract", "fictional")


# ------------------------------------------------------------------ 4. RESOLVE (the job)
class Job:
    def __init__(self, jid):
        self.id = jid
        self.cancelled = False

    def step(self, text):
        with _lock:
            j = _jobs.get(self.id)
            if j:
                j["step"] = text
                j["steps"].append(text)
        if self.cancelled:
            raise RuntimeError("Cancelled.")


def resolve(query, job, context=None, choice=None, prefer=None):
    """The whole pipeline. Returns a scene dict (never raises for 'not found' - that's level 7)."""
    job.step("Understanding the request…")
    u = choice.get("understanding") if choice and choice.get("understanding") else understand(query, context)
    job.step(f"Looking for {u['display_name']}…")
    alternatives, tried = [], []

    def done(rep, level, cand=None):
        scene = {"id": secrets.token_hex(5), "query": u["query"], "subject": u["subject"], "display_name": u["display_name"],
                 "category": u.get("category"), "aspect": u.get("aspect"), "level": level, "level_label": LEVELS[level],
                 "expected_parts": u.get("expected_parts") or [], "understanding": u, "t": time.time(), **rep}
        scene["alternatives"] = [a for a in alternatives if not cand or a.get("key") != _key(cand)][:10]
        scene["tried"] = tried
        scene["message"] = _message(scene)
        return scene

    def attempt(provider, cand, level):
        try:
            rep = provider.fetch(cand, job)
            return done(rep, level, cand)
        except Exception as e:
            if job.cancelled:
                raise
            tried.append({"source": provider.label, "title": cand.get("title"), "error": str(e)[:200]})
            print(f"(spatial: {provider.label} failed: {e})")
            return None

    meta = {"understanding": u}
    if choice:                                               # the user picked an alternative
        prov = PROVIDER_BY_NAME.get(choice.get("provider"))
        if prov:
            c = {**choice, **meta}
            level = {"generated": 4, "procedural": 5, "image": 6}.get(prov.name, 1)
            if prov.name == "sketchfab" and not (sketchfab_token() and c.get("downloadable")):
                level = 6
            r = attempt(prov, c, level)
            if r:
                return r

    job.step("Searching available representations…")
    cands, _errs = gather(u, job) if not choice else ([], {})
    job.step("Evaluating candidates…" if cands else "No ready-made models found - preparing another way…")
    ranked = rank(u, cands)
    good = [c for c in ranked if c.get("score", 0) >= 6]
    for c in ranked:
        if c.get("score", 0) >= 5 and len(alternatives) < 8:
            alternatives.append(_alt(c))
    gen_ok = GENERATED.available() and u.get("concrete", True)
    if not any(a["provider"] == "procedural" for a in alternatives):
        alternatives.append({"key": "procedural", "provider": "procedural", "title": "AI-built structural model (every part selectable)", "label": "AI-BUILT"})
    if gen_ok:
        alternatives.append({"key": "generated", "provider": "generated", "title": "Generate a new 3D model with AI (1-3 min)", "label": "GENERATE"})

    real = [c for c in good if c["provider"] != "sketchfab" or c.get("downloadable")]
    embeds = [c for c in good if c["provider"] == "sketchfab" and not c.get("downloadable")]
    order = []
    if prefer == "procedural" or (_prefers_structure(u) and not any(c["provider"] == "science" for c in real)):
        order.append(("procedural", None))
    for i, c in enumerate(real[:3]):
        order.append((c["provider"], c))
    if gen_ok and prefer != "procedural":
        order.append(("generated", None))
    order.append(("procedural", None))
    order += [("sketchfab", c) for c in embeds[:1]]
    order.append(("image", None))

    if u.get("needs_parts") and len(real) > 1:
        order = _best_by_parts(u, real, order, attempt, job, done, alternatives, tried) or order

    done_names = set()
    for name, c in order:
        if c is None and name in done_names:
            continue
        done_names.add(name)
        prov = PROVIDER_BY_NAME[name]
        if c is None:
            c = {"provider": name, "title": u["display_name"]}
            level = {"generated": 4, "procedural": 5, "image": 6}[name]
        else:
            level = 6 if name == "sketchfab" and not c.get("downloadable") else (1 if c is real[0] else 2)
            if level < 6 and (c.get("format") in ("stl", "obj", "ply", "fbx")):
                level = 3
        r = attempt(prov, {**c, **meta}, level)
        if r:
            return r
    return done({"kind": "none", "source": None,
                 "unavailable": "I couldn't find, generate or build a 3D representation of this right now."}, 7)


def _key(c):
    return f"{c.get('provider')}:{c.get('id') or ''}"


def _alt(c):
    a = {k: c.get(k) for k in ("provider", "id", "title", "author", "license", "source_url", "thumb", "format", "url", "downloadable",
                                "kind", "query", "pdb", "dir", "entry", "provider_label", "faces")}
    a["key"] = _key(c)
    a["label"] = (c.get("provider_label") or c["provider"]).upper()
    a["score"] = c.get("score")
    return {k: v for k, v in a.items() if v is not None}


def _message(scene):
    s, name = scene.get("source") or {}, scene["display_name"]
    if scene["level"] == 7:
        return f"I couldn't bring up {name} - no source had it and building it failed. " + "; ".join(t["error"] for t in scene["tried"][:2])
    if scene["kind"] == "procedural":
        return f"Here's {name}, built by me as a structural model - approximate, but every part is selectable."
    if scene["kind"] in ("molecule", "macromolecule"):
        return f"Here's {name} - real structure data from {s.get('label')}."
    if scene["kind"] == "embed":
        return f"Here's {name} in Sketchfab's viewer. Connect Sketchfab (free) so I can load it fully and control its parts."
    if scene["kind"] == "image":
        return f"I couldn't find a 3D model of {name}, so here's a real picture of it."
    if scene["level"] == 4:
        return f"Here's {name}, generated by AI - a reconstruction, not a real scan."
    return f"Here's {name} - {s.get('title')}{', by ' + s['author'] if s.get('author') else ''} ({s.get('label')})."


def start(query, device=None, context=None, choice=None, prefer=None):
    query = str(query or "").strip()[:500]
    if not query and not choice:
        raise ValueError("Say what to bring up.")
    jid = secrets.token_hex(5)
    with _lock:
        for k in [k for k, v in _jobs.items() if time.time() - v["t"] > 3600]:
            _jobs.pop(k, None)
        _jobs[jid] = {"id": jid, "state": "working", "step": "Understanding the request…", "steps": [], "t": time.time(), "query": query}
    job = Job(jid)
    with _lock:
        _jobs[jid]["_job"] = job

    def run():
        try:
            scene = resolve(query, job, context, choice, prefer)
            result = {"state": "done", "scene": scene}
            _remember(scene)
        except Exception as e:
            print(f"(spatial: {e!r})")
            result = {"state": "cancelled" if job.cancelled else "error", "error": str(e)[:300]}
        with _lock:
            if jid in _jobs:
                _jobs[jid].update(result)
    threading.Thread(target=run, daemon=True, name="spatial").start()
    return jid


def job_status(jid):
    with _lock:
        j = _jobs.get(jid)
        return {k: v for k, v in j.items() if k != "_job"} if j else {"state": "missing"}


def cancel(jid):
    with _lock:
        j = _jobs.get(jid)
        if j and j.get("_job"):
            j["_job"].cancelled = True
            j["state"] = "cancelled"
            return True
    return False


def wait(jid, timeout=600):
    end = time.time() + timeout
    while time.time() < end:
        j = job_status(jid)
        if j.get("state") not in ("working",):
            return j
        time.sleep(0.4)
    return {"state": "error", "error": "Took too long."}


HISTORY = os.path.join(STORE, "history.json")


def _remember(scene):
    try:
        os.makedirs(STORE, exist_ok=True)
        try:
            with open(HISTORY, encoding="utf-8") as f:
                hist = json.load(f)
        except Exception:
            hist = []
        hist = [h for h in hist if h.get("query") != scene["query"]]
        hist.insert(0, {"query": scene["query"], "name": scene["display_name"], "kind": scene["kind"], "level": scene["level"], "t": scene["t"]})
        with open(HISTORY, "w", encoding="utf-8") as f:
            json.dump(hist[:60], f)
    except Exception:
        pass


def history():
    try:
        with open(HISTORY, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


# ------------------------------------------------------------------ scientific data
_COV = {"H": 0.31, "C": 0.76, "N": 0.71, "O": 0.66, "S": 1.05, "P": 1.07, "F": 0.57, "Cl": 1.02, "Br": 1.2, "I": 1.39}


def pubchem_molecule(name):
    base = "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/" + urllib.parse.quote(name)
    r = _get(base + "/SDF", params={"record_type": "3d"})
    is3d = r.status_code == 200
    if not is3d:
        r = _get(base + "/SDF")
        if r.status_code != 200:
            raise RuntimeError(f"PubChem doesn't know a compound called '{name}'.")
    atoms, bonds = parse_sdf(r.text)
    props = {}
    try:
        props = _get(base + "/property/MolecularFormula,MolecularWeight,IUPACName,Title/JSON", timeout=15).json()["PropertyTable"]["Properties"][0]
    except Exception:
        pass
    return {"name": props.get("Title") or name, "formula": props.get("MolecularFormula"), "weight": props.get("MolecularWeight"),
            "iupac": props.get("IUPACName"), "cid": props.get("CID"), "atoms": atoms, "bonds": bonds, "is3d": is3d,
            "url": f"https://pubchem.ncbi.nlm.nih.gov/compound/{props.get('CID')}" if props.get("CID") else None}


def parse_sdf(text):
    lines = text.splitlines()
    na, nb = int(lines[3][0:3]), int(lines[3][3:6])
    atoms = []
    for ln in lines[4:4 + na]:
        p = ln.split()
        atoms.append([p[3], round(float(p[0]), 3), round(float(p[1]), 3), round(float(p[2]), 3)])
    bonds = []
    for ln in lines[4 + na:4 + na + nb]:
        bonds.append([int(ln[0:3]) - 1, int(ln[3:6]) - 1, int(ln[6:9])])
    if na > 4000:
        raise RuntimeError("That molecule is too large to draw atom by atom.")
    return atoms, bonds


_NUC = {"DA", "DT", "DG", "DC", "DU", "A", "U", "G", "C", "I", "DI"}


def pdb_structure(pid, title):
    r = _get(f"https://files.rcsb.org/download/{pid}.pdb", timeout=40)
    if r.status_code != 200:
        raise RuntimeError(f"Structure {pid} isn't available in PDB format (too large) - try another.")
    chains, ligands, seen_model = {}, {}, False
    residues = {}                                   # nucleic acids: (chain, residue) -> sugar C1' + base ring atoms
    for ln in r.text.splitlines():
        rec = ln[:6]
        if rec.startswith("MODEL"):
            if seen_model:
                break
            seen_model = True
        if rec.startswith("ENDMDL"):
            break
        if rec not in ("ATOM  ", "HETATM"):
            continue
        name, res, ch = ln[12:16].strip(), ln[17:20].strip(), ln[21].strip() or "A"
        try:
            xyz = [round(float(ln[30:38]), 2), round(float(ln[38:46]), 2), round(float(ln[46:54]), 2)]
        except ValueError:
            continue
        el = (ln[76:78].strip() or name[:1]).capitalize()
        if rec == "ATOM  ":
            c = chains.setdefault(ch, {"id": ch, "kind": "protein", "trace": [], "atoms": []})
            if name == "CA" and res not in _NUC:
                c["trace"].append(xyz)
            elif name == "P" or (name == "C4'" and res in _NUC and not c["trace"]):
                if res in _NUC:
                    c["kind"] = "rna" if res in ("A", "U", "G", "C") else "dna"
                    if name == "P":
                        c["trace"].append(xyz)
            if len(c["atoms"]) < 6000:
                c["atoms"].append([el] + xyz)
            if res in _NUC:
                r = residues.setdefault((ch, ln[22:27]), {"res": res, "c1": None, "ring": []})
                if name == "C1'":
                    r["c1"] = xyz
                elif name in ("N1", "C2", "N3", "C4", "C5", "C6", "N7", "C8", "N9"):
                    r["ring"].append(xyz)
        elif res != "HOH":
            key = f"{res} {ch}{ln[22:26].strip()}"
            lig = ligands.setdefault(key, {"name": res, "id": key, "atoms": []})
            if len(lig["atoms"]) < 300:
                lig["atoms"].append([el] + xyz)
    for (ch, _num), r in residues.items():           # one rung per nucleotide: from its sugar to the middle of its base
        if r["c1"] and len(r["ring"]) >= 4 and ch in chains:
            mid = [round(sum(a[i] for a in r["ring"]) / len(r["ring"]), 2) for i in range(3)]
            chains[ch].setdefault("bases", []).append(r["c1"] + mid + [r["res"][-1]])
    out_chains = [c for c in chains.values() if len(c["trace"]) >= 2]
    if not out_chains:
        raise RuntimeError("Couldn't read that structure.")
    small = sum(len(c["atoms"]) for c in out_chains) <= 12000
    for c in out_chains:
        if not small:
            c.pop("atoms")
    return {"pdb": pid, "title": title, "chains": out_chains, "ligands": list(ligands.values())[:40], "url": f"https://www.rcsb.org/structure/{pid}"}


# ------------------------------------------------------------------ AI-built structural models
PROCEDURAL_PROMPT = """You are AURA's spatial modeller. Build a 3D STRUCTURAL MODEL of: {subject} (aspect: {aspect}; category: {category}).
Real components to include where relevant: {parts}

Coordinates: Y is up, units are centimetres at REAL proportions, the whole object centred on the origin. Respond ONLY with JSON:
{{"title": "...", "units": "cm", "real_size": "approximate real size in words",
 "parts": [{{"name": "precise technical / anatomical name", "group": "system or assembly it belongs to",
   "shape": "box|sphere|ellipsoid|cylinder|cone|torus|capsule|lathe|tube|extrude|parametric",
   "dims": {{...see below...}}, "pos": [x,y,z], "rot": [degX,degY,degZ],
   "color": "#rrggbb", "material": "metal|plastic|glass|rubber|fabric|tissue|bone|wood|stone|organic|emissive|ceramic",
   "opacity": 1, "desc": "one line: what it is / does",
   "copies": [{{"name": "...", "pos": [x,y,z], "rot": [..]}}] (optional: identical repeats such as wheels, pistons, ribs)}}],
 "animations": [{{"name": "e.g. beating / fan rotation / piston motion", "parts": ["part names"], "type": "rotate|oscillate|pulse|orbit",
   "axis": [0,1,0], "speed": 1.0, "amount": 10}}],
 "facts_note": "one line on what is simplified"}}
dims by shape: box {{w,h,d}} | sphere {{r}} | ellipsoid {{rx,ry,rz}} | cylinder {{rt,rb,h}} | cone {{r,h}} | torus {{R,r,arc}} (arc degrees) |
capsule {{r,h}} | lathe {{points: [[radius,y],...] profile rotated around Y}} | tube {{points: [[x,y,z],...], r}} (vessels, wires, pipes, tendons) |
extrude {{shape: [[x,y],...], depth}} | parametric {{x: "expr", y: "expr", z: "expr", u: [a,b], v: [a,b]}} (expr uses u v pi sin cos tan sqrt abs exp log pow min max).
Rules: 15-70 parts. Correct relative positions, sizes and names - this is used for learning. Inner structures inside outer ones
(give outer shells opacity 0.35-0.6 so the inside shows). Organic shapes: ellipsoids, lathes and tubes; machines: cylinders, boxes, tori.
Only add animations that reflect real motion (heart beating, fan spinning, pistons). Never add parts that don't exist.
If the aspect is "internal": ONLY the internal structures - no outer skin, shell, casing, outline or silhouette (it sits inside a real model).
MATHEMATICAL objects (Klein bottle, Möbius strip, torus knot, hyperboloid, seashell, minimal surfaces...): use parametric parts with the
real standard equations (e.g. Klein bottle 'figure-8' immersion), scaled to ~20 cm, plus optional named axes/annotations as thin tubes."""

_SHAPES = {"box", "sphere", "ellipsoid", "cylinder", "cone", "torus", "capsule", "lathe", "tube", "extrude", "parametric"}


def procedural_spec(u):
    prompt = PROCEDURAL_PROMPT.format(subject=u["subject"], aspect=u.get("aspect") or "whole", category=u.get("category") or "other",
                                      parts=", ".join(u.get("expected_parts") or []) or "decide from your knowledge")
    raw = ask_llm([{"role": "user", "content": prompt}], temperature=0.3, reasoning_effort="medium", max_tokens=14000)
    spec = extract_json(raw)
    if not isinstance(spec, dict) or not isinstance(spec.get("parts"), list):
        raise RuntimeError("The structural model came back unreadable.")
    return clean_spec(spec, u)


def _num(v, default=0.0, lo=-1e5, hi=1e5):
    try:
        f = float(v)
        return default if math.isnan(f) else max(lo, min(hi, f))
    except (TypeError, ValueError):
        return default


def _vec(v, n=3):
    v = v if isinstance(v, (list, tuple)) else []
    return [_num(v[i] if i < len(v) else 0) for i in range(n)]


def _pretty(name):
    """'left_lung' -> 'Left Lung'; keeps names that already have capitals (e.g. 'L4 Vertebra')."""
    t = re.sub(r"[_]+", " ", str(name or "Part")).strip()[:80] or "Part"
    return t if re.search(r"[A-Z]", t) else t.title()


def clean_spec(spec, u=None):
    """Keeps only well-formed parts with sane numbers (the viewer builds geometry from these - nothing is executed)."""
    parts, names = [], set()
    for p in spec.get("parts", [])[:120]:
        if not isinstance(p, dict) or p.get("shape") not in _SHAPES:
            continue
        name = _pretty(p.get("name") or p["shape"])
        base, n = name, 2
        while name.lower() in names:
            name, n = f"{base} {n}", n + 1
        names.add(name.lower())
        dims = {k: v for k, v in (p.get("dims") or {}).items() if k in ("w", "h", "d", "r", "rx", "ry", "rz", "rt", "rb", "R", "arc", "depth", "x", "y", "z", "u", "v", "points", "shape")}
        for k in ("x", "y", "z"):
            if k in dims:
                dims[k] = str(dims[k])[:200]
        for k in ("points", "shape"):
            if k in dims:
                dims[k] = [[_num(c) for c in pt[:3]] for pt in (dims[k] if isinstance(dims[k], list) else []) if isinstance(pt, (list, tuple))][:400]
        for k in ("u", "v"):
            if k in dims:
                dims[k] = _vec(dims[k], 2)
        for k in ("w", "h", "d", "r", "rx", "ry", "rz", "rt", "rb", "R", "arc", "depth"):
            if k in dims:
                dims[k] = _num(dims[k], 1, 0, 1e5)
        q = {"name": name, "group": str(p.get("group") or "")[:60], "shape": p["shape"], "dims": dims, "pos": _vec(p.get("pos")),
             "rot": _vec(p.get("rot")), "color": p.get("color") if re.fullmatch(r"#[0-9a-fA-F]{6}", str(p.get("color") or "")) else "#9ec9d9",
             "material": str(p.get("material") or "plastic")[:20], "opacity": _num(p.get("opacity"), 1, 0.05, 1), "desc": str(p.get("desc") or "")[:200]}
        copies = []
        for ci, c in enumerate((p.get("copies") or [])[:40]):
            if isinstance(c, dict):
                cn = _pretty(c.get("name") or f"{name} {ci + 2}")
                k = 2
                while cn.lower() in names:
                    cn = f"{_pretty(c.get('name') or name)} {k}"
                    k += 1
                names.add(cn.lower())
                copies.append({"name": cn, "pos": _vec(c.get("pos")), "rot": _vec(c.get("rot") or p.get("rot"))})
        if copies:
            q["copies"] = copies
        parts.append(q)
    if not parts:
        raise RuntimeError("The structural model had no usable parts.")
    anims = []
    for a in (spec.get("animations") or [])[:6]:
        if isinstance(a, dict) and a.get("type") in ("rotate", "oscillate", "pulse", "orbit"):
            anims.append({"name": str(a.get("name") or a["type"])[:60], "parts": [str(x)[:80] for x in a.get("parts") or []][:60],
                          "type": a["type"], "axis": _vec(a.get("axis") or [0, 1, 0]), "speed": _num(a.get("speed"), 1, 0.05, 20),
                          "amount": _num(a.get("amount"), 10, 0, 1000)})
    return {"title": str(spec.get("title") or (u or {}).get("display_name") or "Model")[:80], "units": "cm",
            "real_size": str(spec.get("real_size") or "")[:80], "parts": parts, "animations": anims,
            "facts_note": str(spec.get("facts_note") or "")[:200]}


# ------------------------------------------------------------------ 5. COMMANDS + EXPLANATIONS (with the live spatial context)
COMMAND_PROMPT = """You control AURA's 3D spatial viewer. Turn what the user said into viewer operations.
SPATIAL CONTEXT (what is on screen now):
{context}
User said: "{text}"

Operations (use the exact part names from the context; "it"/"this" = the current selection, else the whole object):
 {{"op":"load","query":"<thing to bring up>","mode":"replace|add"}}  (add = show side by side to compare)
 {{"op":"select","targets":[names]}}  {{"op":"highlight","targets":[names],"color":"#hex"}}  {{"op":"unhighlight"}}
 {{"op":"hide","targets":[names]}}  {{"op":"show","targets":[names]}}  {{"op":"isolate","targets":[names]}}  {{"op":"showall"}}
 {{"op":"opacity","targets":[names] or [] for all,"value":0.0-1.0}}  {{"op":"xray","on":true}}  {{"op":"wireframe","on":true}}
 {{"op":"color","targets":[names],"color":"#hex"}}  {{"op":"explode","amount":0.0-1.0}}  {{"op":"section","axis":"x|y|z","value":-1..1 or null to remove}}
 {{"op":"rotate","axis":"x|y|z","degrees":90}}  {{"op":"spin","on":true}}  {{"op":"view","preset":"front|back|left|right|top|bottom|iso"}}
 {{"op":"zoom","factor":1.5}}  {{"op":"focus","target":"name"}}  {{"op":"reset"}}  {{"op":"animate","name":"animation name or auto","on":true}}
 {{"op":"label","on":true}}  {{"op":"annotate","target":"name","text":"..."}}  {{"op":"measure","a":"name","b":"name"}}
 {{"op":"duplicate","target":"name"}}  {{"op":"explain","target":"name or null"}}  {{"op":"switch","to":"procedural|generated|next"}}
 {{"op":"remove","object":index}}  {{"op":"fullscreen"}}
 {{"op":"undo"}}  {{"op":"restore"}} (everything back to normal)  {{"op":"unisolate"}}  {{"op":"show","targets":[]}} (= unhide everything)
 {{"op":"disassemble","step":true}} (take it apart piece by piece)  {{"op":"reassemble","targets":[names] or omit for all,"step":false}}
 {{"op":"explode","amount":0.7,"targets":[names]}} (targets optional: explode only those)  {{"op":"move","targets":[names],"direction":"out|up|down|left|right|toward|away","amount":0.2-1}}
 {{"op":"hologram","on":true}}  {{"op":"inspect","target":"name"}} (float it, ghost the rest, orbit it)  {{"op":"identify"}} (AI looks at the pieces and names them)
Rules: every change can be reversed - "unhide", "bring it back", "undo", "put it back together", "exit isolate" map to show / undo / reassemble / unisolate.
Pieces called "Piece N" / "Section N" are unnamed geometry: for "what are these pieces" use identify. "Bring back the X" = show X.
"show me X" where X is a PART of the current object -> select + focus (+ isolate or xray if it is hidden inside). "show me the inside"
-> section or xray. "behind it" -> make the selection transparent and rotate 180 around y. A different object -> load. "what is this / what am I
looking at" -> explain. Parts not in the model: say so in reply and suggest "switch" to an AI-built structural model (which has named parts).
Respond ONLY with JSON: {{"reply": "short spoken confirmation (or answer)", "ops": [...]}}"""


def _context_text(ctx):
    ctx = ctx or {}
    parts = ctx.get("parts") or []
    if len(parts) > 220:
        parts = parts[:220] + [f"... and {len(parts) - 220} more"]
    lines = [f"OBJECTS: {json.dumps(ctx.get('objects') or [], ensure_ascii=False)[:600]}",
             f"CURRENT OBJECT: {ctx.get('subject') or 'nothing loaded'}  (representation: {ctx.get('kind')}, source: {ctx.get('source')})",
             f"PARTS: {', '.join(map(str, parts)) or 'one solid piece - no named parts'}",
             f"SELECTION: {ctx.get('selection') or 'none'}", f"HIDDEN: {', '.join(ctx.get('hidden') or []) or 'none'}",
             f"ISOLATED: {ctx.get('isolated') or 'no'}", f"VIEW: {ctx.get('view') or 'iso'}  EXPLODE: {ctx.get('explode', 0)}",
             f"XRAY: {ctx.get('xray', False)}  SECTION: {ctx.get('section') or 'none'}",
             f"ANIMATIONS: {', '.join(ctx.get('animations') or []) or 'none'}"]
    return "\n".join(lines)


def command(text, context=None):
    text = str(text or "").strip()[:500]
    raw = ask_llm([{"role": "user", "content": COMMAND_PROMPT.format(context=_context_text(context), text=text)}],
                  temperature=0.1, reasoning_effort="low", max_tokens=1500)
    data = extract_json(raw)
    if not isinstance(data, dict):
        raise RuntimeError("I didn't understand that command.")
    ops = [o for o in data.get("ops") or [] if isinstance(o, dict) and o.get("op")][:12]
    return {"reply": str(data.get("reply") or "")[:300], "ops": ops}


def wiki_page(query):
    """The best-matching Wikipedia article: title, extract, picture, link (or None)."""
    try:
        r = _get("https://en.wikipedia.org/w/api.php", timeout=15,
                 params={"action": "query", "list": "search", "srsearch": query, "srlimit": 1, "format": "json"})
        hits = r.json().get("query", {}).get("search", [])
        if not hits:
            return None
        title = hits[0]["title"]
        s = _get("https://en.wikipedia.org/api/rest_v1/page/summary/" + urllib.parse.quote(title.replace(" ", "_")), timeout=15).json()
        return {"title": s.get("title") or title, "extract": s.get("extract") or "", "url": (s.get("content_urls") or {}).get("desktop", {}).get("page"),
                "image": (s.get("originalimage") or s.get("thumbnail") or {}).get("source")}
    except Exception:
        return None


def explain(context, target=None):
    """FACTUAL SOURCE (Wikipedia, quoted with its link) kept apart from the AI EXPLANATION (which may only use the source for specifics)."""
    ctx = context or {}
    subject = ctx.get("subject") or "the object"
    target = (target or ctx.get("selection") or "").strip() or None
    page = wiki_page(f"{target} {subject}" if target else subject) or (wiki_page(target) if target else None)
    facts = None
    if page and page.get("extract"):
        facts = {"title": page["title"], "text": page["extract"][:1200], "url": page["url"], "source": "Wikipedia"}
    prompt = (f"Spatial context:\n{_context_text(ctx)}\n\nExplain {'the ' + target + ' of the ' + subject if target else 'what the user is looking at: ' + subject}.\n"
              f"Source text (may be about something else - only use it if it matches): {facts['text'] if facts else 'none'}\n"
              "Do not invent numbers, specifications or dates that are not in the source. Respond ONLY with JSON: "
              '{"name": "...", "what": "1-2 sentences", "function": "what it does / how it works", "connections": "what it connects to / sits next to",'
              ' "facts": ["2-4 short key facts"], "source_matches": true/false, "caution": "anything uncertain, or \'\'"}')
    try:
        ai = extract_json(ask_llm([{"role": "user", "content": prompt}], temperature=0.2, reasoning_effort="low", max_tokens=1500))
    except Exception as e:
        ai = {"what": f"I couldn't reach the AI to explain this ({e})."}
    if isinstance(ai, dict) and facts and ai.get("source_matches") is False:
        facts["mismatch"] = True
    return {"target": target, "subject": subject, "source": facts, "ai": ai if isinstance(ai, dict) else {"what": str(ai)}}


def identify(image_b64, items, context=None):
    """AURA's vision AI looks at the exploded model (pieces numbered in yellow) and names each piece. Names are suggestions."""
    import vision_lab
    subject = (context or {}).get("subject") or "the object"
    nums = []
    for i in (items or [])[:30]:
        try:
            nums.append(str(int(i.get("n"))))
        except (TypeError, ValueError):
            pass
    prompt = (f"This is an exploded 3D model of {subject}. Yellow dots numbered {', '.join(nums)} mark separate pieces. For EACH number, name the real "
              "component it most likely is (precise technical / anatomical name, 1-4 words), judging by its shape, size and position in the assembly. "
              "If a piece can't be told apart, use the name unknown. Never invent parts the object doesn't have. Respond ONLY with JSON: "
              '{"pieces": [{"n": 1, "name": "...", "desc": "one short line", "confidence": "high|medium|low"}], "summary": "one sentence on what the pieces are"}')
    data = vision_lab._json(str(image_b64 or ""), prompt, max_tokens=1600)
    pieces = []
    for p in (data.get("pieces") or []) if isinstance(data, dict) else []:
        try:
            pieces.append({"n": int(p.get("n")), "name": str(p.get("name") or "")[:60], "desc": str(p.get("desc") or "")[:160],
                           "confidence": str(p.get("confidence") or "medium").lower()})
        except (TypeError, ValueError, AttributeError):
            continue
    return {"pieces": pieces, "summary": str((data or {}).get("summary") or "")[:300] if isinstance(data, dict) else ""}


def count_parts(path):
    """How many separate meshes a model file really has (glTF / GLB); 1 for formats we can't count cheaply."""
    try:
        if path.lower().endswith(".glb"):
            with open(path, "rb") as f:
                head = f.read(20)
                n = int.from_bytes(head[12:16], "little")
                j = json.loads(f.read(n))
        elif path.lower().endswith(".gltf"):
            with open(path, encoding="utf-8") as f:
                j = json.load(f)
        else:
            return 1
        names = [nd.get("name", "") for nd in j.get("nodes", []) if "mesh" in nd]
        floors = sum(1 for nm in names if re.search(r"floor|ground|plane|backdrop|stand|base", nm, re.I))
        return max(1, len(names) - floors)
    except Exception:
        return 1


def _best_by_parts(u, real, order, attempt, job, done, alternatives, tried):
    """Downloads up to 3 relevant real models and returns an order that starts with the one built from the most separate parts."""
    best, best_n, picked = None, 0, []
    for c in real[:3]:
        prov = PROVIDER_BY_NAME.get(c["provider"])
        if not prov or c["provider"] in ("science", "library"):
            continue
        try:
            job.step(f"Checking which model has real separate parts ({len(picked) + 1}/3)…")
            rep = prov.fetch({**c, "understanding": u}, job)
        except Exception as e:
            tried.append({"source": prov.label, "title": c.get("title"), "error": str(e)[:200]})
            continue
        if rep.get("kind") != "model":
            continue
        n = count_parts(os.path.join(rep["dir"], rep["entry"]))
        picked.append((n, c))
        if n > best_n:
            best, best_n = c, n
        if n >= 25:
            break
    if not best:
        return None
    print(f"(spatial: most parts -> {best.get('title')} with {best_n} parts; checked {[(n, c.get('title')) for n, c in picked]})")
    rest = [x for x in order if x[1] is not best]
    return [(best["provider"], best)] + rest


DISSECT_PROMPT = """This is a front view of a 3D model of {subject}. Mark where each of its REAL component parts is, so the model can be cut
into those parts. Use the real names (anatomy, garment pieces, machine parts...). Cover the whole object; 8-30 parts; boxes may overlap
(smaller, more specific parts win where they overlap). Boxes are [ymin, xmin, ymax, xmax] on a 0-1000 scale of this image.
Also say whether the real thing has internal structure worth showing inside (organs/skeleton/engine internals) and describe it.
Respond ONLY with JSON: {{"parts": [{{"name": "...", "box": [ymin, xmin, ymax, xmax], "desc": "one short line"}}],
"has_internals": true|false, "internal_query": "e.g. 'human skeleton and major organs inside a body' or ''"}}"""


def dissect(image_b64, subject, context=None):
    """AURA's vision AI marks the real parts on a front view of a one-piece model (boxes), so the viewer can cut the REAL geometry
    into named parts. Also says whether there's an inside worth building."""
    import vision_lab
    data = vision_lab._json(str(image_b64 or ""), DISSECT_PROMPT.format(subject=subject or "the object"), max_tokens=2500)
    parts = []
    for p in (data.get("parts") or []) if isinstance(data, dict) else []:
        try:
            box = [max(0.0, min(1000.0, float(v))) for v in p.get("box")][:4]
            if len(box) == 4 and box[2] > box[0] and box[3] > box[1]:
                parts.append({"name": str(p.get("name") or "Part")[:50], "box": box, "desc": str(p.get("desc") or "")[:160]})
        except (TypeError, ValueError):
            continue
    ok = isinstance(data, dict)
    return {"parts": parts[:40], "has_internals": bool(data.get("has_internals")) if ok else False,
            "internal_query": str(data.get("internal_query") or "")[:200] if ok else ""}
