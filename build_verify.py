# build_verify.py
# Actually TESTS a project AURA built, the way a person would: installs its
# dependencies (in an isolated .venv / node_modules), checks every source
# file compiles, starts the app, calls its API routes, then opens it in a
# real (headless Edge) browser - catching console errors, failed requests,
# "undefined"/"NaN" on screen, broken mobile layout - and tries the main
# form. Returns a list of concrete problems for the builder to fix, plus a
# screenshot.
#
# Also a command-line tool (Claude Code uses it to check its own work):
#   python build_verify.py --url http://localhost:5000 --shot shot.png
#   python build_verify.py --static path/to/site --shot shot.png
import argparse
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

SKIP_DIRS = {".venv", "venv", "node_modules", ".git", "__pycache__", "dist", "build", ".next", "bin", "obj", ".aura"}
# Signs of broken rendering on screen, incl. raw template tags ({% block %}, {{ name }}) that
# mean a template is being served as a plain file instead of rendered by the server.
SCREEN_JUNK = re.compile(r"\bundefined\b|\bNaN\b|\[object Object\]|\{\{\s*[\w.]+\s*\}\}|\{%\s*\w+")
INSTALL_TIMEOUT = 420
START_TIMEOUT = 45


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _port_open(port):
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _run(cmd, cwd, env, timeout):
    """Runs a shell command; returns (ok, combined output)."""
    try:
        r = subprocess.run(cmd, cwd=cwd, env=env, shell=True, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        return r.returncode == 0, ((r.stdout or "") + "\n" + (r.stderr or "")).strip()
    except subprocess.TimeoutExpired:
        return False, f"`{cmd}` timed out after {timeout}s"
    except Exception as e:
        return False, f"`{cmd}` couldn't run: {e}"


def _kill_tree(proc):
    if proc and proc.poll() is None:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)


def _source_files(project_dir, exts):
    for root, dirs, files in os.walk(project_dir):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if os.path.splitext(f)[1].lower() in exts:
                yield os.path.join(root, f)


def _tail(text, n=2500):
    text = (text or "").strip()
    return text if len(text) <= n else "…" + text[-n:]


# ------------------------------------------------------------------ environment
def prepare_env(project_dir, log=print):
    """Isolated runtime for the project: a .venv for Python (so generated apps
    never clutter AURA's own Python) and node_modules for Node. Returns
    (env, problems)."""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    problems = []

    has_py = any(True for _ in _source_files(project_dir, {".py"}))
    if has_py:
        venv = os.path.join(project_dir, ".venv")
        if not os.path.exists(os.path.join(venv, "Scripts", "python.exe")):
            log("  creating a Python environment for it...")
            ok, out = _run(f'"{sys.executable}" -m venv .venv', project_dir, env, 180)
            if not ok:
                problems.append(f"Couldn't create a virtualenv: {_tail(out, 600)}")
        env["VIRTUAL_ENV"] = venv
        env["PATH"] = os.path.join(venv, "Scripts") + os.pathsep + env.get("PATH", "")
        if os.path.exists(os.path.join(project_dir, "requirements.txt")):
            log("  installing Python packages...")
            ok, out = _run("python -m pip install -r requirements.txt --disable-pip-version-check -q", project_dir, env, INSTALL_TIMEOUT)
            if not ok:
                problems.append(f"`pip install -r requirements.txt` failed:\n{_tail(out, 1500)}")

    if os.path.exists(os.path.join(project_dir, "package.json")):
        log("  installing npm packages...")
        ok, out = _run("npm install --no-audit --no-fund --loglevel=error", project_dir, env, INSTALL_TIMEOUT)
        if not ok:
            problems.append(f"`npm install` failed:\n{_tail(out, 1500)}")

    if any(True for _ in _source_files(project_dir, {".csproj"})):
        log("  building the .NET project...")
        ok, out = _run("dotnet build -nologo -v q", project_dir, env, INSTALL_TIMEOUT)
        if not ok:
            problems.append(f"`dotnet build` failed:\n{_tail(out, 2000)}")
    return env, problems


