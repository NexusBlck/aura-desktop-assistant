# autocoder.py
# Builds real apps, websites, tools and games from a description - in any
# language - with a polished frontend AND a solid backend.
#
# Two engines:
#   * Claude Code (claude_builder.py) - used whenever it's installed and
#     signed in. Much stronger: it designs, builds, runs, browser-tests and
#     visually polishes the app on its own.
#   * AURA's own builder (below) - the fallback, on the free model. Instead
#     of writing files blind, it plans an API CONTRACT + design direction up
#     front, writes files in dependency order with the files they depend on
#     in view, ships a designed UI kit (build_kit/aura-ui.css) so apps look
#     good by default, then really tests the result (build_verify.py: installs,
#     starts the server, calls the API, opens it in headless Edge) and fixes
#     whichever files are actually broken.
#
# Builds run in the BACKGROUND - AURA stays usable, the HUD shows progress,
# and AURA tells you (voice + toast) when it's done and opens the app.
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time

from ai import ask_llm
import build_verify
import claude_builder

AURA_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECTS_DIR = os.path.join(os.path.expanduser("~"), "Desktop", "AURA_Projects")
BUILD_STATE_FILE = os.path.join(AURA_DIR, "aura_last_build.json")
KIT_CSS = os.path.join(AURA_DIR, "build_kit", "aura-ui.css")
DESIGN_GUIDE = os.path.join(AURA_DIR, "build_kit", "DESIGN.md")
DEPENDENCY_FILENAMES = {"package.json", "requirements.txt", "Pipfile", "go.mod", "Cargo.toml", "Gemfile", "pyproject.toml"}
FRONTEND_EXTS = {".html", ".htm", ".css", ".js", ".jsx", ".ts", ".tsx", ".vue", ".svelte", ".mjs"}
MAX_FIX_ROUNDS = 4

# Phrases that mean "don't hand this to Claude Code".
SELF_BUILD_RE = re.compile(r"\b(build it yourself|by yourself|on your own|without claude|don'?t use claude|aura'?s own builder)\b", re.I)

_build_lock = threading.Lock()
_current_build = {"name": None}


def _design_guide():
    with open(DESIGN_GUIDE, encoding="utf-8") as f:
        return f.read()


KIT_REFERENCE = """AURA UI KIT (already in the project at {kit_path} - link it FIRST, then the app's own
stylesheet that sets the theme tokens and adds app-specific styles). Theme it per app:
  :root { --primary: 262 83% 58%; --font: 'Manrope', sans-serif; }   /* HSL triplets */
Tokens: --primary --primary-fg --success --warning --danger --bg --surface --surface-2 --border
  --text --muted (use as hsl(var(--x)) / hsl(var(--x) / .12)), --radius --shadow-sm --shadow --shadow-lg --font.
Auto dark mode via prefers-color-scheme; force with <html data-theme="dark|light">.
Layout: .container .page .stack .row .spread .grid(--min) .grid-2 .grid-3 .app-shell(.sidebar + main)
Nav: .topbar>.container>.brand(.logo)+.nav>a(.active)   .sidebar>a(.active)
Surfaces: .card(.hover) .card-header .panel .divider .hero .stat>.value+.label
Buttons: .btn .btn-secondary .btn-ghost .btn-danger .btn-sm .btn-lg .btn-icon .btn-block
Forms: .field>.label+.input|.select|.textarea+.hint|.error-text  .checkbox  .search>svg+.input  .input.invalid
Data: .badge(.primary|.success|.warning|.danger) .table-wrap>.table .list>.list-item .avatar .progress>span(width%)
  .tabs>.tab(.active)
Feedback: .empty-state>.icon+h3+p  .alert(.danger|.success)  .skeleton  .spinner
  .toasts>.toast(.success|.danger)   .modal-backdrop(.open)>.modal>...+.modal-footer
Typography: .muted .small .center .eyebrow; h1-h4 are pre-styled. Icons: inline SVG (Lucide style, stroke 2).
Build distinctive screens by COMPOSING these - don't re-style buttons/inputs/cards from scratch."""


# ------------------------------------------------------------------ state + helpers
def _save_build_state(name, status, path, step="", engine=""):
    try:
        with open(BUILD_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"project_name": name, "status": status, "path": path, "step": step,
                       "engine": engine, "updated": time.time()}, f)
    except Exception:
        pass


def _strip_code_fence(raw):
    raw = (raw or "").strip()
    m = re.match(r"^```[\w+-]*\s*\n(.*)\n```\s*$", raw, re.S)
    if m:
        return m.group(1)
    if raw.startswith("```"):
        lines = raw.split("\n")[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines)
    return raw


def _slug(text):
    s = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")
    return s[:40] or "aura_project"


def _project_name(request):
    data = _paced_json("Give a short snake_case folder name (2-4 words) for this project. "
                       f'Reply ONLY with JSON {{"name": "..."}}.\n\nProject: {request[:600]}', expect=300, default={})
    return _slug((data or {}).get("name") or " ".join(request.split()[:4]))


def _unique_dir(name):
    path = os.path.join(PROJECTS_DIR, name)
    n = 2
    while os.path.exists(path) and os.listdir(path):
        path = os.path.join(PROJECTS_DIR, f"{name}_{n}")
        n += 1
    os.makedirs(path, exist_ok=True)
    return path


# ------------------------------------------------------------------ free-tier pacing
# Groq's free tier allows ~8,000 tokens per MINUTE per model (prompt + answer,
# reasoning included) - one big prompt can use most of it. So every builder
# call is sized to fit, and spread across all the models AURA has, waiting
# for a model's minute to free up instead of failing.
TOKENS_PER_MIN = 7600
MAX_PROMPT_TOKENS = 4300       # leaves room for a ~3k-token file in the same minute
EXPECTED_REPLY = 3200
_usage = {}                    # model -> list of (timestamp, tokens)
_pace_lock = threading.Lock()


class PromptTooLarge(Exception):
    pass


