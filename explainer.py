# explainer.py
# The cat's brain: works out what a highlighted word or phrase means FOR YOU.
# Pulls together everything AURA has - a live web search, what AURA
# remembers about you, the app/document you highlighted it in, and the
# model's own knowledge - and returns a short, spoken-style explanation
# plus the web sources it leaned on.
import threading
from urllib.parse import urlparse
from ai import ask_json, ask_llm
from web_search import find_sites
from memory_store import load_store, retrieve_relevant

MAX_TEXT = 3000          # how much of a long highlight we send to the model
SHORT_PHRASE_WORDS = 10  # up to this many words counts as "a word or phrase"


def _search_query(text):
    words = text.split()
    if len(words) <= SHORT_PHRASE_WORDS:
        return f'what does "{text}" mean'
    # A whole passage: search on its opening words - enough to find the
    # topic without sending a paragraph to the search engine.
    return " ".join(words[:14]) + " explained"


def _domain(url):
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _web_results(text):
    try:
        results = find_sites(_search_query(text), max_results=5, snippet_len=500)
    except Exception as e:
        print(f"(explainer web search failed: {e})")
        results = []
    return [r for r in results if r.get("url", "").startswith(("http://", "https://"))]


def _memory_context(text, app):
    try:
        store = load_store()
        relevant = retrieve_relevant(store, f"{text} {app or ''}", top_k=6)
        recent = store.get("recent_turns", [])[-4:]
        recent_lines = "\n".join(f"{t['role']}: {t['content'][:200]}" for t in recent)
        return relevant, recent_lines
    except Exception as e:
        print(f"(explainer memory lookup failed: {e})")
        return "", ""


class Explanation:
    """Gathers context once, then answers the first 'what is this?' and any
    follow-up questions from the same pile of context."""

    def __init__(self, text, app_title=""):
        self.text = (text or "").strip()[:MAX_TEXT]
        self.app = (app_title or "").strip()
        self.web = []
        self.memory = ""
        self.recent = ""
        self.history = []  # previous answers, so follow-ups have continuity

    def gather(self, on_stage=None):
        """Web search runs while memory is read; on_stage(text) reports progress."""
        stage = on_stage or (lambda _t: None)
        web_holder = {}
        stage("Sniffing around the web…")
        t = threading.Thread(target=lambda: web_holder.setdefault("r", _web_results(self.text)), daemon=True)
        t.start()
        self.memory, self.recent = _memory_context(self.text, self.app)
        t.join(timeout=12)
        self.web = web_holder.get("r", [])
        stage("Putting it together for you…")

    def _context_block(self):
        parts = []
        if self.app:
            parts.append(f'Where they highlighted it (window title): "{self.app}"')
        if self.memory:
            parts.append(self.memory)
        if self.recent:
            parts.append("What you and the user were just talking about:\n" + self.recent)
        if self.web:
            lines = [f"[{i}] {r['title']} ({_domain(r['url'])}): {r['snippet']}" for i, r in enumerate(self.web, 1)]
            parts.append("Fresh web search results:\n" + "\n".join(lines))
        else:
            parts.append("(No web results came back - rely on your own knowledge.)")
        return "\n\n".join(parts)

    def _ask(self, task):
        prompt = f"""You are AURA, the user's personal assistant, appearing as a friendly little cat
that just walked over to explain something they highlighted on their screen.

The user highlighted this:
\"\"\"{self.text}\"\"\"

{self._context_block()}

{task}

Use the web results for facts that may be current or specialised, your own
knowledge for the rest, and what you know about the user to make it
relevant to THEM (their work, studies, projects, interests). Pick the
meaning that fits where they found it - a word means different things in a
medical paper, a game, or code.

Reply with ONLY a JSON object:
{{
  "headline": "one short line: what it is, in plain words",
  "explanation": "2-4 clear spoken sentences giving the meaning and the context that matters",
  "for_you": "one sentence tying it to the user's own life/work/what they're reading - or empty string if nothing genuinely fits",
  "sources": [numbers of the web results you actually used, e.g. 1, 3]
}}
This is SPOKEN ALOUD and shown in a small bubble: plain natural sentences,
no markdown, no asterisks, no bullet points, no lists."""
        data = ask_json([{"role": "user", "content": prompt}], temperature=0.4)
        if not isinstance(data, dict) or not (data.get("explanation") or data.get("headline")):
            # The model didn't give usable JSON - fall back to plain text.
            plain = ask_llm([{"role": "user", "content": prompt.split("Reply with ONLY")[0]
                              + "Answer in 2-4 plain spoken sentences, no markdown."}])
            data = {"headline": "", "explanation": plain, "for_you": "", "sources": []}
        return self._finish(data)

    def _finish(self, data):
        used = []
        for n in data.get("sources") or []:
            try:
                r = self.web[int(n) - 1]
            except (ValueError, TypeError, IndexError):
                continue
            if r not in used:
                used.append(r)
        if not used:
            used = self.web[:3]  # still show where AURA looked
        answer = {
            "headline": str(data.get("headline") or "").strip(),
            "explanation": str(data.get("explanation") or "").strip(),
            "for_you": str(data.get("for_you") or "").strip(),
            "sources": [{"title": r["title"], "url": r["url"], "domain": _domain(r["url"])} for r in used[:4]],
        }
        self.history.append(answer["headline"] + " " + answer["explanation"])
        return answer

    def explain(self):
        return self._ask("Explain what this word, phrase or passage means.")

    def follow_up(self, question):
        earlier = "\n".join(self.history[-3:])
        return self._ask(f'You already told them: "{earlier}"\n\nNow they ask a follow-up: "{question}"\n'
                         "Answer that question specifically (the headline should answer it in one line).")


def spoken_text(answer):
    """What the cat says out loud - the explanation (or the headline if that's
    all there is), then the personal bit."""
    parts = [answer.get("explanation") or answer.get("headline") or ""]
    if answer.get("for_you"):
        parts.append(answer["for_you"])
    return " ".join(p for p in parts if p)