def static_checks(project_dir, env):
    """Syntax-checks every Python and JavaScript file."""
    problems = []
    for path in _source_files(project_dir, {".py"}):
        ok, out = _run(f'python -m py_compile "{path}"', project_dir, env, 30)
        if not ok:
            problems.append(f"{os.path.relpath(path, project_dir)} has a syntax error:\n{_tail(out, 800)}")
    for path in _source_files(project_dir, {".js", ".mjs", ".cjs"}):
        ok, out = _run(f'node --check "{path}"', project_dir, env, 30)
        if not ok and "Cannot find" not in out:
            problems.append(f"{os.path.relpath(path, project_dir)} has a syntax error:\n{_tail(out, 800)}")
    return problems


# ------------------------------------------------------------------ http
def _http(method, url, body=None, timeout=15):
    data = None
    headers = {"Accept": "application/json, text/html;q=0.9, */*;q=0.8"}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read(4000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), e.read(4000).decode("utf-8", "replace")
    except Exception as e:
        return None, "", str(e)


def http_checks(base, page_path, smoke):
    problems = []
    status, ctype, text = _http("GET", base + (page_path or "/"))
    if status != 200:
        problems.append(f"GET {page_path or '/'} returned {status}: {text[:300]}")
    elif "html" not in ctype.lower():
        problems.append(f"GET {page_path or '/'} should return the HTML page but returned {ctype or 'no content type'}")
    for s in smoke or []:
        method = (s.get("method") or "GET").upper()
        path = s.get("path") or "/"
        if "{" in path or ":" in path.split("/")[-1]:
            continue  # route with a parameter - the browser test covers it
        status, ctype, text = _http(method, base + path, s.get("body") if method in ("POST", "PUT", "PATCH") else None)
        if status is None:
            problems.append(f"{method} {path} failed: {text[:200]}")
        elif status >= 500:
            problems.append(f"{method} {path} crashed the server ({status}): {text[:500]}")
        elif status == 404:
            problems.append(f"{method} {path} doesn't exist (404) - the frontend/plan expects it")
        elif status == 405:
            problems.append(f"{method} {path} is not allowed (405) - route exists but not for {method}")
    return problems


# ------------------------------------------------------------------ browser
def browser_check(url, shot_path=None, interact=True):
    """Loads the page in headless Edge. Returns (problems, info)."""
    problems, info = [], {}
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return [], {"skipped": "playwright not installed"}

    origin = re.match(r"^(https?://[^/]+|file://)", url)
    origin = origin.group(1) if origin else ""
    errors, failed = [], []
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="msedge", headless=True)
        except Exception:
            try:
                browser = p.chromium.launch(headless=True)
            except Exception as e:
                return [], {"skipped": f"no browser available: {e}"}
        try:
            page = browser.new_page(viewport={"width": 1366, "height": 900})
            page.on("console", lambda m: m.type == "error" and errors.append(m.text[:400]))
            page.on("pageerror", lambda e: errors.append(f"Uncaught {str(e)[:400]}"))
            page.on("requestfailed", lambda r: failed.append(f"{r.method} {r.url} ({r.failure})"))
            page.on("response", lambda r: (r.status >= 400 and r.url.startswith(origin) and "favicon" not in r.url
                                           and failed.append(f"{r.request.method} {r.url} -> HTTP {r.status}")))
            try:
                page.goto(url, wait_until="networkidle", timeout=30000)
            except Exception as e:
                problems.append(f"The page didn't finish loading: {str(e)[:300]}")
            page.wait_for_timeout(900)

            text = page.evaluate("() => document.body ? document.body.innerText : ''") or ""
            info["title"] = page.title()
            info["text_sample"] = text[:600]
            has_canvas = page.evaluate("() => !!document.querySelector('canvas, svg, img, video')")
            if len(text.strip()) < 15 and not has_canvas:
                problems.append("The page renders blank (almost no visible content) - likely a JavaScript error or missing render call.")
            junk = sorted(set(m.group(0) for m in SCREEN_JUNK.finditer(text)))
            if junk:
                problems.append(f"The page shows broken values on screen: {', '.join(junk)} - data isn't being read/rendered correctly.")
            styled = page.evaluate("""() => { const b = getComputedStyle(document.body);
                return !/Times New Roman|serif$/i.test(b.fontFamily) || document.styleSheets.length > 0; }""")
            if not styled:
                problems.append("The page looks unstyled (default browser fonts) - the stylesheet is missing or not linked.")
            if shot_path:
                os.makedirs(os.path.dirname(os.path.abspath(shot_path)), exist_ok=True)
                page.screenshot(path=shot_path)
                info["screenshot"] = shot_path

            if interact:
                before = len(errors) + len(failed)
                info["form_test"] = _try_main_form(page)
                page.wait_for_timeout(1200)
                if len(errors) + len(failed) > before:
                    info["form_test"] += " -> produced errors"

            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_timeout(400)
            overflow = page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
            if overflow and overflow > 4:
                problems.append(f"On a phone-sized screen (390px) the page scrolls sideways by {overflow}px - layout isn't responsive.")
            if shot_path:
                page.screenshot(path=shot_path.replace(".png", "-mobile.png"))
        finally:
            browser.close()

    for e in dict.fromkeys(errors):
        problems.append(f"Browser console error: {e}")
    for f in dict.fromkeys(failed):
        problems.append(f"Failed request: {f}")
    return problems, info