def _est_tokens(text):
    return int(len(text) / 3.3) + 20


def _used(model, now):
    entries = [(t, n) for t, n in _usage.get(model, []) if now - t < 60]
    _usage[model] = entries
    return sum(n for _, n in entries)


# Per-model caps learned from the provider's "request too large" errors, e.g.
# a model allowed only 1,000 OUTPUT tokens a minute can never write a code file.
_limits = {}                   # model -> {"in": max input tokens, "out": max output tokens}


_blocked_until = {}            # model -> time its DAILY quota frees up again


class DailyLimitReached(Exception):
    pass


def _learn_limit(model, message):
    m = re.search(r"\((ITPM|OTPM)\): Limit (\d+)", message)
    if m:
        key = "in" if m.group(1) == "ITPM" else "out"
        _limits.setdefault(model, {})[key] = int(m.group(2))
        return True
    if "per day" in message or "(TPD)" in message or "(RPD)" in message:
        wait = re.search(r"try again in (?:(\d+)h)?(?:(\d+)m)?(?:([\d.]+)s)?", message)
        secs = 3600.0
        if wait and any(wait.groups()):
            h, mnt, s = wait.groups()
            secs = int(h or 0) * 3600 + int(mnt or 0) * 60 + float(s or 0)
        _blocked_until[model] = time.time() + secs + 5
        return True
    return False


def _fits(model, prompt_tokens, expect):
    lim = _limits.get(model, {})
    return (prompt_tokens <= lim.get("in", 10**9) * 0.95 and expect <= lim.get("out", 10**9)
            and _blocked_until.get(model, 0) < time.time())


def _paced(prompt, temperature=0.35, reasoning_effort="low", expect=EXPECTED_REPLY, system=None, info=None):
    """One model call that respects every model's per-minute token budget.
    info (a dict) receives "truncated": True if the reply was cut off."""
    from ai import MODELS_TO_TRY, RateLimitError, APIStatusError, _resting_until
    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    prompt_tokens = sum(_est_tokens(m["content"]) for m in messages)
    if prompt_tokens > TOKENS_PER_MIN - 1500:
        raise PromptTooLarge(f"{prompt_tokens} tokens")
    need = min(prompt_tokens + expect, TOKENS_PER_MIN)
    last_error = None
    for _attempt in range(14):
        with _pace_lock:
            now = time.time()
            capable = [m for m in MODELS_TO_TRY if _fits(m, prompt_tokens, expect)
                       and _resting_until.get(m, 0) < now] or [m for m in MODELS_TO_TRY if _fits(m, prompt_tokens, expect)]
            if not capable:
                if any(_blocked_until.get(m, 0) > now for m in MODELS_TO_TRY):
                    soonest = min(t for t in _blocked_until.values() if t > now)
                    raise DailyLimitReached(f"the free model's daily limit is used up - it resets in about "
                                            f"{max(1, round((soonest - now) / 60))} minutes")
                raise PromptTooLarge(f"no model accepts {prompt_tokens} in / {expect} out tokens")
            model = next((m for m in capable if _used(m, now) + need <= TOKENS_PER_MIN), None)
            if model:
                _usage.setdefault(model, []).append((now, need))   # reserve now, correct after
            else:
                oldest = min((t for m in capable for t, _ in _usage.get(m, [])), default=now)
                wait = max(1.0, 60 - (now - oldest) + 0.5)
        if not model:
            time.sleep(min(wait, 30))
            continue
        usage = {}
        try:
            text = ask_llm(messages, temperature=temperature, reasoning_effort=reasoning_effort,
                           models=[model], usage=usage)
            with _pace_lock:   # replace the reservation with what it really cost
                entries = _usage.get(model, [])
                for i in range(len(entries) - 1, -1, -1):
                    if entries[i][1] == need:
                        entries[i] = (entries[i][0], usage.get("total_tokens") or need)
                        break
            if info is not None:
                info["truncated"] = usage.get("finish_reason") == "length"
            return text
        except (RateLimitError, APIStatusError) as e:
            last_error = e
            with _pace_lock:
                if not _learn_limit(model, str(e)):
                    # out of budget right now (or a one-off failure) - rest this model for a minute
                    _usage.setdefault(model, []).append((time.time(), TOKENS_PER_MIN))
        except Exception as e:
            last_error = e
            time.sleep(3)
    raise last_error or RuntimeError("no model available")


def _paced_json(prompt, temperature=0.1, reasoning_effort="low", expect=900, system=None, default=None):
    from ai import extract_json
    try:
        return extract_json(_paced(prompt, temperature, reasoning_effort, expect, system))
    except DailyLimitReached:
        raise
    except Exception as e:
        print(f"  (couldn't get JSON from the model: {str(e)[:160]})")
        return default


