# relevance.py
# Finds the files on your PC that are genuinely about a topic - the way a
# person would search:
#   1. Think of related words first ("medicine" -> pharmacology, clinical,
#      antibiotics, MBBS, patient...), because files about a topic rarely
#      use the topic's exact word.
#   2. Score every indexed document by those words in its NAME, its FOLDER
#      path, and its actual TEXT - fast, runs locally, no AI.
#   3. Have the AI read the best-scoring candidates (name + folder + a text
#      snippet) and keep only the ones really about the topic.
# Folders whose name matches the topic (e.g. "Systematic Reviews") are
# returned too, since opening the folder is often what you want.
import os
import re
from concurrent.futures import ThreadPoolExecutor
from ai import ask_json
from file_search import get_index

# Filler words that don't say what a file is ABOUT - so "bring up everything
# we have on medicine from my pc" searches for "medicine", not "everything".
STOPWORDS = {
    "the", "a", "an", "of", "on", "in", "at", "to", "for", "from", "my", "we",
    "our", "your", "pc", "computer", "laptop", "file", "files", "document",
    "documents", "everything", "anything", "all", "about", "related", "associated",
    "have", "has", "saved", "and", "or", "with", "that", "this", "i", "me", "please",
    "bring", "up", "open", "show", "find", "stuff", "things", "notes",
}

EXPAND_PROMPT = """The user wants to find every file on their computer about: "{topic}"

List 12-25 lowercase search words/short phrases that such files' NAMES,
FOLDER names or TEXT would likely contain: the topic itself, synonyms,
sub-topics, abbreviations and key technical terms. Prefer specific words
over generic ones ("pharmacology" yes, "study" or "document" no).
Respond ONLY with JSON: {{"keywords": ["...", "..."]}}"""

RELEVANCE_PROMPT = """
You are helping find files on someone's computer that are genuinely about a
topic they asked for. For each candidate you get its PATH (the folder names
are strong clues), NAME, and a SNIPPET of its actual text.

Judge by SUBJECT MATTER, not literal keyword matching: a file named
"PRISMA_flow_diagram.docx" full of study-selection criteria IS about
"systematic reviews" even without those exact words. Exclude files that only
mention a word in passing and clearly aren't about the topic (software
licences, logs, readme files of unrelated programs, etc.).

Respond ONLY with a JSON array of the exact PATH values (copied exactly as
given) of the files in THIS batch that are genuinely about the topic, or []
if none are.
"""

SPECIFIC_PROMPT = """
The user asked to open ONE specific file: "{topic}". From the candidates
below, return the PATH of the file they mean, copied exactly - or [] if none
of them is it. Respond ONLY with a JSON array with at most one PATH.
"""

BATCH_SIZE = 15       # files per AI request - small enough to be judged reliably
MAX_CANDIDATES = 90   # how many top-scoring files the AI reads at most


def _words(text):
    return [w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 2 and w not in STOPWORDS]


def expand_topic(topic):
    """Topic -> list of related search words (always includes the topic's
    own words, even if the AI is unavailable)."""
    base = _words(topic)
    phrase = " ".join(base)
    data = ask_json([{"role": "user", "content": EXPAND_PROMPT.format(topic=topic)}],
                    temperature=0.2, default={}, reasoning_effort="low")
    extra = data.get("keywords", []) if isinstance(data, dict) else []
    keywords = []
    for kw in [phrase] + base + [str(k).lower().strip() for k in extra]:
        if kw and len(kw) > 2 and kw not in STOPWORDS and kw not in keywords:
            keywords.append(kw)
    return keywords[:30]


def _score(path, entry, keywords):
    name = entry["name"].lower()
    folder = os.path.dirname(path).lower()
    snippet = (entry.get("snippet") or "").lower()
    score = 0
    for kw in keywords:
        if kw in name:
            score += 3
        elif kw in folder:
            score += 2
        if kw in snippet:
            score += 1
    return score