def _try_main_form(page):
    """Fills the first visible form/input row with sample data and submits it,
    like a user trying the app for the first time."""
    try:
        inputs = page.locator("input:visible:not([type=hidden]):not([type=checkbox]):not([type=radio]):not([type=file]), textarea:visible")
        if inputs.count() == 0:
            return "no form to try"
        filled = 0
        for i in range(min(inputs.count(), 6)):
            el = inputs.nth(i)
            kind = (el.get_attribute("type") or "text").lower()
            sample = {"email": "test@example.com", "number": "3", "date": "2026-10-01", "time": "09:30",
                      "url": "https://example.com", "password": "Test12345!", "tel": "5551234",
                      "color": None, "range": None, "search": None}.get(kind, "Sample item")
            if sample is None:
                continue
            try:
                el.fill(sample, timeout=1500)
                filled += 1
            except Exception:
                pass
        if not filled:
            return "no fillable inputs"
        form = page.locator("form:visible")
        if form.count():
            btn = form.first.locator("button[type=submit], button:not([type]), input[type=submit]")
            if btn.count():
                btn.first.click(timeout=2000)
                return f"filled {filled} field(s) and submitted the form"
        inputs.first.press("Enter")
        return f"filled {filled} field(s) and pressed Enter"
    except Exception as e:
        return f"form test skipped ({str(e)[:80]})"


# ------------------------------------------------------------------ static sites
class _QuietStaticServer:
    def __init__(self, directory):
        import http.server
        import functools
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=directory)
        handler.log_message = lambda *a, **k: None
        self.port = _free_port()
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", self.port), handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()