# ------------------------------------------------------------------ AURA's own builder
PLAN_PROMPT = """You are a principal engineer AND a product designer planning a project from the
user's request. Respond ONLY with one JSON object:

{
  "project_name": "short_snake_case_name",
  "summary": "one sentence: what it is and who it's for",
  "tech_stack": "ONE precise sentence locking in the exact language, framework, storage and frontend approach",
  "kind": "web | static | cli | desktop | other",
  "design": {
    "direction": "the visual concept in one sentence (e.g. 'calm, airy productivity app - soft neutrals, one confident violet accent')",
    "primary_hsl": "H S% L% for the accent colour, e.g. 262 83% 58%",
    "font": "a Google Font family name, e.g. Manrope",
    "screens": ["each screen/section and what's on it"]
  },
  "data_model": "entities and their fields, with types",
  "api": [
    {"method": "GET", "path": "/api/items", "request": "query/body fields or null", "response": "exact JSON shape returned", "purpose": "..."}
  ],
  "features": ["the complete feature list - real, useful features, not the bare minimum"],
  "files": [
    {"path": "relative/path.ext", "purpose": "what it contains", "depends_on": ["paths of files whose CONTENT this file must match"]}
  ],
  "ui_kit_path": "where the UI kit stylesheet should live so the frontend can link it (e.g. static/aura-ui.css or aura-ui.css), or null if there's no web UI",
  "run": {
    "kind": "web | static | cli | other",
    "start": "command to start it from the project folder, e.g. python app.py (null for static)",
    "port": 5000,
    "path": "/",
    "smoke": [{"method": "GET", "path": "/api/items"}],
    "check": "for cli/other: a quick command that proves it works without user input, or null"
  },
  "run_instructions": "how the user runs and uses it"
}

ARCHITECTURE RULES:
- ONE coherent stack, fully committed. If the user named a language/framework, use it.
- Web apps: default to ONE backend (Python Flask or FastAPI, or Node Express) that serves
  the API under /api/... AND the frontend (plain HTML + CSS + modern JavaScript, no build
  step) from the same server, with SQLite or a JSON file for storage. Only use a bundler
  (Vite/React) if the request truly needs it.
- The server reads its port from the PORT environment variable (default = run.port) and
  creates its database/tables and sample seed data automatically on first start.
- Pure websites/landing pages with no backend: kind "static" (index.html + css + js).
- Other kinds (CLI tools, games, scripts, desktop apps) - pick what fits; for games in
  the browser prefer a static HTML5 canvas game.
- List files in the order they should be written: data/backend first, then frontend
  (HTML before the JS/CSS that style/drive it), README.md, and dependency manifests LAST.
- The api list is a CONTRACT: the backend implements exactly it and the frontend calls
  exactly it. Include every route the frontend needs.
- Keep the file list focused: typically 5-10 files, each under ~250 lines. Split big
  frontend logic into modules (e.g. static/js/api.js, static/js/ui.js, static/js/app.js) and
  page styling into the app's own CSS file. No placeholder or empty files.

"""


def _parse_object(raw):
    """The OUTERMOST JSON object in a reply (ai.extract_json would fall back to a
    nested object - e.g. just the "design" part - if the whole thing is broken)."""
    text = _strip_code_fence(raw or "")
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object")
    return json.JSONDecoder().raw_decode(text[start:])[0]


def _plan(request):
    system = PLAN_PROMPT + _design_guide()
    raw = _paced(request, temperature=0.3, reasoning_effort="medium", expect=3600, system=system)
    try:
        plan = _parse_object(raw)
    except ValueError as e:
        if "Unterminated" not in str(e) and "Expecting" not in str(e):
            return None
        # The reply was cut off part-way through the JSON - have it finish.
        complete = _complete_lines(_strip_code_fence(raw))
        more = _paced("You were writing this JSON project plan and your output was cut off. The LAST complete lines were:\n\n"
                      + "\n".join(complete[-25:]) + "\n\nContinue with the line right after these and finish the JSON. "
                      "Output only the remaining JSON text - no fences, no commentary.", temperature=0.2, expect=2500)
        try:
            plan = _parse_object(_stitch(complete, _strip_code_fence(more)))
        except ValueError:
            return None
    if not isinstance(plan, dict) or not plan.get("files"):
        return None
    files = [f for f in plan["files"] if isinstance(f, dict) and f.get("path")]
    for f in files:
        f["path"] = f["path"].replace("\\", "/").lstrip("/")
    kit = (plan.get("ui_kit_path") or "").replace("\\", "/").lstrip("/")
    plan["ui_kit_path"] = kit or None
    plan["files"] = [f for f in files if not (kit and f["path"] == kit)]   # we supply the kit ourselves
    plan["files"].sort(key=lambda f: os.path.basename(f["path"]) in DEPENDENCY_FILENAMES)
    return plan


def _contract(plan):
    keep = {k: plan.get(k) for k in ("summary", "tech_stack", "design", "data_model", "api", "features", "ui_kit_path", "run")
            if plan.get(k)}
    keep["files"] = [f"{f['path']}: {f.get('purpose', '')[:140]}" for f in plan["files"]]
    return json.dumps(keep, separators=(",", ":"), ensure_ascii=False)


def _is_backend(path):
    """Server-side source: the routes a frontend file has to match."""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".py", ".go", ".cs", ".rb", ".java", ".php", ".rs"):
        return True
    in_frontend_dir = path.startswith(("static/", "public/", "frontend/", "client/", "src/components/"))
    return ext in (".js", ".ts", ".mjs") and not in_frontend_dir and \
        bool(re.search(r"(^|/)(server|app|index|main|routes?|api)\.\w+$", path))


DESIGN_SHORT = """STANDARDS (non-negotiable):
- Real product, not a demo: complete flows (create/edit/delete/search where they fit), seed data so it
  never opens empty, designed loading/empty/error/success states (toasts, not alert()), Enter submits, Esc closes.
- Backend: validate input, JSON errors with proper status codes, never crash; PORT from env; serves the frontend.
- Frontend: follow the design direction; one accent + neutral palette, the chosen Google Font, 8px spacing rhythm,
  cards/radii/subtle shadows, hover/focus states, inline SVG icons (never emoji as icons), responsive to 375px,
  one obvious primary action per screen. No lorem ipsum, no default browser styling, no 'undefined'/'NaN' on screen.
- Frontend and backend must agree EXACTLY on routes, field names and JSON shapes (the contract).
- Keep each file focused and under ~250 lines."""


