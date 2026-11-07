# web_agent.py
# AURA's own browser + an agent that USES it to get you finished answers.
#
# "Find the opening hours on the city library site" -> AURA opens its browser,
# navigates the site, picks dropdown options itself, reads the page and TELLS you
# the answer. No manual steps.
#
# The browser stays open between commands, and AURA remembers what it just did,
# so commands stack for as long as you like:
#   "check train times from A to B"  ->  "and on Sunday?"  ->  "open the cheapest one" ...
#
# How it works: each step AURA reads the page as structure (every link, button,
# dropdown with its options, input, and the visible text/tables), the model picks
# ONE next action, AURA does it, and repeats until it can answer.
#
# Guardrails (always on):
#   * It never clicks anything that submits, saves, deletes, pays, sends, signs
#     out, changes a password, registers/enrols, uploads, applies or requests -
#     it tells you instead. Search/Show/View/Filter-type buttons are fine.
#   * It never types into password fields - if a site needs you to sign in or pass
#     a security check, it asks you to do that yourself in the AURA browser window.
import json
import os
import queue
import re
import threading
import time
from urllib.parse import quote_plus

PROFILE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "browser_data", "aura-web")


class Stop(Exception):
    """Something the user needs to know about - stops the run with a clear message."""


def _is_cloudflare(page):
    try:
        return "Just a moment" in page.title() or page.locator("text=Performing security verification").count() > 0
    except Exception:
        return False


def _is_login(page):
    try:
        return page.locator("input[type=password]").count() > 0
    except Exception:
        return False


def _wait_for_user(page, status, minutes=5):
    """A sign-in page or security check: the user completes it themselves in the AURA window."""
    status("this site needs you - please complete the sign-in or security check in the AURA browser window")
    deadline = time.time() + minutes * 60
    while time.time() < deadline:
        page.wait_for_timeout(1500)
        if not _is_login(page) and not _is_cloudflare(page):
            return
    raise Stop(f"I waited {minutes} minutes but the sign-in or security check wasn't completed.")

MAX_STEPS = 14
TEXT_BUDGET = 4500          # characters of page text shown to the model each step
MAX_ELEMENTS = 70
HISTORY_TURNS = 6           # earlier tasks + answers remembered for follow-ups

RISKY = re.compile(r"\b(submit|save|delete|remove|pay|payment|checkout|purchase|buy|log\s*out|logout|sign\s*out|"
                   r"change\s+password|reset|update|register|enrol|enroll|upload|send|confirm|apply|request|"
                   r"cancel|withdraw|drop|post|publish|accept|agree)\b", re.I)
SAFE_DESPITE_WORDS = re.compile(r"^\s*(search|show|view|filter|go|get|find|load|display|refresh|next|previous|back|"
                                r"details?|open|more|expand|print\s*preview)\b", re.I)

