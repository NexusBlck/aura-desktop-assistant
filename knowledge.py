# knowledge.py
# AURA's personal knowledge base: YOUR study material, searchable, so answers come from your own course
# instead of general knowledge. Free and fully on this PC (no API, no limits).
#
# Indexes: ECHO recordings (lecture transcripts + notes), your notes, AURA Uploads (PDFs / slides / Word /
# text you added), Vision scans (DOC mode text), PBL drafts, saved papers, plus any folders listed in
# config.KNOWLEDGE_FOLDERS. Text is cut into passages and ranked with BM25 (the classic search-engine
# formula) with light word-stemming, so "diuretics" finds "diuretic". Rebuilt in the background when files
# change; search(question) returns the best passages with where they came from.
import json
import math
import os
import re
import threading
import time

import config

AURA_DIR = os.path.dirname(os.path.abspath(__file__))
INDEX_FILE = os.path.join(AURA_DIR, "aura_knowledge.json")
DOCS = os.path.join(os.path.expanduser("~"), "Documents")
TEXT_EXT = {".txt", ".md", ".csv"}
DOC_EXT = TEXT_EXT | {".pdf", ".docx", ".pptx"}
CHUNK, OVERLAP = 900, 150            # characters per passage
MAX_FILES, MAX_FILE_MB = 600, 40
_lock = threading.RLock()
_index = None                       # {"files": {path: {"mtime", "chunks": [{"text","where"}]}}, ...}
_bm25 = None                        # built search structures (in memory only)

STOP = set("""a an the and or but if then of to in on at by for with from as is are was were be been being it its this that these those
i you he she we they me my your our their them his her what which who whom whose when where why how do does did done can could
should would will shall may might must not no yes so than too very just about into over under again further once here there all any
both each few more most other some such only own same also up down out off s t don now please tell explain""".split())


def sources():
    """Folders (and how to label them) that AURA reads."""
    out = [(os.path.join(DOCS, "AURA Echo"), "Lecture"), (os.path.join(DOCS, "AURA Uploads"), "Upload"),
           (os.path.join(DOCS, "AURA Vision"), "Scan"), (os.path.join(DOCS, "AURA PBL"), "PBL draft")]
    for folder in getattr(config, "KNOWLEDGE_FOLDERS", []) or []:
        out.append((os.path.expandvars(os.path.expanduser(folder)), os.path.basename(folder.rstrip("\\/")) or "Folder"))
    return out


# ------------------------------------------------------------------ reading files
def _read(path):
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext in TEXT_EXT:
            with open(path, encoding="utf-8", errors="ignore") as f:
                return [(f.read(), "")]
        if ext == ".pdf":
            from pypdf import PdfReader
            pages = PdfReader(path).pages[:400]
            return [(p.extract_text() or "", f"p.{i + 1}") for i, p in enumerate(pages)]
        if ext == ".docx":
            import docx
            return [("\n".join(p.text for p in docx.Document(path).paragraphs), "")]
        if ext == ".pptx":
            from pptx import Presentation
            slides = []
            for i, s in enumerate(Presentation(path).slides):
                slides.append(("\n".join(sh.text_frame.text for sh in s.shapes if getattr(sh, "has_text_frame", False)), f"slide {i + 1}"))
            return slides
    except Exception as e:
        print(f"(knowledge: couldn't read {os.path.basename(path)}: {e})")
    return []


def _echo(path):
    """An ECHO recording: its notes + the transcript with timestamps."""
    try:
        with open(path, encoding="utf-8") as f:
            rec = json.load(f)
    except Exception:
        return None, []
    parts = []
    n = rec.get("notes") or {}
    if n:
        bits = [n.get("summary", "")] + [f"- {x}" for k in ("key_points", "exam_points", "decisions", "action_items") for x in (n.get(k) or [])]
        bits += [f"{c.get('q')} - {c.get('a')}" for c in (n.get("flashcards") or []) if isinstance(c, dict)]
        bits += [f"{t.get('term')}: {t.get('meaning')}" for t in (n.get("terms") or []) if isinstance(t, dict)]
        parts.append(("\n".join(b for b in bits if b), "notes"))
    seg_text, start = [], None
    for s in rec.get("segments") or []:
        if start is None:
            start = s.get("t", 0)
        seg_text.append(s.get("text", ""))
        if sum(len(x) for x in seg_text) > CHUNK:
            parts.append((" ".join(seg_text), f"{int(start // 60):02d}:{int(start % 60):02d}"))
            seg_text, start = [], None
    if seg_text:
        parts.append((" ".join(seg_text), f"{int((start or 0) // 60):02d}:{int((start or 0) % 60):02d}"))
    return rec.get("title") or "Recording", parts


def _chunks(parts):
    out = []
    for text, where in parts:
        text = re.sub(r"[ \t]+", " ", text or "").strip()
        i = 0
        while i < len(text):
            piece = text[i:i + CHUNK]
            if len(piece.strip()) > 40:
                out.append({"text": piece, "where": where})
            i += CHUNK - OVERLAP
    return out