# ------------------------------------------------------------------ main entry
def verify(project_dir, run, log=print, env=None, shot_path=None):
    """Runs every check that applies. `run` = the plan's run spec:
    {"kind": "web"|"static"|"cli"|"other", "start": "python app.py", "port": 5000,
     "path": "/", "smoke": [{"method": "GET", "path": "/api/items"}], "check": "python main.py --help"}
    Returns {"ok": bool, "problems": [...], "logs": str, "screenshot": path|None, "info": {...}}"""
    run = run or {}
    kind = (run.get("kind") or "other").lower()
    shot_path = shot_path or os.path.join(project_dir, ".aura", "screenshot.png")
    problems, logs, info = [], "", {}

    if env is None:
        env, env_problems = prepare_env(project_dir, log)
        problems += env_problems
    log("  checking the code compiles...")
    problems += static_checks(project_dir, env)
    if problems:
        return {"ok": False, "problems": problems, "logs": logs, "screenshot": None, "info": info}

    if kind == "static":
        entry = run.get("path") or "/index.html"
        server = _QuietStaticServer(project_dir)
        try:
            log("  opening it in a browser...")
            p, info = browser_check(f"http://127.0.0.1:{server.port}{entry}", shot_path)
            problems += p
        finally:
            server.close()

    elif kind == "web" and run.get("start"):
        port = _free_port()
        env = dict(env, PORT=str(port), HOST="127.0.0.1", FLASK_DEBUG="0", NODE_ENV="development")
        declared = run.get("port")
        log(f"  starting it: {run['start']}")
        out_path = os.path.join(project_dir, ".aura", "server.log")
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        out = open(out_path, "w", encoding="utf-8", errors="replace")
        proc = subprocess.Popen(run["start"], cwd=project_dir, env=env, shell=True, stdout=out, stderr=subprocess.STDOUT)
        try:
            live = None
            deadline = time.time() + START_TIMEOUT
            while time.time() < deadline and proc.poll() is None:
                for candidate in (port, declared):
                    if candidate and _port_open(int(candidate)):
                        live = int(candidate)
                        break
                if live:
                    break
                time.sleep(0.5)
            if not live:
                time.sleep(0.5)
                out.flush()
                with open(out_path, encoding="utf-8", errors="replace") as f:
                    logs = f.read()
                if proc.poll() is not None:
                    problems.append(f"The server crashed on startup:\n{_tail(logs, 2500)}")
                else:
                    problems.append(f"The server didn't start listening within {START_TIMEOUT}s "
                                    f"(it should listen on the PORT environment variable, default {declared}).\n{_tail(logs, 1500)}")
            else:
                if live != port:
                    problems.append(f"The server ignores the PORT environment variable (listened on {live} instead of {port}) - read PORT from the environment.")
                base = f"http://127.0.0.1:{live}"
                info["url"] = base + (run.get("path") or "/")
                time.sleep(0.6)
                log("  calling its API...")
                problems += http_checks(base, run.get("path") or "/", run.get("smoke"))
                log("  opening it in a browser...")
                p, binfo = browser_check(info["url"], shot_path)
                problems += p
                info.update(binfo)
                time.sleep(0.3)
                if proc.poll() is not None:
                    problems.append("The server crashed while being used.")
        finally:
            _kill_tree(proc)
            out.close()
            try:
                with open(out_path, encoding="utf-8", errors="replace") as f:
                    logs = f.read()
            except Exception:
                pass
            tracebacks = re.findall(r"Traceback \(most recent call last\):.*?(?=\n\S|\Z)", logs, re.S)
            if tracebacks and not any("crashed" in p for p in problems):
                problems.append(f"The server logged an error while handling requests:\n{_tail(tracebacks[-1], 1500)}")

    elif run.get("check") or run.get("start"):
        cmd = run.get("check") or run["start"]
        log(f"  running it: {cmd}")
        try:
            r = subprocess.run(cmd, cwd=project_dir, env=env, shell=True, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=25, stdin=subprocess.DEVNULL)
            logs = (r.stdout or "") + (r.stderr or "")
            if r.returncode != 0 and "EOFError" not in logs:
                problems.append(f"`{cmd}` exited with code {r.returncode}:\n{_tail(logs, 2500)}")
        except subprocess.TimeoutExpired:
            pass  # still running (a GUI, game loop or interactive tool) - didn't crash

    return {"ok": not problems, "problems": problems, "logs": _tail(logs, 3000),
            "screenshot": shot_path if os.path.exists(shot_path) else None, "info": info}


def _cli():
    ap = argparse.ArgumentParser(description="Check a running web app (or a static site folder) in a real headless browser.")
    ap.add_argument("--url", help="URL of the running app, e.g. http://localhost:5000")
    ap.add_argument("--static", help="folder of a static site to serve and check")
    ap.add_argument("--entry", default="/index.html", help="entry page for --static")
    ap.add_argument("--shot", default=".aura/screenshot.png", help="where to save the screenshot")
    ap.add_argument("--no-interact", action="store_true", help="don't try filling/submitting the main form")
    a = ap.parse_args()
    if a.static:
        server = _QuietStaticServer(os.path.abspath(a.static))
        url = f"http://127.0.0.1:{server.port}{a.entry}"
    elif a.url:
        server, url = None, a.url
    else:
        ap.error("give --url or --static")
    try:
        problems, info = browser_check(url, os.path.abspath(a.shot), interact=not a.no_interact)
    finally:
        if server:
            server.close()
    print(json.dumps({"ok": not problems, "problems": problems, "info": info}, indent=2))


if __name__ == "__main__":
    _cli()