# ------------------------------------------------------------------ reading a page
SNAPSHOT_JS = r"""(maxEls) => {
  const vis = (e) => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
    return r.width > 2 && r.height > 2 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const label = (e) => {
    let t = (e.getAttribute('aria-label') || e.innerText || e.value || e.title || e.placeholder || e.name || '').trim();
    if (!t && e.id) { const l = document.querySelector(`label[for="${e.id}"]`); if (l) t = l.innerText.trim(); }
    if (!t && (e.tagName === 'SELECT' || e.tagName === 'INPUT')) {
      const prev = e.closest('.form-group, td, div')?.querySelector('label, b, strong, h5');
      if (prev) t = prev.innerText.trim(); }
    return t.replace(/\s+/g, ' ').slice(0, 80);
  };
  document.querySelectorAll('[data-aura-id]').forEach(e => e.removeAttribute('data-aura-id'));
  const main = document.querySelector('.content-wrapper, main, #content, .content') || document.body;
  const sel = 'a[href], button, input:not([type=hidden]), select, textarea, [role=button], [onclick]';
  // Content-area elements first, then the rest (menus) - so the useful ones fit the budget.
  const all = [...main.querySelectorAll(sel), ...[...document.querySelectorAll(sel)].filter(e => !main.contains(e))];
  const items = []; let n = 0;
  for (const e of all) {
    if (!vis(e) || items.length >= maxEls) continue;
    const id = ++n; e.setAttribute('data-aura-id', id);
    const tag = e.tagName.toLowerCase(), type = (e.type || '').toLowerCase();
    let line = '';
    if (tag === 'select') {
      const opts = [...e.options].map(o => (o.selected ? '*' : '') + o.text.trim()).filter(Boolean).slice(0, 30);
      line = `select "${label(e)}" options: ${opts.join(' | ')}`;
    } else if (tag === 'a') {
      line = `link "${label(e)}" -> ${(e.getAttribute('href') || '').slice(0, 90)}`;
    } else if (tag === 'input' && ['text','search','email','number','date','tel','url'].includes(type)) {
      line = `input ${type} "${label(e)}" value="${(e.value || '').slice(0, 40)}"`;
    } else if (tag === 'input' && type === 'password') {
      line = `password field (AURA never types here)`;
    } else if (tag === 'input' && (type === 'checkbox' || type === 'radio')) {
      line = `${type} "${label(e)}" ${e.checked ? 'checked' : 'unchecked'}`;
    } else if (tag === 'textarea') {
      line = `textarea "${label(e)}" (${(e.value || '').length} chars)`;
    } else {
      line = `button "${label(e)}"`;
    }
    items.push(`[${id}] ${line}`);
  }
  // Visible text of the main area; tables come out row by row.
  const text = main.innerText.replace(/[ \t]+/g, ' ').replace(/\n{3,}/g, '\n\n').trim();
  return {url: location.href, title: document.title, elements: items, text};
}"""


def _snapshot(page):
    snap = page.evaluate(SNAPSHOT_JS, MAX_ELEMENTS)
    text = snap["text"]
    if len(text) > TEXT_BUDGET:
        text = text[:TEXT_BUDGET] + "\n...(more below - scroll to see it)"
    return snap, (f"URL: {snap['url']}\nTITLE: {snap['title']}\n\nINTERACTIVE ELEMENTS:\n" + "\n".join(snap["elements"])
                  + f"\n\nVISIBLE TEXT:\n{text}")


# ------------------------------------------------------------------ deciding + doing
AGENT_PROMPT = """You are AURA, operating a web browser for the user to get them a FINISHED answer or result.
You see the current page and choose exactly ONE next action. Do the clicking/choosing yourself - the user
does not want to do manual steps.

The user's request: "{task}"
{memory}
Steps taken so far:
{steps}

Current page:
{page}

Rules:
- Work it out like a capable person: navigate menus, pick dropdown options (dates, categories, filters),
  press Search/Show/View, open details, scroll if the answer is further down.
- When a choice isn't specified (e.g. which date or year), use the CURRENT/latest one and say which you used
  in the answer. If the user asks for "all", cover all.
- After choosing options and pressing Show/Search, read the result. If it says no record / shows nothing, try
  the other likely options (e.g. the other categories, the previous year) before giving up.
- Never repeat the exact same action more than twice - if it didn't work, do something different.
- If a page needs the user to sign in, answer asking them to sign in in the AURA browser window, then ask again.
- NEVER click anything that submits, saves, deletes, pays, sends, signs out, changes passwords, registers,
  uploads or applies. If the request needs that, answer explaining what the user should do.
- To look something up on the open web, use "search".
- As soon as you have what's needed, use "answer": give the actual result (numbers, names, dates) in 1-4
  natural spoken sentences - not "I found the page". If something is genuinely impossible, answer saying why.

Reply ONLY with JSON, one of:
{{"action": "click", "id": N, "why": "..."}}
{{"action": "select", "id": N, "option": "exact option text", "why": "..."}}
{{"action": "type", "id": N, "text": "...", "enter": true/false, "why": "..."}}
{{"action": "goto", "url": "https://...", "why": "..."}}
{{"action": "search", "query": "...", "why": "..."}}
{{"action": "scroll", "why": "..."}}
{{"action": "back", "why": "..."}}
{{"action": "answer", "text": "..."}}"""


def _element(page, n):
    loc = page.locator(f'[data-aura-id="{int(n)}"]')
    return loc if loc.count() else None


