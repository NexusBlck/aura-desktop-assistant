# claude_builder.py
# Hands a build to Claude Code (the CLI) - a far stronger coding agent than
# AURA's free model. Claude Code works inside the project folder on its own:
# plans, writes every file, installs packages, runs the app, checks it in a
# real browser with build_verify.py (and LOOKS at the screenshots to polish
# the design), and fixes whatever's wrong - following build_kit/DESIGN.md.
#
# One-time setup: run `claude` in a terminal and sign in. Until then
# available() is False and AURA uses its own builder (autocoder.py).
import json
import os
import shutil
import subprocess
import sys
import threading
import time

AURA_DIR = os.path.dirname(os.path.abspath(__file__))
DESIGN_GUIDE = os.path.join(AURA_DIR, "build_kit", "DESIGN.md")
VERIFIER = os.path.join(AURA_DIR, "build_verify.py")
BUILD_TIMEOUT = 75 * 60          # big apps can take a while
_auth_cache = {"at": 0, "ok": False}

# What Claude Code may do unattended: edit files in the project, look things
# up, and run the usual development tools. Anything else is refused.
ALLOWED_TOOLS = [
    "Read", "Write", "Edit", "MultiEdit", "Glob", "Grep", "LS", "TodoWrite", "WebSearch", "WebFetch",
    *(f"Bash({tool} *)" for tool in (
        "npm", "npx", "node", "pnpm", "yarn", "bun", "deno", "tsc", "vite", "next",
        "python", "py", "pip", "uv", "uvicorn", "flask", "pytest", "./.venv/Scripts/python", ".venv/Scripts/python",
        "dotnet", "go", "cargo", "rustc", "javac", "java", "git", "mkdir", "ls", "cat", "head", "tail",
        "cp", "mv", "touch", "echo", "curl", "sleep", "timeout", "which", "where", "cd", "grep", "find", "wc",
    )),
    f"Bash({sys.executable} *)",
    f"Bash({sys.executable.replace(os.sep, '/')} *)",
]


def _exe():
    """The real claude.exe (the npm .cmd shim mangles long arguments)."""
    shim = shutil.which("claude")
    if not shim:
        return None
    native = os.path.join(os.path.dirname(shim), "node_modules", "@anthropic-ai", "claude-code", "bin", "claude.exe")
    return native if os.path.exists(native) else shim


def available():
    """True if Claude Code is installed AND signed in (checked at most every 5 min)."""
    if time.time() - _auth_cache["at"] < 300:
        return _auth_cache["ok"]
    ok = False
    exe = _exe()
    if exe:
        try:
            r = subprocess.run([exe, "auth", "status"], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=30)
            ok = bool(json.loads(r.stdout or "{}").get("loggedIn"))
        except Exception:
            ok = False
    _auth_cache.update(at=time.time(), ok=ok)
    return ok


def _brief(request):
    with open(DESIGN_GUIDE, encoding="utf-8") as f:
        standards = f.read()
    # Forward slashes: Claude Code runs commands in Git Bash, which eats backslashes.
    py = sys.executable.replace("\\", "/")
    verifier = VERIFIER.replace("\\", "/")
    return f"""# Project brief (from AURA, the user's assistant)

## What to build
{request}

The user asked for this by voice/text to their assistant and will not be
answering questions - make sensible decisions yourself and build the
complete thing. Interpret the request generously: build what they would
be delighted to receive, not the most literal minimum.

## How to work
1. Decide the stack. Use whatever language/framework genuinely fits the
   request (if they named one, use it). For web apps prefer a stack that runs
   with ONE command on Windows. Python projects: use a local virtualenv
   (`python -m venv .venv`, then `.venv/Scripts/python -m pip install -r requirements.txt`).
2. Design first: write the design direction (palette, type, layout) into
   README.md, then build every screen to it.
3. Build it completely - backend, frontend, data, seed data, README.
4. VERIFY IT FOR REAL before finishing:
   - Install dependencies and start the app; fix every error.
   - For anything with a web UI, while the app is running, check it in a real browser:
     `{py} "{verifier}" --url http://localhost:<port> --shot .aura/screenshot.png`
     (for a static site: `{py} "{verifier}" --static . --shot .aura/screenshot.png`).
     It reports console errors, failed requests, broken values, mobile overflow,
     and saves desktop + mobile screenshots. Read the screenshots (they are
     images) and critically judge the design like a senior product designer -
     spacing, alignment, hierarchy, colour, empty states. Improve and re-check
     until it looks genuinely polished and reports zero problems.
   - Stop any server you started before you finish.
5. Finish with a short summary, and then on the LAST line exactly one JSON object:
   {{"project_name": "...", "summary": "one sentence", "run_command": "command to start it from the project folder (or null)", "url": "http://localhost:<port>/ (or null)", "open_file": "file to open instead for static/no-server projects (or null)", "verified": true/false}}

{standards}
"""