def _files():
    found = []
    for folder, label in sources():
        if not os.path.isdir(folder):
            continue
        for root, _dirs, names in os.walk(folder):
            for name in names:
                ext = os.path.splitext(name)[1].lower()
                if name.startswith(("~$", ".")):
                    continue                 # Office lock files, hidden files
                if ext in DOC_EXT or (label == "Lecture" and ext == ".json"):
                    if label == "Lecture" and ext == ".md":
                        continue             # the .md copy of a recording - the .json has the same, with timestamps
                    found.append((os.path.join(root, name), label))
                if len(found) >= MAX_FILES:
                    return found
    return found


# ------------------------------------------------------------------ the index
def _load():
    global _index
    if _index is None:
        try:
            with open(INDEX_FILE, encoding="utf-8") as f:
                _index = json.load(f)
        except Exception:
            _index = {"files": {}}
    return _index


def refresh():
    """Re-reads changed files (only those). Returns (files, passages)."""
    global _bm25
    with _lock:
        idx = _load()
        files = idx["files"]
        current = {}
        for path, label in _files():
            try:
                st = os.stat(path)
            except OSError:
                continue
            if st.st_size > MAX_FILE_MB * 1024 * 1024:
                continue
            old = files.get(path)
            if old and old.get("mtime") == st.st_mtime:
                current[path] = old
                continue
            if label == "Lecture" and path.endswith(".json"):
                title, parts = _echo(path)
            else:
                title, parts = os.path.splitext(os.path.basename(path))[0], _read(path)
            current[path] = {"mtime": st.st_mtime, "label": label, "title": title, "chunks": _chunks(parts)}
        try:                                          # your quick notes (notes.py)
            from notes import get_notes
            text = get_notes() or ""
            current["notes:"] = {"mtime": hash(text), "label": "Notes", "title": "Your notes", "chunks": _chunks([(text, "")])}
        except Exception:
            pass
        try:                                          # saved papers (paper_library.py)
            from paper_library import load_library
            lib = load_library()
            items = lib if isinstance(lib, list) else lib.get("papers", []) if isinstance(lib, dict) else []
            text = "\n".join(" - ".join(str(p.get(k, "")) for k in ("title", "authors", "year", "notes", "tags") if p.get(k)) for p in items if isinstance(p, dict))
            current["papers:"] = {"mtime": hash(text), "label": "Paper", "title": "Paper library", "chunks": _chunks([(text, "")])}
        except Exception:
            pass
        idx["files"] = current
        idx["built"] = time.time()
        tmp = INDEX_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(idx, f, ensure_ascii=False)
        os.replace(tmp, INDEX_FILE)
        _bm25 = None
        return len(current), sum(len(v["chunks"]) for v in current.values())


def refresh_in_background(every_minutes=30):
    def loop():
        while True:
            try:
                refresh()
            except Exception as e:
                print(f"(knowledge index: {e!r})")
            time.sleep(every_minutes * 60)
    threading.Thread(target=loop, daemon=True, name="knowledge").start()


# ------------------------------------------------------------------ search (BM25)
def _stem(w):
    for suf in ("ization", "ations", "ation", "ings", "ing", "edly", "ies", "ied", "es", "ed", "ly", "s"):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            return w[: -len(suf)] + ("y" if suf in ("ies", "ied") else "")
    return w


def tokens(text):
    return [_stem(w) for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if w not in STOP and len(w) > 1]


def _build():
    global _bm25
    idx = _load()
    docs, df = [], {}
    for path, info in idx["files"].items():
        for c in info["chunks"]:
            tf = {}
            for t in tokens(c["text"] + " " + info.get("title", "")):
                tf[t] = tf.get(t, 0) + 1
            docs.append((path, info, c, tf, sum(tf.values())))
            for t in tf:
                df[t] = df.get(t, 0) + 1
    avg = sum(d[4] for d in docs) / max(1, len(docs))
    _bm25 = {"docs": docs, "df": df, "avg": avg, "n": len(docs)}
    return _bm25


def search(query, k=6, min_score=0.8):
    """The best passages from your material: [{"text", "source", "title", "where", "score"}]."""
    with _lock:
        bm = _bm25 or _build()
    q = list(dict.fromkeys(tokens(query)))
    if not q or not bm["n"]:
        return []
    k1, b = 1.5, 0.75
    scored = []
    for path, info, c, tf, length in bm["docs"]:
        s = 0.0
        for t in q:
            f = tf.get(t)
            if f:
                idf = math.log(1 + (bm["n"] - bm["df"][t] + 0.5) / (bm["df"][t] + 0.5))
                s += idf * f * (k1 + 1) / (f + k1 * (1 - b + b * length / bm["avg"]))
        if s >= min_score:
            scored.append((s, path, info, c))
    scored.sort(key=lambda x: x[0], reverse=True)
    out, per_file = [], {}
    for s, path, info, c in scored:
        if per_file.get(path, 0) >= 3:                # variety: at most 3 passages from one file
            continue
        per_file[path] = per_file.get(path, 0) + 1
        out.append({"text": c["text"], "title": info.get("title", ""), "source": info.get("label", ""), "where": c.get("where", ""),
                    "score": round(s, 2)})
        if len(out) >= k:
            break
    return out


def stats():
    idx = _load()
    return {"files": len(idx["files"]), "passages": sum(len(v["chunks"]) for v in idx["files"].values()), "built": idx.get("built"),
            "folders": [f for f, _ in sources()]}