def _describe(loc):
    return loc.evaluate("e => ({text: (e.innerText || e.value || e.getAttribute('aria-label') || e.title || '').trim().slice(0, 80),"
                        " type: (e.type || '').toLowerCase(), tag: e.tagName.toLowerCase(),"
                        " formAction: e.form ? (e.form.getAttribute('action') || '') : '', href: e.getAttribute('href') || ''})")


def _is_risky(info):
    text = info.get("text") or ""
    if re.search(r"logout|signout|sign-out", info.get("href", ""), re.I):
        return True
    return bool(RISKY.search(text)) and not SAFE_DESPITE_WORDS.search(text)


def _do(page, step):
    """Carries out one action. Returns an observation string for the next step."""
    act = (step.get("action") or "").lower()
    if act in ("click", "select", "type"):
        loc = _element(page, step.get("id", -1))
        if loc is None:
            return f"element [{step.get('id')}] doesn't exist on this page"
        info = _describe(loc)
        if act == "click":
            if _is_risky(info):
                return f"REFUSED to click '{info['text']}' - that could submit/change something. Don't try it again."
            loc.scroll_into_view_if_needed(timeout=3000)
            loc.click(timeout=8000)
        elif act == "select":
            option = str(step.get("option", ""))
            try:
                loc.select_option(label=option, timeout=5000)
            except Exception:
                loc.select_option(value=option, timeout=5000)
        else:
            if info.get("type") == "password":
                return "REFUSED - AURA never types into password fields."
            loc.fill(str(step.get("text", "")), timeout=5000)
            if step.get("enter"):
                loc.press("Enter")
    elif act == "goto":
        url = str(step.get("url", ""))
        if not url.startswith(("http://", "https://")):
            return "only http(s) addresses can be opened"
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
    elif act == "search":
        page.goto("https://duckduckgo.com/html/?q=" + quote_plus(str(step.get("query", ""))),
                  wait_until="domcontentloaded", timeout=45000)
    elif act == "scroll":
        page.mouse.wheel(0, 900)
    elif act == "back":
        page.go_back(wait_until="domcontentloaded", timeout=30000)
    else:
        return f"unknown action '{act}'"
    try:
        page.wait_for_load_state("domcontentloaded", timeout=15000)
    except Exception:
        pass
    page.wait_for_timeout(900)
    return "done"


def run_task(page, task, status, memory=()):
    """The agent loop. Returns the final answer text."""
    from autocoder import _paced, DailyLimitReached   # free-tier-aware model calls
    from ai import extract_json
    mem = ""
    if memory:
        mem = "Earlier in this conversation (for context - the user may refer back to it):\n" + "\n".join(
            f"- asked \"{m['task']}\" -> {m['answer']}" for m in memory[-HISTORY_TURNS:])
    steps = []
    for n in range(MAX_STEPS):
        if _is_login(page) or _is_cloudflare(page):
            _wait_for_user(page, status)
        snap, page_text = _snapshot(page)
        prompt = AGENT_PROMPT.format(task=task, memory=mem, page=page_text,
                                     steps="\n".join(steps[-10:]) or "(none yet)")
        try:
            step = extract_json(_paced(prompt, temperature=0.1, reasoning_effort="low", expect=700))
        except DailyLimitReached as e:
            return f"I've hit the free AI model's daily limit, so I had to stop - {e}."
        except Exception:
            steps.append(f"{n + 1}. (my reply wasn't valid JSON - reply with JSON only)")
            continue
        if not isinstance(step, dict):
            continue
        if step.get("action") == "answer":
            return str(step.get("text") or "").strip() or "I couldn't find that."
        why = str(step.get("why", ""))[:120]
        status(why or step.get("action", ""))
        try:
            result = _do(page, step)
        except Exception as e:
            result = f"failed: {str(e).splitlines()[0][:150]}"
        desc = {k: v for k, v in step.items() if k != "why"}
        key = json.dumps(desc, sort_keys=True)
        repeats = sum(1 for s in steps if key in s)
        if repeats >= 2:
            result += " | WARNING: you've now done this exact action 3 times - stop repeating it and try something different"
        steps.append(f"{n + 1}. {key} -> {result}")
    return "I couldn't get to a final answer within my step limit - the page is open where I stopped."


