# cdc_window.py
# The CDC WONDER extractor window: asks for ICD-10 codes, age groups and
# options, runs the automatic (API) part in the background, walks you through
# the website exports for Census region / State / Urbanization (and picks the
# files up from Downloads by itself), then builds and opens the Excel files.
import os
import json
import time
import threading
import webbrowser
import webview
import cdc_wonder as W
import cdc_excel as X
from ai import ask_json

PAGE_URL = "http://localhost:8734/cdc_wonder.html"
_window = None
_ready = threading.Event()
_job = None
_imports = {}          # (condition, grouping, db) -> parsed website export
_prefill = None
_lock = threading.Lock()

PREFILL_PROMPT = """The user asked AURA to extract CDC WONDER mortality data: "{request}"

They study deaths where conditions occur TOGETHER: the first condition is
"A" (the main one) and the second, if any, is "B" (deaths with A AND B).
List the conditions in that order (max 2). For each: a short name, and the ICD-10 codes
ONLY if the user typed them. If they only named a condition, suggest the
standard ICD-10 codes used in CDC WONDER mortality research and mark them
suggested. Respond ONLY with JSON:
{{"conditions": [{{"name": "Heart failure", "codes": "I50", "suggested": true}}],
  "ages": "the age range if they mentioned one (e.g. '25+', '65 and older'), else ''",
  "cause": "underlying" if they said underlying cause, else "multiple"}}"""


class CdcAPI:
    def get_setup(self):
        return json.dumps({
            "ages": [{"value": v, "label": l} for v, l in W.AGE_GROUPS],
            "consented": W.has_consent(),
            "prefill": _prefill,
            "status": _status(),
            "gap_seconds": W.QUERY_GAP_SECONDS,
            "api_groupings": [g["label"] for g in W.API_GROUPINGS],
            "web_groupings": [g["label"] for g in W.WEB_GROUPINGS],
        })

    def check_codes(self, text):
        codes, errors = W.parse_icd_codes(text)
        return json.dumps({"codes": codes, "errors": errors})

    def start(self, spec_json):
        global _job
        spec = json.loads(spec_json)
        if not W.has_consent():
            if not spec.get("accept_terms"):
                return json.dumps({"error": "Please tick the box to accept CDC WONDER's data use restrictions first."})
            W.record_consent()
        # One extraction = Condition A, AND Condition B if given (deaths with both).
        a, b = spec.get("a") or {}, spec.get("b") or {}
        codes_a, errors_a = W.parse_icd_codes(a.get("codes", ""))
        codes_b, errors_b = W.parse_icd_codes(b.get("codes", ""))
        if errors_a or errors_b:
            return json.dumps({"error": f"Codes I don't recognise: {', '.join(errors_a + errors_b)}"})
        if not codes_a or not a.get("name", "").strip():
            return json.dumps({"error": "Condition A needs a name and at least one ICD-10 code."})
        if (b.get("name", "").strip() or codes_b) and not (codes_b and b.get("name", "").strip()):
            return json.dumps({"error": "Condition B needs both a name and ICD-10 codes (or leave both empty)."})
        cond = {"name": a["name"].strip(), "codes": codes_a, "label_a": a["name"].strip()}
        if codes_b:
            cond.update(name=f"{a['name'].strip()} + {b['name'].strip()}", and_codes=codes_b, label_b=b["name"].strip())
        conditions = [cond]
        ages = [a for a in spec.get("ages", []) if a in {v for v, _ in W.AGE_GROUPS}]
        with _lock:
            if _job and _job.state == "running":
                return json.dumps({"error": "An extraction is already running."})
            _imports.clear()
            _job = W.Job({"conditions": conditions, "ages": ages, "cause": spec.get("cause", "multiple")})
            _job.start(on_done=_api_finished)
        _start_watcher()
        return json.dumps({"ok": True, "status": _status()})

    def status(self):
        return json.dumps(_status())

    def stop(self):
        if _job:
            _job.stop()
        return True

    def open_website(self, db):
        url = W.WEBSITE_URLS.get(db)
        if url:
            webbrowser.open(url)
        return True

    def add_export_file(self):
        """Lets you pick an exported file by hand (if it was saved somewhere other than Downloads)."""
        if not _window:
            return json.dumps({"error": "Window not ready."})
        paths = _window.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=True,
                                           file_types=("CDC WONDER exports (*.txt;*.tsv;*.csv;*.xls)", "All files (*.*)"))
        added = [_take_export(X.parse_web_export(p)) for p in (paths or [])]
        return json.dumps({"added": sum(bool(a) for a in added), "status": _status()})

    def build(self):
        return json.dumps({"outputs": _build(open_files=True), "status": _status()})

    def open_path(self, path):
        if _job and os.path.realpath(path).startswith(os.path.realpath(_job.folder)):
            os.startfile(path)
            return True
        return False

    def close(self):
        hide()