def _digest(path, body):
    """A compact summary of a file's interface - what other files must match -
    used when the whole file doesn't fit in the prompt budget."""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".html", ".htm"):
        ids = sorted(set(re.findall(r'\bid=["\']([^"\']+)', body)))
        forms = sorted(set(re.findall(r'\bname=["\']([^"\']+)', body)))
        links = re.findall(r'<(?:script|link)[^>]+(?:src|href)=["\']([^"\']+)', body)
        datas = sorted(set(re.findall(r'\b(data-[\w-]+)', body)))
        return (f"element ids: {', '.join(ids)}\nform field names: {', '.join(forms)}\n"
                f"data attributes: {', '.join(datas)}\nloads: {', '.join(links)}")
    if ext == ".css":
        return "classes: " + ", ".join(sorted(set(re.findall(r"\.([a-zA-Z][\w-]*)\s*[{,:]", body)))[:120])
    keep = []
    pattern = re.compile(r"^\s*(@\w+[\.\w]*\(|(async\s+)?def |class |(export\s+)?(async\s+)?function |"
                         r"(export\s+)?(const|let)\s+\w+\s*=\s*(async\s*)?(\(|function)|"
                         r"(app|router|api|bp|blueprint)\.(get|post|put|patch|delete|route|use)\(|"
                         r"CREATE TABLE|fetch\(|(import|from)\s)", re.I)
    for line in body.splitlines():
        if pattern.search(line) or "fetch(" in line or "/api/" in line:
            keep.append(line.rstrip()[:160])
    return "\n".join(keep[:90]) or body[:1200]


def _related_context(plan, path, contents, budget):
    """The files this one has to agree with: its declared dependencies, plus for
    frontend files the backend routes, and for manifests every source file.
    Whole files while they fit the budget, interface digests after that."""
    names = list((next((f for f in plan["files"] if f["path"] == path), {}) or {}).get("depends_on") or [])
    ext = os.path.splitext(path)[1].lower()
    base = os.path.basename(path)
    if base in DEPENDENCY_FILENAMES:
        names = [p for p in contents if os.path.basename(p) not in DEPENDENCY_FILENAMES]
    elif ext in FRONTEND_EXTS:
        names += [p for p in contents if _is_backend(p)]
        names += [p for p in contents if p.endswith((".html", ".htm"))]
    parts, seen = [], set()
    for n in names:
        if n == path or n in seen or n not in contents or n == plan.get("ui_kit_path"):
            continue   # (the kit is described by KIT_REFERENCE instead of pasted in full)
        seen.add(n)
        body = contents[n]
        if base in DEPENDENCY_FILENAMES:   # a manifest only needs the imports
            body = "\n".join(l for l in body.splitlines() if re.match(r"\s*(import |from |const .*require\(|.*\brequire\()", l))
            label = "imports"
        elif len(body) + 40 <= budget:
            label = "full"
        else:
            body, label = _digest(n, body), "interface digest"
        if len(body) > budget:
            body = body[:max(budget, 0)]
        if budget <= 200:
            break
        parts.append(f"--- {n} ({label}) ---\n{body}")
        budget -= len(body) + 40
    return "\n\n".join(parts)


def _file_prompt(request, plan, path, contents, task, current=None, budget=10000):
    ext = os.path.splitext(path)[1].lower()
    frontend = ext in FRONTEND_EXTS and plan.get("ui_kit_path")
    kit = KIT_REFERENCE.replace("{kit_path}", plan["ui_kit_path"]) if frontend else ""
    if frontend and ext in (".html", ".htm"):
        href = os.path.relpath(plan["ui_kit_path"], os.path.dirname(path) or ".").replace("\\", "/")
        kit += f"\n(From {path} the kit is at '{href}' - but if the server serves static files under a URL prefix such as /static/, use that URL.)"
    head = f"""You are a senior full-stack engineer and product designer building this project:
"{request[:1500]}"

PROJECT CONTRACT - follow it exactly:
{_contract(plan)}

{DESIGN_SHORT}

{kit}
"""
    tail = f"""
{"CURRENT CONTENT of " + path + ":" + chr(10) + current if current is not None else ""}

TASK: {task}

Output ONLY the complete raw content of {path} - no markdown fences, no commentary."""
    room = min(budget, MAX_PROMPT_TOKENS * 3.3 - len(head) - len(tail))
    related = _related_context(plan, path, contents, int(room)) if room > 300 else ""
    middle = ("FILES ALREADY WRITTEN that this file must agree with (exact routes, field names, element ids, "
              "function names):\n" + related + "\n") if related else ""
    return head + middle + tail


def _generate(request, plan, path, contents, task, current=None, temperature=0.35):
    """Writes one file, shrinking the context if the free tier says the prompt is too big."""
    for budget in (10000, 5000, 1500, 0):
        prompt = _file_prompt(request, plan, path, contents, task, current, budget)
        info = {}
        try:
            text = _strip_code_fence(_paced(prompt, temperature, info=info))
        except PromptTooLarge:
            continue
        # The model ran out of reply length mid-file: ask it to carry on from where it stopped.
        for _ in range(3):
            if not info.get("truncated"):
                break
            info = {}
            complete = _complete_lines(text)
            tail = "\n".join(complete[-40:])
            more = _paced(f"You were writing the file {path} for this project and your output was cut off. "
                          f"Here are the LAST complete lines you wrote:\n\n{tail}\n\nContinue with the line that comes "
                          "right AFTER the last line above - do not repeat lines, do not restart the file, no markdown "
                          "fences, no commentary. Finish the file completely.", temperature, info=info)
            text = _stitch(complete, _strip_code_fence(more))
        return text
    raise RuntimeError(f"{path} is too large to rewrite on the free model")


def _complete_lines(text):
    """The file's lines minus a final line that was cut off part-way."""
    lines = text.split("\n")
    if lines and not text.endswith("\n"):
        lines.pop()
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def _stitch(lines, more):
    """Joins a continuation onto a cut-off file without duplicating anything:
    if the model repeated lines it had already written (or restarted a
    section), skip past the last line we already have."""
    more_lines = more.split("\n")
    anchors = [l.strip() for l in lines if l.strip()][-2:]   # our last two non-blank lines
    if anchors:
        last = anchors[-1]
        prev_nonblank = None
        for k, line in enumerate(more_lines):
            s = line.strip()
            if s == last and (len(anchors) < 2 or prev_nonblank == anchors[0] or len(last) > 25):
                more_lines = more_lines[k + 1:]
                break
            if s:
                prev_nonblank = s
    return "\n".join(lines + more_lines).rstrip("\n") + "\n"