def _has_unsaved_work(page):
    """True if the page holds anything typed into a text box."""
    try:
        return page.evaluate("""() => !!document.getElementById('aura-banner') ||
            [...document.querySelectorAll('textarea, input[type=text]:not([readonly])')].some(t => (t.value || '').trim().length > 30)""")
    except Exception:
        return False


# ------------------------------------------------------------------ the persistent session
class BrowserSession:
    """One AURA browser window, owned by one thread (Playwright needs that), fed by a queue.
    Stays open between commands so they stack, and remembers recent tasks + answers."""

    def __init__(self, headless=False, profile_dir=None, setup=None):
        self.headless, self.profile_dir, self.setup = headless, profile_dir, setup   # (tests use these)
        self.jobs = queue.Queue()
        self.thread = None
        self.memory = []
        self.url = ""
        self.busy = False
        self.alive = False

    def running(self):
        return bool(self.thread and self.thread.is_alive() and self.alive)

    def submit(self, kind, payload, say):
        if not self.running():
            self.alive = True
            self.thread = threading.Thread(target=self._loop, daemon=True)
            self.thread.start()
        self.jobs.put((kind, payload, say))

    def _loop(self):
        from playwright.sync_api import sync_playwright
        profile = self.profile_dir or PROFILE_DIR
        os.makedirs(profile, exist_ok=True)
        try:
            with sync_playwright() as p:
                context = p.chromium.launch_persistent_context(
                    profile, channel="msedge", headless=self.headless, no_viewport=True,
                    args=["--start-maximized"])
                if self.setup:
                    self.setup(context)
                closed = threading.Event()
                context.on("close", lambda *_: closed.set())
                page = context.pages[0] if context.pages else context.new_page()
                while not closed.is_set():
                    try:
                        kind, payload, say = self.jobs.get(timeout=0.5)
                    except queue.Empty:
                        try:
                            page.wait_for_timeout(50)   # keep the browser responsive between jobs
                        except Exception:
                            break
                        continue
                    if page.is_closed():
                        page = context.pages[-1] if context.pages else context.new_page()
                    if _has_unsaved_work(page):
                        page = context.new_page()   # never navigate away from drafts / typed text - use a new tab
                    self.busy = True
                    try:
                        answer = self._run_job(context, page, kind, payload, say)
                    except Stop as e:
                        answer = str(e)
                    except Exception as e:
                        answer = ("You closed the browser window, so I stopped." if "closed" in str(e).lower()
                                  else f"Something went wrong in the browser: {str(e).splitlines()[0][:160]}")
                    finally:
                        self.busy = False
                    try:
                        self.url = page.url
                    except Exception:
                        pass
                    self.memory.append({"task": payload, "answer": answer[:300]})
                    say(answer, final=True)
                try:
                    context.close()
                except Exception:
                    pass
        finally:
            self.alive = False
            self.memory.clear()

    def _run_job(self, context, page, kind, payload, say):
        status = lambda t: say(t, final=False)
        # kind == "task": start somewhere sensible, then let the agent work
        if page.url in ("", "about:blank"):
            page.goto("https://duckduckgo.com/html/?q=" + quote_plus(payload), wait_until="domcontentloaded", timeout=45000)
        return run_task(page, payload, status, self.memory)


SESSION = BrowserSession()


def _say_factory():
    """Progress goes to the HUD log; things you need to do + the final answer are spoken."""
    def say(text, final=False):
        print(f"  [browser] {text}")
        try:
            if final or text.startswith("please") or "security check" in text:
                from voice import speak   # (also logs it)
                speak(text[0].upper() + text[1:] if text else text)
            else:
                import face_window
                face_window.add_log("aura", text)
        except Exception:
            pass
    return say


def do_task(task):
    """'check train times from A to B', 'find X on site Y'... -> a finished answer."""
    SESSION.submit("task", task, _say_factory())
    return "On it." if SESSION.busy else "On it - give me a moment."


def describe():
    """For the intent classifier: what AURA's browser currently has open (so follow-ups stack)."""
    if not SESSION.running():
        return ""
    last = SESSION.memory[-1]["task"] if SESSION.memory else ""
    where = SESSION.url or "a page"
    return f"AURA's browser is open on {where}" + (f" (last request: \"{last}\")" if last else "")