def _judge_batch(topic, batch, files, prompt):
    listing = "\n\n".join(
        f"PATH: {p}\nNAME: {files[p]['name']}\n"
        f"SNIPPET: {files[p].get('snippet') or '(no readable text - judge by name and folder)'}"[:900]
        for p in batch
    )
    picked = ask_json([
        {"role": "system", "content": prompt},
        {"role": "user", "content": f'Topic: "{topic}"\n\nCandidate files:\n\n{listing}'},
    ], default=[], reasoning_effort="low")
    return [p for p in picked if p in batch] if isinstance(picked, list) else []


def _judge(topic, paths, files, prompt):
    """Asks the AI which of these paths are really relevant. Batches are
    checked at the same time (not one after another), so a search takes
    about as long as a single check."""
    batches = [paths[i:i + BATCH_SIZE] for i in range(0, len(paths), BATCH_SIZE)]
    print(f"  checking {len(paths)} candidates in {len(batches)} batch(es) at once...")
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda b: _judge_batch(topic, b, files, prompt), batches))
    relevant = []
    for batch_result in results:  # keep best-scoring-first order
        relevant.extend(p for p in batch_result if p not in relevant)
    return relevant


def _matching_folders(topic, folders):
    """Folders whose own name contains the topic's words - e.g. the
    "Systematic Reviews" folder for "systematic reviews"."""
    topic_words = _words(topic)
    if not topic_words:
        return []
    matches = []
    for path, name in folders.items():
        folder_words = set(_words(name))
        # every topic word must appear (allowing plural/singular: "review" ~ "reviews")
        if all(any(fw.startswith(tw[:5]) for fw in folder_words) for tw in topic_words):
            matches.append(path)
    # Drop sub-folders of a folder that's already matched.
    matches.sort(key=len)
    return [m for m in matches if not any(m != o and m.startswith(o + os.sep) for o in matches)]


def find_relevant(topic, mode="topic", max_files=60):
    """Returns {"files": [...], "folders": [...], "scanned": n}.
    mode "topic": everything about a subject. mode "specific": the one file
    the user named ("open PRISMA 2020")."""
    index = get_index()
    files = index["files"]
    if not files:
        return {"files": [], "folders": [], "scanned": 0}

    topic_words = _words(topic)
    direct = [p for p, e in files.items() if topic_words and all(w in e["name"].lower() for w in topic_words)]

    if mode == "specific":
        if direct:
            direct.sort(key=lambda p: -files[p]["mtime"])  # newest first
            return {"files": direct[:3], "folders": [], "scanned": len(files)}
        scored = sorted(((_score(p, e, topic_words), p) for p, e in files.items()), reverse=True)
        candidates = [p for s, p in scored if s > 0][:30]
        picked = _judge(topic, candidates, files, SPECIFIC_PROMPT.format(topic=topic))[:1] if candidates else []
        return {"files": picked, "folders": [], "scanned": len(files)}

    keywords = expand_topic(topic)
    print(f"  (searching {len(files)} documents for: {', '.join(keywords[:12])}{'...' if len(keywords) > 12 else ''})")
    scored = sorted(((_score(p, e, keywords), p) for p, e in files.items()), reverse=True)
    candidates = [p for s, p in scored if s > 0][:MAX_CANDIDATES]
    for p in direct:  # a file literally named after the topic is always considered
        if p not in candidates:
            candidates.insert(0, p)

    relevant = _judge(topic, candidates, files, RELEVANCE_PROMPT) if candidates else []
    for p in direct:  # ...and always kept
        if p not in relevant:
            relevant.insert(0, p)

    return {
        "files": relevant[:max_files],
        "folders": _matching_folders(topic, index.get("folders", {})),
        "scanned": len(files),
    }


def find_relevant_files(topic, max_results=8):
    """Older interface (used by pdf_analysis): (paths, number_scanned)."""
    mode = "specific" if max_results <= 3 else "topic"
    result = find_relevant(topic, mode=mode, max_files=max_results)
    return result["files"][:max_results], result["scanned"]