def _lines_for(path, problems):
    """Line numbers the test problems point at in this file."""
    base = re.escape(os.path.basename(path))
    nums = set()
    for p in problems:
        if os.path.basename(path) not in p.replace("\\", "/"):
            continue
        nums.update(int(n) for n in re.findall(rf"{base}\D{{0,60}}?line (\d+)", p))
        nums.update(int(n) for n in re.findall(rf"{base}:(\d+)", p))
        nums.update(int(n) for n in re.findall(r'File "[^"]*' + base + r'", line (\d+)', p))
    return sorted(nums)


def _edit_fix(request, plan, path, current, problems, hint):
    """Fixes a file with targeted line edits - the model sees numbered lines
    around each error and replaces only those ranges, so the request stays
    small however big the file is."""
    lines = current.split("\n")
    targets = _lines_for(path, problems) or [1]
    shown = set()
    for n in targets:
        shown.update(range(max(1, n - 35), min(len(lines), n + 35) + 1))
    if len(shown) < 120:   # small file or no line info - show more of it
        shown.update(range(1, min(len(lines), 160) + 1))
    excerpt, prev = [], 0
    for n in sorted(shown):
        if n != prev + 1:
            excerpt.append("   ...")
        excerpt.append(f"{n:4d}| {lines[n - 1]}")
        prev = n
    excerpt_text = "\n".join(excerpt)
    while _est_tokens(excerpt_text) > 3000 and len(excerpt) > 60:   # keep the request inside the budget
        excerpt = excerpt[:len(excerpt) * 3 // 4]
        excerpt_text = "\n".join(excerpt)
    prompt = f"""Project: "{request[:600]}"
Tech stack: {plan.get('tech_stack', '')}

The file {path} ({len(lines)} lines) fails its tests:
{chr(10).join('- ' + p[:700] for p in problems[:8])}
{('Hint: ' + hint) if hint else ''}

Numbered excerpt of {path} (other lines omitted):
{excerpt_text}

Fix the ROOT CAUSE with minimal, correct line edits (verify any library API you use really exists).
Watch for duplicated or half-written lines - delete them.
Reply ONLY with JSON:
{{"edits": [{{"start": first_line_number, "end": last_line_number, "replacement": "the new text for lines start..end (may be several lines, or empty to delete)"}}]}}
Line numbers refer to the excerpt above; ranges must not overlap."""
    data = _paced_json(prompt, temperature=0.1, expect=2200, default={})
    edits = []
    for e in (data or {}).get("edits") or []:
        try:
            s, t = int(e["start"]), int(e["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if 1 <= s <= t <= len(lines):
            edits.append((s, t, str(e.get("replacement") or "")))
    if not edits:
        return None
    for s, t, rep in sorted(edits, reverse=True):
        lines[s - 1:t] = rep.split("\n") if rep else []
    return "\n".join(lines)


def _fix(request, plan, path, contents, problems, hint, temperature=0.2):
    """Rewrites the whole file when it fits the budget (best quality), otherwise patches it with line edits."""
    current = contents.get(path, "")
    task = ("Fix these test failures at their root cause (verify the real API of any library you use - don't invent methods):\n"
            + "\n".join("- " + p[:900] for p in problems[:12])
            + (f"\nIn this file specifically: {hint}" if hint else "")
            + "\nRemove any duplicated or half-written lines. Keep everything that already works, and keep the same "
              "quality and design. Return the whole corrected file.")
    if current and _est_tokens(current) * 2 + 2500 > MAX_PROMPT_TOKENS + EXPECTED_REPLY:
        patched = _edit_fix(request, plan, path, current, problems, hint)
        if patched is not None:
            return patched
    return _generate(request, plan, path, contents, task, current=current or None, temperature=temperature)


def _write(project_dir, contents):
    for rel, body in contents.items():
        full = os.path.join(project_dir, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8", newline="\n") as f:
            f.write(body)


def _files_in_errors(contents, problems):
    """Project files that error output points at (tracebacks, 'file.js:12'),
    innermost frame first - library frames (.venv, node_modules) are skipped."""
    found = []
    frame_re = re.compile(r'File "([^"]+)", line \d+|([A-Za-z]:[\\/][^\s:"\']+?\.\w+):\d+|(?<![\w/\\])([\w./\\-]+\.(?:py|js|mjs|ts|tsx|jsx|html|css|json|cs|go))\b')
    for p in problems:
        frames = [next(g for g in m.groups() if g) for m in frame_re.finditer(p)]
        for raw in reversed(frames):   # Python tracebacks list the failing line last
            norm = raw.replace("\\", "/")
            if any(f"/{d}/" in f"/{norm}" for d in build_verify.SKIP_DIRS):
                continue
            for key in contents:
                if norm == key or norm.endswith("/" + key):
                    if key not in found:
                        found.append(key)
                    break
    return found


def _choose_files_to_fix(request, plan, contents, problems):
    named = _files_in_errors(contents, problems)
    listing = "\n".join(f"- {f['path']}: {f.get('purpose', '')}" for f in plan["files"])
    data = _paced_json(f"""A project built for "{request[:800]}" failed its tests.

Files:
{listing}

Test problems:
{chr(10).join('- ' + p[:900] for p in problems[:12])}

{('The error output points at these project files: ' + ', '.join(named)) if named else ''}

Which files must change to fix ALL of these at the root cause? Think about which file
actually contains the bug (e.g. a 404 on /api/x means the backend lacks the route OR the
frontend calls the wrong path - check the contract; a missing package means the manifest).
Reply ONLY with JSON: {{"files": [{{"path": "...", "fix": "what to change in this file"}}]}} - at most 4 files.""",
        default={})
    hints = {}
    for item in (data or {}).get("files") or []:
        p = (item.get("path") or "").replace("\\", "/").lstrip("/")
        if p:
            hints.setdefault(p, item.get("fix") or "")
    # Files the errors actually point at always get fixed (the model sometimes
    # blames the wrong file); the model's extra picks follow.
    order = named + [p for p in hints if p not in named]
    chosen = [(p, hints.get(p, "")) for p in order if p in contents or p in hints]
    if not chosen:
        chosen = [(plan["files"][0]["path"], "")]
    return chosen[:4]


def native_build(request, project_dir, on_progress=print):
    on_progress("planning the app and its design")
    try:
        plan = _plan(request) or _plan(request)   # one retry - free models occasionally return junk
    except DailyLimitReached as e:
        return {"ok": False, "error": f"{e}. (Signing in to Claude Code avoids this limit entirely.)"}
    if not plan:
        return {"ok": False, "error": "I couldn't plan that out - can you describe it a bit differently?"}
    name = _slug(plan.get("project_name") or os.path.basename(project_dir))

    contents = {}
    if plan.get("ui_kit_path"):
        with open(KIT_CSS, encoding="utf-8") as f:
            contents[plan["ui_kit_path"]] = f.read()
    for f in plan["files"]:
        on_progress(f"writing {f['path']}")
        task = (f"Write the complete {f['path']} - {f.get('purpose', '')}. Production quality, fully working, matching "
                "the contract and the files above exactly. Keep it under ~220 lines: concise, no long comment banners.")
        if os.path.basename(f["path"]) in DEPENDENCY_FILENAMES:
            task += (" List exactly the third-party packages the code above imports - nothing it doesn't use. "
                     "Use current releases: minimum versions only (e.g. fastapi>=0.115), NO upper caps or old pins - "
                     f"it must install from ready-made wheels on Python {sys.version_info.major}.{sys.version_info.minor} "
                     "and the latest Node.js. No comments.")
        contents[f["path"]] = _generate(request, plan, f["path"], contents, task)
    _write(project_dir, contents)
    _save_meta(project_dir, {"request": request, "engine": "aura", "plan": plan})
    return _verify_and_fix(request, plan, contents, project_dir, on_progress, name)


def _verify_and_fix(request, plan, contents, project_dir, on_progress, name):
    """Test -> pick the guilty files -> rewrite them -> re-test, until clean."""
    env, problems = build_verify.prepare_env(project_dir, on_progress)
    result = None
    for round_no in range(MAX_FIX_ROUNDS + 1):
        on_progress("testing it" if round_no == 0 else f"re-testing (fix round {round_no})")
        result = build_verify.verify(project_dir, plan.get("run"), log=on_progress, env=env)
        result["problems"] = problems + result["problems"]
        problems = []
        for p in result["problems"][:6]:
            on_progress("problem: " + p.splitlines()[0][:150])
        if result["ok"] or round_no == MAX_FIX_ROUNDS:
            break
        to_fix = _choose_files_to_fix(request, plan, contents, result["problems"])
        for path, fix in to_fix:
            on_progress(f"fixing {path}")
            try:
                contents[path] = _fix(request, plan, path, contents, result["problems"], fix)
            except DailyLimitReached:
                raise
            except Exception as e:   # one stubborn file mustn't kill the whole build
                on_progress(f"couldn't fix {path} this round ({str(e)[:80]})")
        _write(project_dir, contents)
        if any(os.path.basename(p) in DEPENDENCY_FILENAMES for p, _ in to_fix):
            env, problems = build_verify.prepare_env(project_dir, on_progress)   # packages changed - reinstall

    run = plan.get("run") or {}
    kind = (run.get("kind") or plan.get("kind") or "other").lower()
    return {
        "ok": result["ok"],
        "project_name": name,
        "summary": plan.get("summary", ""),
        "run_command": run.get("start") if kind != "static" else None,
        "url": f"http://localhost:{run.get('port')}{run.get('path') or '/'}" if kind == "web" and run.get("port") else None,
        "open_file": (run.get("path") or "index.html").lstrip("/") if kind == "static" else None,
        "verified": result["ok"],
        "problems": result["problems"],
        "instructions": plan.get("run_instructions", ""),
    }


# ------------------------------------------------------------------ changing an existing project
META_FILE = os.path.join(".aura", "build.json")


def _save_meta(project_dir, meta):
    path = os.path.join(project_dir, META_FILE)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        old = _load_meta(project_dir)
        old.update(meta)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(old, f, indent=1)
    except Exception as e:
        print(f"  (couldn't save build info: {e})")


def _load_meta(project_dir):
    try:
        with open(os.path.join(project_dir, META_FILE), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _read_sources(project_dir, limit=60):
    contents = {}
    for full in build_verify._source_files(project_dir, {
            ".py", ".js", ".mjs", ".ts", ".tsx", ".jsx", ".vue", ".svelte", ".html", ".htm", ".css", ".json",
            ".md", ".txt", ".toml", ".cs", ".csproj", ".go", ".rs", ".java", ".rb", ".php", ".sql", ".yml", ".yaml"}):
        rel = os.path.relpath(full, project_dir).replace("\\", "/")
        if rel in ("package-lock.json", "CLAUDE.md") or os.path.getsize(full) > 200_000:
            continue
        with open(full, encoding="utf-8", errors="replace") as f:
            contents[rel] = f.read()
        if len(contents) >= limit:
            break
    return contents


def _plan_for_existing(project_dir, meta, contents):
    """The saved plan, or (for projects Claude Code built) a minimal one rebuilt from the files."""
    if meta.get("plan"):
        plan = dict(meta["plan"])
        known = {f["path"] for f in plan["files"]}
        plan["files"] = plan["files"] + [{"path": p, "purpose": ""} for p in contents if p not in known and p != plan.get("ui_kit_path")]
        return plan
    result = meta.get("result") or {}
    url = result.get("url") or ""
    port = re.search(r":(\d+)", url)
    run = {"kind": "web" if url and result.get("run_command") else ("static" if result.get("open_file") else "other"),
           "start": result.get("run_command"), "port": int(port.group(1)) if port else None,
           "path": urlparse_path(url), "smoke": []}
    if run["kind"] == "static":
        run["path"] = "/" + result["open_file"]
    return {"summary": result.get("summary", ""), "tech_stack": "as already used in the existing files",
            "files": [{"path": p, "purpose": ""} for p in contents], "ui_kit_path": None, "run": run,
            "project_name": result.get("project_name") or os.path.basename(project_dir)}


def urlparse_path(url):
    m = re.match(r"^https?://[^/]+(/.*)?$", url or "")
    return (m.group(1) if m and m.group(1) else "/")


def native_modify(change, project_dir, on_progress=print):
    meta = _load_meta(project_dir)
    contents = _read_sources(project_dir)
    if not contents:
        return {"ok": False, "error": "I couldn't find any source files in that project."}
    plan = _plan_for_existing(project_dir, meta, contents)
    request = f"{meta.get('request', '')}\n\nRequested change: {change}".strip()
    name = plan.get("project_name") or os.path.basename(project_dir)

    on_progress("working out what to change")
    picks = _paced_json(f"""An existing project needs this change: "{change}"

Files:
{chr(10).join(f"- {p} ({len(c)} chars)" for p, c in contents.items())}

Which files must be edited or created to make this change properly (frontend AND backend if both are affected)?
Reply ONLY with JSON: {{"files": [{{"path": "...", "change": "what to do in this file"}}]}} - at most 6 files.""",
                        default={})
    targets = [((i.get("path") or "").replace("\\", "/").lstrip("/"), i.get("change") or change)
               for i in (picks or {}).get("files") or [] if i.get("path")][:6]
    if not targets:
        return {"ok": False, "error": "I couldn't work out which files that change touches."}
    for path, what in targets:
        on_progress(f"editing {path}")
        if not any(f["path"] == path for f in plan["files"]):
            plan["files"].append({"path": path, "purpose": what})
        task = (f'Apply this change requested by the user: "{change}". In this file: {what}. '
                "Keep everything else working exactly as before, match the other files, and keep (or raise) the design quality. "
                "Return the whole file.")
        try:
            contents[path] = _generate(request, plan, path, contents, task, current=contents.get(path), temperature=0.3)
        except Exception:   # too big to rewrite whole on the free model - patch it instead
            patched = _edit_fix(request, plan, path, contents.get(path, ""), [f"Requested change: {what}"], change)
            if patched is None:
                on_progress(f"couldn't change {path}")
                continue
            contents[path] = patched
    _write(project_dir, {p: contents[p] for p, _ in targets if p in contents})
    _save_meta(project_dir, {"changes": meta.get("changes", []) + [change]})
    return _verify_and_fix(request, plan, contents, project_dir, on_progress, name)


def modify_project(change):
    """Changes / fixes / extends the most recently built project, in the background."""
    change = (change or "").strip()
    try:
        with open(BUILD_STATE_FILE, encoding="utf-8") as f:
            last = json.load(f)
    except Exception:
        last = {}
    project_dir = last.get("path")
    if not project_dir or not os.path.isdir(project_dir):
        return "I don't have a recent project to change - ask me to build something first."
    if not change:
        return f"What would you like me to change in '{last.get('project_name')}'?"
    if not _build_lock.acquire(blocking=False):
        return f"I'm still working on '{_current_build['name']}' - I'll do this right after."

    engine = "aura" if SELF_BUILD_RE.search(change) or not claude_builder.available() else "claude"
    change = SELF_BUILD_RE.sub("", change).strip(" ,.")
    name = os.path.basename(project_dir)
    _current_build["name"] = name
    _save_build_state(name, "building", project_dir, "starting the change", engine)

    def worker():
        try:
            _run_build(change, project_dir, engine, modify=True)
        finally:
            _current_build["name"] = None
            _build_lock.release()

    threading.Thread(target=worker, daemon=True).start()
    return f"On it - updating '{name}': {change}. I'll test it again and tell you when it's done."


# ------------------------------------------------------------------ after the build
def _open_in_vscode(project_dir):
    # -n = its own window with the project as the workspace (Live Preview and
    # workspace trust only work for files inside the open folder).
    try:
        subprocess.Popen(f'code -n "{project_dir}"', shell=True)
    except Exception:
        try:
            os.startfile(project_dir)
        except Exception:
            pass


START_BAT = r"""@echo off
rem Starts {name}. Double-click this file (or press Ctrl+Shift+B in VS Code).
rem Don't open the HTML files directly - the app's server builds the pages.
title {name}
cd /d "%~dp0"
if exist requirements.txt if not exist .venv\Scripts\python.exe (
  echo Setting up Python packages - first run only...
  python -m venv .venv && .venv\Scripts\python -m pip install -r requirements.txt
)
if exist package.json if not exist node_modules (
  echo Installing npm packages - first run only...
  call npm install
)
if exist .venv\Scripts\activate.bat call .venv\Scripts\activate.bat
{open_browser}
{run}
"""

WAIT_THEN_OPEN = ('start "" /b powershell -NoProfile -WindowStyle Hidden -Command "'
                  "$c=New-Object Net.Sockets.TcpClient; for($i=0;$i -lt 120;$i++){{try{{$c.Connect('127.0.0.1',{port});break}}"
                  "catch{{Start-Sleep -Milliseconds 500}}}}; Start-Process '{url}'\"")


def _add_launchers(project_dir, result):
    """start.bat + a VS Code task, so the app is always started properly (server
    running, browser at the right address) instead of opening index.html directly."""
    name = result.get("project_name") or os.path.basename(project_dir)
    cmd, url, open_file = result.get("run_command"), result.get("url"), result.get("open_file")
    if not (cmd or open_file):
        return None
    port = re.search(r":(\d+)", url or "")
    if cmd:
        opener = WAIT_THEN_OPEN.format(port=port.group(1), url=url) if (url and port) else ""
        run = cmd
    else:
        opener, run = "", f'start "" "{open_file}"'
    bat = os.path.join(project_dir, "start.bat")
    with open(bat, "w", encoding="utf-8", newline="\r\n") as f:
        f.write(START_BAT.format(name=name, open_browser=opener, run=run))

    vscode = os.path.join(project_dir, ".vscode")
    os.makedirs(vscode, exist_ok=True)
    with open(os.path.join(vscode, "tasks.json"), "w", encoding="utf-8") as f:
        json.dump({"version": "2.0.0", "tasks": [{
            "label": f"Run {name}", "type": "shell", "command": "${workspaceFolder}\\start.bat",
            "group": {"kind": "build", "isDefault": True}, "isBackground": bool(cmd), "problemMatcher": []}]}, f, indent=2)

    readme = os.path.join(project_dir, "README.md")
    note = ("> **How to run:** double-click `start.bat` (or press `Ctrl+Shift+B` in VS Code)."
            + (f" It starts the server and opens {url} in your browser - don't open the HTML files directly,"
               " the server builds those pages." if cmd else "") + "\n\n")
    try:
        old = open(readme, encoding="utf-8").read() if os.path.exists(readme) else f"# {name}\n\n"
        if "start.bat" not in old:
            first_break = old.find("\n") + 1 if old.startswith("#") else 0
            with open(readme, "w", encoding="utf-8") as f:
                f.write(old[:first_break] + "\n" + note + old[first_break:])
    except Exception:
        pass
    return bat


def _launch(project_dir, result):
    """Starts the finished app (its start.bat) in its own window; it opens the browser itself."""
    try:
        bat = _add_launchers(project_dir, result)
        if bat:
            subprocess.Popen(f'start "{result.get("project_name", "app")}" "{bat}"', cwd=project_dir, shell=True)
    except Exception as e:
        print(f"  (couldn't launch the app: {e})")


def _announce(text, title, kind="focus"):
    print(text)
    try:
        import face_window
        face_window.show_toast(title, text, kind)
    except Exception:
        pass
    try:
        from voice import speak
        speak(text)
    except Exception:
        pass


def _run_build(request, project_dir, engine, modify=False):
    name = os.path.basename(project_dir)
    started = time.time()

    def progress(step):
        print(f"  [{name}] {step}")
        _save_build_state(name, "building", project_dir, step, engine)

    try:
        if engine == "claude":
            result = (claude_builder.modify(request, project_dir, progress) if modify
                      else claude_builder.build(request, project_dir, progress))
            if result.get("ok") and not modify:
                _save_meta(project_dir, {"engine": "claude", "request": request})
            elif not result.get("ok") and not modify and not [f for f in os.listdir(project_dir) if f != "CLAUDE.md"]:
                engine = "aura"
                progress("Claude Code couldn't run - switching to my own builder")
                result = native_build(request, project_dir, progress)
        else:
            result = native_modify(request, project_dir, progress) if modify else native_build(request, project_dir, progress)
    except Exception as e:
        print(f"  (build crashed: {e!r})")
        result = {"ok": False, "error": str(e)}

    # Remember how to run it (a later change may not restate it), for either engine.
    saved = _load_meta(project_dir).get("result") or {}
    for key in ("run_command", "url", "open_file", "project_name", "summary"):
        if not result.get(key) and saved.get(key):
            result[key] = saved[key]
    if result.get("ok"):
        _save_meta(project_dir, {"result": {k: result.get(k) for k in ("project_name", "summary", "run_command", "url", "open_file")}})
    name = result.get("project_name") or name
    minutes = max(1, round((time.time() - started) / 60))
    _open_in_vscode(project_dir)

    if result.get("ok") and result.get("verified", True):
        _save_build_state(name, "ok", project_dir, "", engine)
        _launch(project_dir, result)
        did = "updated" if modify else "built"
        _announce(f"'{name}' is ready - {did} and tested in about {minutes} minute{'s' if minutes != 1 else ''}. "
                  f"{result.get('summary', '')} I've opened it for you, and the code is in VS Code.", "Build complete")
    elif result.get("ok"):
        _save_build_state(name, "untested", project_dir, "", engine)
        _launch(project_dir, result)
        _announce(f"'{name}' is built. {result.get('summary', '')} I couldn't fully test it, so give it a try - it's open in VS Code.",
                  "Build complete")
    elif result.get("problems") is not None and os.listdir(project_dir):
        _save_build_state(name, "error", project_dir, (result.get("problems") or [""])[0][:200], engine)
        try:
            _add_launchers(project_dir, result)   # so it can still be started properly to look at
        except Exception:
            pass
        first = (result.get("problems") or ["unknown problem"])[0].splitlines()[0][:160]
        _announce(f"I built '{name}' but it still has a problem I couldn't fix: {first}. It's open in VS Code - "
                  f"say 'fix my last project' or tell me what to change.", "Build needs attention", "reminder")
    else:
        _save_build_state(name, "error", project_dir, (result.get("error") or "")[:200], engine)
        _announce(f"The build of '{name}' failed: {(result.get('error') or 'unknown error')[:200]}", "Build failed", "reminder")


def build_project(request, max_fix_attempts=None):
    """Starts building in the background and returns straight away with what's happening."""
    request = (request or "").strip()
    if not request:
        return "What would you like me to build?"
    if not _build_lock.acquire(blocking=False):
        return f"I'm still building '{_current_build['name']}' - I'll tell you as soon as it's done, then I can start this one."

    engine = "aura" if SELF_BUILD_RE.search(request) or not claude_builder.available() else "claude"
    request = SELF_BUILD_RE.sub("", request).strip(" ,.")
    project_dir = _unique_dir(_project_name(request))
    name = os.path.basename(project_dir)
    _current_build["name"] = name
    _save_build_state(name, "building", project_dir, "starting", engine)

    def worker():
        try:
            _run_build(request, project_dir, engine)
        finally:
            _current_build["name"] = None
            _build_lock.release()

    threading.Thread(target=worker, daemon=True).start()
    who = "with Claude Code" if engine == "claude" else "with my own builder"
    return (f"On it - building '{name}' {who}. It'll take a few minutes; I'll design it, build it, "
            f"test it in a browser and fix anything broken. Keep using me meanwhile - I'll tell you when it's ready.")