def _describe_tool(block):
    name = block.get("name", "")
    inp = block.get("input") or {}
    if name in ("Write", "Edit", "MultiEdit"):
        return f"writing {os.path.basename(inp.get('file_path', '') or '')}"
    if name == "Bash":
        cmd = (inp.get("command") or "").strip().splitlines()[0][:70]
        if "build_verify" in cmd:
            return "testing it in a browser"
        return f"running {cmd}"
    if name in ("WebSearch", "WebFetch"):
        return "looking something up"
    if name == "Read" and str(inp.get("file_path", "")).endswith(".png"):
        return "reviewing the design"
    return None


def _last_json(text):
    for line in reversed((text or "").strip().splitlines()):
        line = line.strip().strip("`")
        if line.startswith("{") and line.endswith("}"):
            try:
                return json.loads(line)
            except Exception:
                continue
    return {}


def build(request, project_dir, on_progress=print):
    """Runs Claude Code in project_dir until the app is built and verified.
    Returns {"ok", "project_name", "summary", "run_command", "url", "open_file", "verified", "error", "cost"}."""
    os.makedirs(project_dir, exist_ok=True)
    with open(os.path.join(project_dir, "CLAUDE.md"), "w", encoding="utf-8") as f:
        f.write(_brief(request))
    return _run(project_dir, on_progress,
                "Build the project described in CLAUDE.md in this folder. Follow every step there, "
                "including verifying it in the browser and polishing the design. End with the JSON line.")


def modify(change, project_dir, on_progress=print):
    """Changes/fixes an existing project (built by either engine) and re-verifies it."""
    brief = os.path.join(project_dir, "CLAUDE.md")
    if not os.path.exists(brief):   # built by AURA's own builder - give Claude the same standards
        with open(brief, "w", encoding="utf-8") as f:
            f.write(_brief("(An existing project - read README.md and the code to understand it.)"))
    return _run(project_dir, on_progress,
                f'This project already exists. The user wants this change: "{change}"\n\n'
                "Read the existing code first, then make the change properly (frontend and backend as needed), "
                "keeping everything else working and the design at least as polished. Then verify it exactly as "
                "CLAUDE.md describes (run it, browser-check it, look at the screenshots) and fix anything broken. "
                "End with the JSON line from CLAUDE.md.")


def _run(project_dir, on_progress, prompt):
    exe = _exe()
    cmd = [exe, "-p", "--output-format", "stream-json", "--verbose",
           "--permission-mode", "acceptEdits", "--max-turns", "250",
           "--allowedTools", *ALLOWED_TOOLS]
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    proc = subprocess.Popen(cmd, cwd=project_dir, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    proc.stdin.write(prompt)
    proc.stdin.close()

    killer = threading.Timer(BUILD_TIMEOUT, lambda: subprocess.run(
        ["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True))
    killer.start()
    final, last_step = None, ""
    try:
        for line in proc.stdout:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("type") == "assistant":
                for block in (event.get("message") or {}).get("content") or []:
                    if block.get("type") == "tool_use":
                        step = _describe_tool(block)
                        if step and step != last_step:
                            last_step = step
                            on_progress(step)
            elif event.get("type") == "result":
                final = event
        proc.wait(timeout=60)
    finally:
        killer.cancel()

    if not final:
        err = (proc.stderr.read() or "").strip()
        return {"ok": False, "error": err[-600:] or "Claude Code stopped without finishing."}
    text = final.get("result") or ""
    info = _last_json(text)
    return {
        "ok": final.get("subtype") == "success" and not final.get("is_error"),
        "project_name": info.get("project_name") or os.path.basename(project_dir),
        "summary": info.get("summary") or (text.strip().splitlines()[0][:200] if text.strip() else ""),
        "run_command": info.get("run_command"),
        "url": info.get("url"),
        "open_file": info.get("open_file"),
        "verified": bool(info.get("verified")),
        "error": None if final.get("subtype") == "success" else (text or final.get("subtype")),
        "cost": final.get("total_cost_usd"),
    }