# ------------------------------------------------------------------ helpers
def _status():
    if not _job:
        return None
    p = _job.progress()
    p["conditions"] = [c["name"] for c in _job.spec["conditions"]]
    p["codes"] = {c["name"]: c["codes"] for c in _job.spec["conditions"]}
    p["and_codes"] = {c["name"]: c["and_codes"] for c in _job.spec["conditions"] if c.get("and_codes")}
    p["ages"] = [l for v, l in W.AGE_GROUPS if v in (_job.spec.get("ages") or [])] or ["All ages"]
    p["cause"] = _job.spec.get("cause", "multiple")
    p["checklist"] = [
        {"condition": c["name"], "grouping": g["label"], "db": db, "dataset": W.DATASETS[db]["short"],
         "file": os.path.basename(_imports[(c["name"], g["key"], db)]["file"]) if (c["name"], g["key"], db) in _imports else None}
        for c in _job.spec["conditions"] for g in W.WEB_GROUPINGS for db in W.DATASETS
    ]
    return p


def _take_export(export):
    if not export or not _job:
        return False
    cond = X.match_export(_job, export)
    if not cond:
        return False
    export["condition"] = cond["name"]
    key = (cond["name"], export["grouping"], export["db"])
    new = key not in _imports or _imports[key]["file"] != export["file"]
    _imports[key] = export
    if new and _job.state == "api_done" and all(
            (c["name"], g["key"], db) in _imports for c in _job.spec["conditions"] for g in W.WEB_GROUPINGS for db in W.DATASETS):
        _build(open_files=True)  # everything's in - rebuild with location data
    return True


def _start_watcher():
    """Checks Downloads every few seconds for CDC WONDER website exports."""
    job = _job

    def loop():
        seen = set()
        while _job is job:
            for export in X.find_web_exports(job.started_at):
                sig = (export["file"], os.path.getmtime(export["file"]))
                if sig not in seen:
                    seen.add(sig)
                    _take_export(export)
            time.sleep(3)

    threading.Thread(target=loop, daemon=True).start()


def _build(open_files=False):
    if not _job:
        return []
    outputs = []
    for cond in _job.spec["conditions"]:
        try:
            outputs.append(X.build_workbook(_job, cond, list(_imports.values())))
        except Exception as e:
            print(f"(cdc wonder: couldn't build workbook for {cond['name']}: {e})")
    _job.outputs = outputs
    if open_files:
        for path in outputs:
            try:
                os.startfile(path)
            except OSError:
                pass
    return outputs


def _api_finished(job):
    outputs = _build(open_files=True)
    missing = sum(1 for item in (_status() or {}).get("checklist", []) if not item["file"])
    names = ", ".join(c["name"] for c in job.spec["conditions"])
    try:
        import face_window
        from voice import speak
        face_window.show_toast("CDC WONDER data ready", f"{len(outputs)} workbook(s) for {names} opened in Excel.", "focus")
        extra = (f" The location sheets will be added as soon as you export the {missing} remaining files from the website."
                 if missing else "")
        speak(f"Your CDC WONDER data for {names} is ready - I've opened it in Excel.{extra}")
    except Exception:
        pass


def _prefill_from_text(request):
    """Reads conditions/codes/ages out of a request like 'extract CDC WONDER
    data for heart failure I50 and arthrosis M15-M19, ages 25+'."""
    if not request or len(request.split()) < 4:
        return None
    data = ask_json([{"role": "user", "content": PREFILL_PROMPT.format(request=request)}], default=None, reasoning_effort="low")
    if not isinstance(data, dict) or not data.get("conditions"):
        return None
    # the first two conditions become A and B (deaths with both)
    ages_text = str(data.get("ages") or "")
    ages = []
    import re
    m = re.search(r"(\d+)\s*(?:\+|and (?:older|over|above)|or older)", ages_text)
    if m:
        low = int(m.group(1))
        for v, _ in W.AGE_GROUPS:
            first = re.match(r"(\d+)", v)
            if first and int(first.group(1)) >= low:
                ages.append(v)
    return {"conditions": [{"name": str(c.get("name", "")), "codes": str(c.get("codes", "")), "suggested": bool(c.get("suggested"))}
                           for c in data["conditions"] if isinstance(c, dict)][:2],
            "ages": ages, "cause": "underlying" if data.get("cause") == "underlying" else "multiple"}


# ------------------------------------------------------------------ window
def _on_loaded():
    _ready.set()


def _on_closing():
    hide()
    return False


def create_window():
    """Must be called BEFORE webview.start(), same as AURA's other windows."""
    global _window
    _window = webview.create_window("AURA - CDC WONDER", PAGE_URL, width=1180, height=860, resizable=True,
                                    background_color="#02050a", hidden=True, js_api=CdcAPI())
    _window.events.loaded += _on_loaded
    _window.events.closing += _on_closing


def open_extractor(request=""):
    """Voice/typed command entry point. Returns a spoken reply."""
    global _prefill
    if not _window:
        return "The CDC WONDER extractor isn't available right now."
    if _job and _job.state == "running":
        _window.show()
        return "Your CDC WONDER extraction is still running - I've brought up its progress."
    _prefill = _prefill_from_text(request)
    try:
        _window.show()
        if _ready.is_set():
            _window.evaluate_js("window.cdcRefresh && window.cdcRefresh()")
    except Exception as e:
        return f"Couldn't open the extractor: {e}"
    if _prefill and any(c.get("suggested") for c in _prefill["conditions"]):
        return ("Opening the CDC WONDER extractor. I've filled in suggested ICD-10 codes - please check them, pick the age groups, "
                "and press Start.")
    return "Opening the CDC WONDER extractor - enter the ICD-10 codes and age groups, then press Start."


def hide():
    if _window:
        try:
            _window.hide()
        except Exception:
            pass
