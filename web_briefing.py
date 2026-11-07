# web_briefing.py
# "Bring up everything from the web about X": researches a topic across
# several searches (news, web, images, videos), has the AI keep only what's
# genuinely relevant and write a sourced summary, then opens ONE briefing
# page in the browser - summary, key points with sources, photos, playable
# videos and every article - while AURA reads a short summary out loud.
import os
import re
import time
import html
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from ddgs import DDGS
from ai import ask_json
from config import TAVILY_API_KEY

try:
    from tavily import TavilyClient
    _tavily = TavilyClient(api_key=TAVILY_API_KEY) if TAVILY_API_KEY else None
except Exception:
    _tavily = None

AURA_DIR = os.path.dirname(os.path.abspath(__file__))
# Saved inside the "face" folder so AURA's own local web server (port 8734)
# can serve them - YouTube videos only play on a page served over http.
BRIEFINGS_DIR = os.path.join(AURA_DIR, "face", "briefings")
LOCAL_SERVER = "http://localhost:8734"

PLAN_PROMPT = """The user asked AURA to bring up everything from the web about:
"{request}"
Today's date: {today}

What AURA knows about the user (use it to understand what they MEAN):
{context}

Plan the research. Respond ONLY with JSON:
{{
  "title": "short title for the briefing",
  "subject": "one precise sentence: exactly WHAT the user means, using the context - e.g. 'A boxing gym called RFC, most likely in Leeds, UK, where the user lives and trains'",
  "specific": true if they mean ONE particular thing (a named business, gym, person, product, place or event) - false for a general topic (e.g. "recent medical research"),
  "queries": ["3 web search queries"],
  "recency": "day" | "week" | "month" | "year" | "any",
  "is_news": true or false,
  "checklist": ["ONLY if specific: 6-9 things a person would most want to know about THIS kind of thing. E.g. a gym: 'Head coach / instructors', 'Instructors' credentials and ranks', 'Disciplines taught', 'Class schedule', 'Fees', 'Location / branches', 'Contact', 'Achievements / competitions', 'Reviews'. A restaurant: menu, prices, hours, location... - [] for a general topic"]
}}

Queries:
- General topic: rephrase the request 3 useful ways at the SAME breadth. Do
  NOT invent sub-topics or events - you don't know yet what happened.
- Specific thing: search for THAT thing - its exact name plus the clues that
  pin it down (city, field), e.g. "RFC boxing Leeds", "RFC gym
  Leeds instagram", "RFC boxing club UK".
- Local things (a business, place, local news) with no location given: assume
  the user's own location from the context.
Recency: "week" for recent/latest/new, "day" for today/breaking; "any" for a
specific business/person/place and for timeless topics."""

MATCH_PROMPT = """The user wants information about: {subject}
{identified_note}
What AURA knows about the user: {context}

Below are search results. Many are about DIFFERENT things that merely share a
name or a word (other gyms, other companies, generic pages). Decide which
results are really about THE thing the user means. Use every clue: full name,
city/country, what it does, the user's context. If several different things
share the name, pick the ONE that best fits the user (e.g. in their city) and
reject the others.

STRONGEST CLUE: results marked [literally uses the name: ...] call themselves
exactly what the user said. The thing those results describe is almost
certainly the right one - identify it by the name THOSE sources use (e.g.
"RFC (Riverside Fitness Club)"), not by a look-alike's name.

Be strict: a result about a DIFFERENT but similarly named thing - even one in
the same city - must be rejected, never mixed in. Names must really match:
"R Fitness Studio" is NOT "Riverside Fitness Club" - a different or
missing word means a different place, unless a source says they're the same. If two or more different
things fit the user's request and context about EQUALLY well (e.g. two gyms
in their city whose names could both be shortened to what they said), set
confidence to "low" and list the others in close_candidates - don't guess.

{listing}

Respond ONLY with JSON:
{{
  "identified_as": "full name + where, of the thing that matches - or null if none clearly does",
  "confidence": "high" | "medium" | "low",
  "articles": [numbers of ARTICLES about that exact thing],
  "images": [numbers of IMAGES of that exact thing],
  "videos": [numbers of VIDEOS about that exact thing],
  "refined_queries": ["2 better searches using what you learned, e.g. its full name + city - or [] if none matched"],
  "official_website": "its OWN website URL if you saw one (NOT facebook/instagram/youtube/directories/listing sites) - or null",
  "other_candidates": ["other different things with this name you saw, short, e.g. 'Dynamic Martial Arts of Sioux Falls (USA)'"],
  "close_candidates": ["ONLY other things that fit the user's context about as well as the identified one - full name + area, e.g. 'Riverside Fitness Club (Headingley, Leeds)'; [] if the identified one is clearly right"]
}}"""

BRIEF_PROMPT = """You are writing a research briefing for the user about: "{request}"
What they mean: {subject}
Today's date: {today}

Below are numbered search results. ARTICLES have text excerpts; IMAGES and
VIDEOS only have titles/descriptions. Many results are off-topic noise -
ignore those completely.

If some sources are marked "(official website)", they are the most reliable
description of the subject. Any other source giving a DIFFERENT address,
phone number, name or price is most likely about a DIFFERENT, similarly named
place - leave its details out entirely rather than merging them, and don't
list it in "articles".

Write the briefing ONLY from what these sources actually say. Don't add facts
from memory. State facts ONLY from ARTICLES - image and video titles are not
evidence (never cite [I#] or [V#], never infer names, prices or details from
them). Cite sources as [A3] etc. Mention dates when they matter. If
sources disagree, say so. If there's very little solid information, say that
honestly. For crime/violence topics, stick to reported facts, don't speculate
about guilt, and don't describe graphic details.

{listing}

Respond ONLY with JSON:
{{
  "headline": "one-line headline of the most important finding",
  "spoken_summary": "3-5 natural spoken sentences for AURA to read aloud - plain text, no citations, no markdown",
  "summary": ["2-4 short paragraphs of summary, with [A#] citations"],
  "key_points": ["4-8 bullet points, each with [A#] citations"],
  "quick_facts": [{{"label": "...", "value": "... with [A#] citations"}}] - ONLY for these items (else []): {checklist}. Name people exactly as the sources do. If the sources don't answer an item, value must be "Not found online".
  "articles": [numbers of the ARTICLES that are genuinely relevant, most important first],
  "images": [numbers of IMAGES related to this topic or its stories - aim for 4-8, as plain numbers like 3],
  "videos": [numbers of VIDEOS about this topic or its stories - aim for 2-4, as plain numbers]
}}"""


# ------------------------------------------------------------------ gathering
def _tavily_search(query, plan):
    if not _tavily:
        return [], []
    kwargs = dict(query=query, max_results=6, include_images=True, include_image_descriptions=True)
    if plan.get("is_news"):
        kwargs["topic"] = "news"
    if plan.get("recency") in ("day", "week", "month", "year"):
        kwargs["time_range"] = plan["recency"]
    try:
        r = _tavily.search(**kwargs)
    except Exception as e:
        print(f"  (tavily search failed: {e})")
        return [], []
    articles = [{
        "title": x.get("title", ""), "url": x.get("url", ""), "text": x.get("content", ""),
        "date": x.get("published_date") or "", "source": _domain(x.get("url", "")),
    } for x in r.get("results", []) if x.get("url")]
    images = []
    for img in r.get("images", []):
        if isinstance(img, dict) and img.get("url"):
            images.append({"url": img["url"], "title": img.get("description") or img.get("title") or "", "page": ""})
        elif isinstance(img, str):
            images.append({"url": img, "title": "", "page": ""})
    return articles, images


def _safely(label, fn):
    """Runs one kind of search; if it fails, the others still count."""
    try:
        return list(fn())
    except Exception as e:
        print(f"  (duckduckgo {label} search failed: {e})")
        return []


def _ddg_all(query, plan):
    timelimit = {"day": "d", "week": "w", "month": "m", "year": "y"}.get(plan.get("recency"))
    articles, images, videos = [], [], []
    with DDGS() as d:
        if plan.get("is_news"):
            for x in _safely("news", lambda: d.news(query, max_results=8, timelimit=timelimit)):
                articles.append({"title": x.get("title", ""), "url": x.get("url", ""), "text": x.get("body", ""),
                                 "date": x.get("date", ""), "source": x.get("source") or _domain(x.get("url", ""))})
                if x.get("image") and x["image"] != "None":  # the article's own photo
                    images.append({"url": x["image"], "title": x.get("title", ""), "page": x.get("url", "")})
        else:
            for x in _safely("web", lambda: d.text(query, max_results=8, timelimit=timelimit)):
                articles.append({"title": x.get("title", ""), "url": x.get("href", ""), "text": x.get("body", ""),
                                 "date": "", "source": _domain(x.get("href", ""))})
        image_time = {"d": "day", "w": "week", "m": "month", "y": "year"}.get(timelimit)  # images want words
        for x in _safely("image", lambda: d.images(query, max_results=10, timelimit=image_time)):
            images.append({"url": x.get("image", ""), "thumb": x.get("thumbnail", ""),
                           "title": x.get("title", ""), "page": x.get("url", "")})
        # Videos: don't restrict by date - good explainers are often a bit older.
        for x in _safely("video", lambda: d.videos(query, max_results=8)):
            vid = _youtube_id(x.get("content", "") or x.get("embed_url", ""))
            if vid:
                videos.append({"id": vid, "title": x.get("title", ""), "duration": x.get("duration", ""),
                               "publisher": x.get("uploader") or x.get("publisher", ""), "url": x.get("content", "")})
    return articles, images, videos


# YouTube's own "upload date" filters (the sp= value its search page uses).
_YT_UPLOAD_FILTER = {"day": "EgIIAg==", "week": "EgIIAw==", "month": "EgIIBA==", "year": "EgIIBQ=="}


def _youtube_search(query, recency=None, limit=6):
    """Searches YouTube directly (its public search page) - more dependable
    than DuckDuckGo's video search, which often returns nothing."""
    import json
    import requests
    params = {"search_query": query}
    if recency in _YT_UPLOAD_FILTER:
        params["sp"] = _YT_UPLOAD_FILTER[recency]
    try:
        resp = requests.get("https://www.youtube.com/results", params=params, timeout=10,
                            headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "en-US,en;q=0.9"})
        m = re.search(r"var ytInitialData = (\{.*?\});</script>", resp.text, re.S)
        data = json.loads(m.group(1)) if m else {}
    except Exception as e:
        print(f"  (youtube search failed: {e})")
        return []

    found = []

    def walk(node):
        if len(found) >= limit:
            return
        if isinstance(node, dict):
            v = node.get("videoRenderer")
            if isinstance(v, dict) and v.get("videoId"):
                found.append({
                    "id": v["videoId"],
                    "title": "".join(r.get("text", "") for r in v.get("title", {}).get("runs", [])),
                    "duration": v.get("lengthText", {}).get("simpleText", ""),
                    "publisher": "".join(r.get("text", "") for r in v.get("ownerText", {}).get("runs", [])),
                    "age": v.get("publishedTimeText", {}).get("simpleText", ""),
                    "url": f"https://www.youtube.com/watch?v={v['videoId']}",
                })
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(data)
    return found


def _domain(url):
    return re.sub(r"^https?://(www\.)?", "", url or "").split("/")[0]


def _youtube_id(url):
    m = re.search(r"(?:v=|youtu\.be/|/embed/)([A-Za-z0-9_-]{11})", url or "")
    return m.group(1) if m else None


def _dedupe(items, key):
    seen, out = set(), []
    for item in items:
        k = item.get(key)
        if k and k not in seen:
            seen.add(k)
            out.append(item)
    return out


def _gather(plan, queries=None):
    queries = [q for q in (queries if queries is not None else plan.get("queries", []))
               if isinstance(q, str) and q.strip()][:3]
    articles, images, videos = [], [], []
    with ThreadPoolExecutor(max_workers=6) as pool:
        tavily_jobs = [pool.submit(_tavily_search, q, plan) for q in queries]
        ddg_jobs = [pool.submit(_ddg_all, q, plan) for q in queries[:2]]
        youtube_job = pool.submit(_youtube_search, queries[0] if queries else "", plan.get("recency"), 8)
        for job in tavily_jobs:
            a, i = job.result()
            articles += a
            images += i
        videos += youtube_job.result()
        for job in ddg_jobs:
            a, i, v = job.result()
            articles += a
            images += i
            videos += v
    articles = _dedupe(articles, "url")[:30]
    images = _dedupe([i for i in images if i.get("url", "").startswith("http")], "url")[:30]
    videos = _dedupe(videos, "id")[:10]
    return articles, images, videos


# ------------------------------------------------------------------ the page
def _cite_links(text, articles):
    """Escapes text and turns [A3] citations into links to that article."""
    def link(m):
        n = int(m.group(1))
        if 1 <= n <= len(articles):
            return f'<a class="cite" href="{html.escape(articles[n - 1]["url"])}" target="_blank" rel="noopener">[{n}]</a>'
        return ""
    return re.sub(r"\[A(\d+)\]", link, html.escape(text or ""))


def _pick(numbers, items, limit):
    """The AI answers with 3, "3", "I3", "[V2]"... - accept all of those."""
    out = []
    for n in numbers if isinstance(numbers, list) else []:
        m = re.search(r"\d+", str(n))
        idx = int(m.group()) if m else 0
        if 1 <= idx <= len(items) and items[idx - 1] not in out:
            out.append(items[idx - 1])
    return out[:limit]


def _choose(brief, articles, images, videos):
    """What goes on the page: the AI's picks first, then sensible fallbacks.
    Returns (articles, picture candidates in order of preference, videos)."""
    chosen_articles = _pick(brief.get("articles"), articles, 20) or articles[:10]
    # Every source the summary actually cites must be in the Sources list.
    cited_text = " ".join(str(x) for x in brief.get("summary", []) + brief.get("key_points", []))
    cited = sorted({int(n) for n in re.findall(r"\[A(\d+)\]", cited_text)})
    for article in _pick(cited, articles, 40):
        if article not in chosen_articles:
            chosen_articles.append(article)
    ai_images = _pick(brief.get("images"), images, 12)
    used_pages = {a["url"] for a in chosen_articles}
    from_articles = [i for i in images if i.get("page") in used_pages and i not in ai_images]
    rest = [i for i in images if i not in ai_images and i not in from_articles]
    image_candidates = (ai_images + from_articles + rest)[:18]  # extras replace any that fail to download
    chosen_videos = _pick(brief.get("videos"), videos, 4)
    if not chosen_videos:
        # YouTube's own ranking for the main query is a sensible fallback.
        chosen_videos = [v for v in videos if v.get("age")][:3]
    return chosen_articles, image_candidates, chosen_videos


# ------------------------------------------------------------------ pictures
PICTURES_WANTED = 8
MIN_PICTURE_SIDE = 150            # smaller than this = icon/logo/tracking pixel, not a photo
MAX_PICTURE_BYTES = 8 * 1024 * 1024
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/140.0 Safari/537.36",
    "Accept": "image/avif,image/webp,image/png,image/*,*/*;q=0.8",
}


def _download_picture(img, folder, number):
    """Downloads one picture the way a visitor reading the article would
    (many news sites refuse to show their photos on any other page - that's
    why they appeared broken). Saves a resized JPEG copy next to the
    briefing. Returns (saved filename, fingerprint) or None."""
    import io
    import hashlib
    import requests
    from PIL import Image

    url = img.get("url", "")
    referer = img.get("page") or re.match(r"^https?://[^/]+/", url + "/").group(0)
    for candidate in [url, img.get("thumb")]:
        if not candidate:
            continue
        try:
            resp = requests.get(candidate, headers={**BROWSER_HEADERS, "Referer": referer},
                                timeout=8, stream=True)
            if resp.status_code != 200:
                continue
            data = resp.raw.read(MAX_PICTURE_BYTES + 1, decode_content=True)
            if len(data) > MAX_PICTURE_BYTES:
                continue
            picture = Image.open(io.BytesIO(data))
            picture.load()
        except Exception:
            continue
        if min(picture.size) < MIN_PICTURE_SIDE:
            continue  # a logo or icon, not a real photo
        picture = picture.convert("RGB")
        picture.thumbnail((1200, 1200))
        fingerprint = hashlib.md5(picture.resize((16, 16)).tobytes()).hexdigest()
        filename = f"picture-{number:02d}.jpg"
        picture.save(os.path.join(folder, filename), "JPEG", quality=85)
        return filename, fingerprint
    return None


def _download_pictures(candidates, folder):
    """Downloads candidates in parallel and keeps up to PICTURES_WANTED
    working, non-duplicate pictures (in order of preference). Each kept
    picture gets a "local" path, relative to the briefing page."""
    os.makedirs(folder, exist_ok=True)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda pair: _download_picture(pair[1], folder, pair[0]),
                                enumerate(candidates, 1)))
    kept, seen = [], set()
    for img, result in zip(candidates, results):
        if not result:
            continue
        filename, fingerprint = result
        if fingerprint in seen or len(kept) >= PICTURES_WANTED:
            try:
                os.remove(os.path.join(folder, filename))  # duplicate or surplus
            except OSError:
                pass
            continue
        seen.add(fingerprint)
        kept.append({**img, "local": f"{os.path.basename(folder)}/{filename}"})
    print(f"  (pictures: {len(kept)} saved out of {len(candidates)} tried)")
    return kept


def _build_page(title, request, brief, articles, chosen_articles, pictures, chosen_videos):
    e = html.escape

    summary = "".join(f"<p>{_cite_links(p, articles)}</p>" for p in brief.get("summary", []) if isinstance(p, str))
    points = "".join(f"<li>{_cite_links(p, articles)}</li>" for p in brief.get("key_points", []) if isinstance(p, str))
    facts = "".join(
        f'<tr><th>{e(str(f.get("label", "")))}</th>'
        f'<td class="{"missing" if "not found" in str(f.get("value", "")).lower() else ""}">{_cite_links(str(f.get("value", "")), articles)}</td></tr>'
        for f in brief.get("quick_facts", []) if isinstance(f, dict) and f.get("label"))
    gallery = "".join(
        f'<a class="shot" href="{e(img.get("page") or img["url"])}" target="_blank" rel="noopener">'
        f'<img src="{e(img.get("local") or img["url"])}" alt="{e(img.get("title", ""))}" loading="lazy" '
        f'onerror="this.closest(\'.shot\').remove()">'
        f'<span>{e(img.get("title", "")[:90])}</span></a>'
        for img in pictures
    )
    vids = "".join(
        f'<figure class="video"><iframe src="https://www.youtube-nocookie.com/embed/{e(v["id"])}" title="{e(v["title"])}" '
        f'allow="accelerometer; encrypted-media; picture-in-picture" allowfullscreen loading="lazy" '
        f'referrerpolicy="strict-origin-when-cross-origin"></iframe>'  # YouTube refuses to play without this (Error 153)
        f'<figcaption>{e(v["title"])}{" · " + e(v["duration"]) if v.get("duration") else ""}</figcaption></figure>'
        for v in chosen_videos
    )
    number_of = {a["url"]: i + 1 for i, a in enumerate(articles)}
    sources = "".join(
        f'<li id="a{number_of[a["url"]]}"><span class="num">{number_of[a["url"]]}</span><div>'
        f'<a href="{e(a["url"])}" target="_blank" rel="noopener">{e(a["title"] or a["url"])}</a>'
        f'<div class="meta">{e(a.get("source", ""))}{" · " + e(a["date"][:16]) if a.get("date") else ""}</div>'
        f'<p>{e((a.get("text") or "")[:260])}{"…" if len(a.get("text") or "") > 260 else ""}</p></div></li>'
        for a in chosen_articles
    )
    made = time.strftime("%A %d %B %Y, %I:%M %p")

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)} - AURA Briefing</title>
<style>
  :root {{ --bg:#04070a; --panel:#0a141c; --line:rgba(58,214,255,.22); --cyan:#3ad6ff; --bright:#7de8ff;
          --text:#d9f4ff; --dim:#7b9aa8; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--text); font:15px/1.6 'Segoe UI',system-ui,sans-serif; }}
  .wrap {{ max-width:1100px; margin:0 auto; padding:28px 20px 60px; }}
  .tag {{ color:var(--cyan); letter-spacing:4px; font-size:11px; text-transform:uppercase; }}
  h1 {{ font-size:30px; line-height:1.25; margin:6px 0 4px; color:#fff; }}
  .sub {{ color:var(--dim); font-size:13px; margin-bottom:24px; }}
  section {{ background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:18px 22px; margin-bottom:18px; }}
  h2 {{ font-size:12px; letter-spacing:3px; text-transform:uppercase; color:var(--cyan); margin:0 0 12px; }}
  .headline {{ font-size:19px; color:var(--bright); margin:0 0 10px; }}
  p {{ margin:0 0 10px; }}
  ul.points {{ margin:0; padding-left:20px; }} ul.points li {{ margin-bottom:6px; }}
  a {{ color:var(--bright); }} a.cite {{ font-size:12px; text-decoration:none; margin-left:2px; }}
  .gallery {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(230px,1fr)); gap:12px; }}
  .shot {{ display:block; background:#000; border-radius:8px; overflow:hidden; text-decoration:none; border:1px solid var(--line); }}
  .shot img {{ width:100%; height:170px; object-fit:cover; display:block; }}
  .shot span {{ display:block; padding:6px 9px; font-size:12px; color:var(--dim); }}
  .videos {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(420px,1fr)); gap:14px; }}
  .video {{ margin:0; }} .video iframe {{ width:100%; aspect-ratio:16/9; border:0; border-radius:8px; }}
  .video figcaption {{ font-size:13px; color:var(--dim); margin-top:4px; }}
  table.facts {{ width:100%; border-collapse:collapse; }}
  table.facts th {{ text-align:left; vertical-align:top; width:32%; color:var(--dim); font-weight:600; padding:8px 12px 8px 0; border-top:1px solid var(--line); }}
  table.facts td {{ padding:8px 0; border-top:1px solid var(--line); }}
  table.facts tr:first-child th, table.facts tr:first-child td {{ border-top:0; }}
  table.facts td.missing {{ color:var(--dim); font-style:italic; }}
  ol.sources {{ list-style:none; margin:0; padding:0; }}
  ol.sources li {{ display:flex; gap:12px; padding:12px 0; border-top:1px solid var(--line); }}
  ol.sources li:first-child {{ border-top:0; }}
  .num {{ flex:0 0 26px; height:26px; border-radius:50%; border:1px solid var(--cyan); color:var(--cyan);
          font-size:12px; display:flex; align-items:center; justify-content:center; }}
  .meta {{ font-size:12px; color:var(--dim); }} ol.sources p {{ font-size:13px; color:#b5cfda; margin:4px 0 0; }}
  @media (max-width:600px) {{ .videos {{ grid-template-columns:1fr; }} h1 {{ font-size:24px; }} }}
</style></head>
<body><div class="wrap">
  <div class="tag">AURA Briefing</div>
  <h1>{e(title)}</h1>
  <div class="sub">You asked: “{e(request)}” · {e(made)} · {len(chosen_articles)} sources</div>

  <section><h2>Summary</h2>
    <p class="headline">{e(brief.get("headline", ""))}</p>{summary}</section>
  {f'<section><h2>Quick facts</h2><table class="facts">{facts}</table></section>' if facts else ''}
  {f'<section><h2>Key points</h2><ul class="points">{points}</ul></section>' if points else ''}
  {f'<section><h2>Pictures</h2><div class="gallery">{gallery}</div></section>' if gallery else ''}
  {f'<section><h2>Videos</h2><div class="videos">{vids}</div></section>' if vids else ''}
  <section><h2>Sources</h2><ol class="sources">{sources}</ol></section>
  <div class="sub">Summaries are written by AI from the sources above - open them to check the details.</div>
</div></body></html>"""


def _page_path(title):
    os.makedirs(BRIEFINGS_DIR, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:50] or "briefing"
    return os.path.join(BRIEFINGS_DIR, f"{time.strftime('%Y%m%d-%H%M%S')}-{slug}.html")


def _save_and_open(page_html, path, title):
    filename = os.path.basename(path)
    with open(path, "w", encoding="utf-8") as f:
        f.write(page_html)

    # Prefer AURA's local server (videos need http to play); fall back to
    # opening the file directly if AURA's window server isn't running.
    url = f"{LOCAL_SERVER}/briefings/{filename}"
    try:
        import requests
        requests.head(url, timeout=2).raise_for_status()
    except Exception:
        url = "file:///" + path.replace("\\", "/")
    webbrowser.open(url)
    try:
        import active_window
        active_window.track_opened(title, "website", browser=True)
    except Exception:
        pass
    return path


# ------------------------------------------------------------------ focusing
def _exact_names(request):
    """Names the user used that should literally appear in the right sources:
    acronyms like 'RFC' and anything in quotes."""
    names = re.findall(r"\b[A-Z][A-Z0-9&]{1,9}\b", request or "")
    names += re.findall(r"[\"“']([^\"”']{3,40})[\"”']", request or "")
    return [n for n in dict.fromkeys(names) if n.upper() not in {"AI", "TV", "USA", "UK", "PC"}]


def _uses_name(item, names):
    text = " ".join(str(item.get(k, "")) for k in ("title", "text", "url", "page"))
    return [n for n in names if re.search(r"\b" + re.escape(n) + r"\b", text, re.IGNORECASE)]


_GENERIC_FIRST_WORDS = {"the", "a", "an", "new", "old", "club", "gym", "studio", "school", "academy",
                        "martial", "fitness", "karate", "city", "royal", "national", "international"}


def _keep_named(items, names):
    """Keeps only items that mention one of the names (e.g. "RFC" or
    "Riverside"). Used for a specific thing, so a look-alike ("R.Fitness")
    can't slip in. Returns the originals if nothing would be left."""
    if not names:
        return items
    kept = [i for i in items if i.get("deep") or _uses_name(i, names)]
    return kept or items


def _distinctive_names(exact_names, identity):
    """The words that pin down WHICH thing it is: what the user said (RFC)
    plus the first distinctive word of its identified name (Riverside)."""
    names = list(exact_names or [])
    for word in re.findall(r"[A-Za-z][A-Za-z0-9]+", identity or ""):
        if len(word) >= 3 and word.lower() not in _GENERIC_FIRST_WORDS:
            names.append(word)
            break
    return list(dict.fromkeys(names))


def _listing(articles, images, videos, text_chars=600, names=None):
    return "\n\n".join(
        [f"[A{i}] {a['title']} ({a.get('source', '')}{', ' + a['date'][:16] if a.get('date') else ''})"
         f"{' [literally uses the name: ' + ', '.join(_uses_name(a, names)) + ']' if names and _uses_name(a, names) else ''}\n"
         f"{(a.get('text') or '')[:max(text_chars * 3, 6000 if text_chars >= 600 else 0) if a.get('deep') else text_chars]}"
         for i, a in enumerate(articles, 1)]
        + [f"[I{i}] IMAGE: {img.get('title', '')[:150]} (from {_domain(img.get('page') or img.get('url', ''))})"
           for i, img in enumerate(images, 1)]
        + [f"[V{i}] VIDEO: {v['title']} ({v.get('publisher', '')}{', ' + v['duration'] if v.get('duration') else ''}{', ' + v['age'] if v.get('age') else ''})"
           for i, v in enumerate(videos, 1)]
    )


def _match(plan, context, articles, images, videos, identified_as=None):
    """Asks the AI which results are about the exact thing the user means."""
    note = f"Already identified as: {identified_as} - keep only results about that." if identified_as else ""
    result = ask_json([{"role": "user", "content": MATCH_PROMPT.format(
        subject=plan.get("subject", ""), identified_note=note, context=context or "(nothing known)",
        listing=_listing(articles, images, videos, text_chars=350, names=plan.get("exact_names")))}],
        default=None, reasoning_effort="low")
    if not isinstance(result, dict):
        return None
    return {
        "identified_as": result.get("identified_as") or None,
        "confidence": result.get("confidence", "low"),
        "articles": _pick(result.get("articles"), articles, 30),
        "images": _pick(result.get("images"), images, 30),
        "videos": _pick(result.get("videos"), videos, 10),
        "refined_queries": [q for q in result.get("refined_queries", []) if isinstance(q, str)][:2],
        "other_candidates": [c for c in result.get("other_candidates", []) if isinstance(c, str)][:4],
        "official_website": result.get("official_website") if isinstance(result.get("official_website"), str) else None,
        "close_candidates": [c for c in result.get("close_candidates", []) if isinstance(c, str)][:3],
    }


def _focus_on_subject(plan, context, articles, images, videos):
    """For one specific thing (a gym, a person, a product): keep only results
    about THAT one, learn its full name, then search again with it - the way
    a person narrows a search down. Returns (articles, images, videos,
    identified_as, other_candidates)."""
    first = _match(plan, context, articles, images, videos)
    if not first:
        return articles, images, videos, None, [], None
    kept_a, kept_i, kept_v = first["articles"], first["images"], first["videos"]
    identity = first["identified_as"]
    if identity and first["close_candidates"] and first["confidence"] != "high":
        # Two or more equally good fits - a person would ask, not guess.
        print(f"  (ambiguous: {identity} vs {first['close_candidates']})")
        return [], [], [], None, [identity] + first["close_candidates"], "AMBIGUOUS"
    print(f"  (identified as: {identity or 'nothing clear'} | kept {len(kept_a)} articles, "
          f"{len(kept_i)} images, {len(kept_v)} videos)")

    if identity and first["refined_queries"]:
        print(f"  (second, focused search: {first['refined_queries']})")
        seen = {a["url"] for a in articles} | {i["url"] for i in images} | {v["id"] for v in videos}
        # Search its full identified name first - that's also what YouTube
        # gets searched for, so videos about THIS place turn up.
        name_query = re.sub(r"[()‑‐]", " ", identity).replace(",", " ")
        a2, i2, v2 = _gather({**plan, "recency": "any"}, [name_query] + first["refined_queries"])
        a2 = [a for a in a2 if a["url"] not in seen]
        i2 = [i for i in i2 if i["url"] not in seen]
        v2 = [v for v in v2 if v["id"] not in seen]
        second = _match(plan, context, a2, i2, v2, identified_as=identity) if (a2 or i2 or v2) else None
        if second:
            kept_a += second["articles"]
            kept_i += second["images"]
            kept_v += second["videos"]
            print(f"  (second search added {len(second['articles'])} articles, "
                  f"{len(second['images'])} images, {len(second['videos'])} videos)")

    return kept_a, kept_i, kept_v, identity, first["other_candidates"], first.get("official_website")


# ------------------------------------------------------------------ digging deeper
# Sites that are never a business's "own website".
NOT_OFFICIAL = ("facebook.com", "instagram.com", "youtube.com", "youtu.be", "twitter.com", "x.com",
                "tiktok.com", "linkedin.com", "yelp.com", "google.com", "mapquest.com", "rocketreach.co",
                "wikipedia.org", "reddit.com", "tripadvisor.", "justdial.", "lessons.com")

# Website pages worth reading - where "who teaches here", fees, timings live.
USEFUL_PAGE = re.compile(r"about|team|coach|instructor|trainer|teacher|staff|faculty|master|sensei|founder|"
                         r"people|our-story|class|program|course|schedule|timetable|timing|price|pricing|fee|"
                         r"membership|join|contact|location|branch|faq", re.IGNORECASE)

GAPS_PROMPT = """You're researching: {subject}

Things the user most wants to know:
{checklist}

What has been found so far:
{evidence}

Which of those things are still NOT answered by what's been found? For them,
write up to 3 targeted web searches (use its full name + city).
Respond ONLY with JSON: {{"missing": ["..."], "queries": ["..."]}}"""


def _official_website(identity_site, articles):
    candidates = ([identity_site] if identity_site else []) + [a["url"] for a in articles]
    for url in candidates:
        if url and url.startswith("http") and not any(bad in _domain(url) for bad in NOT_OFFICIAL):
            return re.match(r"^https?://[^/]+", url).group(0)
    return None


# Pages most business websites have - tried directly when the site can't be
# mapped (modern JavaScript sites often hide their page list).
COMMON_PAGES = ["", "about", "about-us", "team", "our-team", "coaches", "instructors", "trainers",
                "classes", "programs", "schedule", "timetable", "pricing", "membership", "fees", "contact"]


def _extract(urls, depth):
    try:
        r = _tavily.extract(urls=urls, extract_depth=depth, format="text")
    except Exception as e:
        print(f"  (couldn't read pages ({depth}): {e})")
        return {}
    return {x["url"]: (x.get("raw_content") or "") for x in r.get("results", [])}


def _read_website(site):
    """Reads the official website's most useful pages (home, about/team/
    coaches, classes, schedule, fees, contact) - the way a person would click
    through it. Handles modern JavaScript sites, whose text only appears when
    the page runs like a real browser."""
    if not _tavily:
        return []
    site = site.rstrip("/")
    try:
        mapped = _tavily.map(site, max_depth=2, limit=40).get("results", [])
    except Exception as e:
        print(f"  (couldn't map {site}: {e})")
        mapped = []
    useful = [u for u in mapped if isinstance(u, str) and USEFUL_PAGE.search(u.split("/", 3)[-1])]
    urls = list(dict.fromkeys([site + "/"] + useful))[:10] if useful else [f"{site}/{p}" for p in COMMON_PAGES]

    texts = _extract(urls, "basic")
    retry = [u for u in urls if len(texts.get(u, "")) < 400]  # empty = a JavaScript-built page
    if retry:
        texts.update({u: t for u, t in _extract(retry, "advanced").items() if len(t) > len(texts.get(u, ""))})

    pages, seen = [], set()
    for url, raw in texts.items():
        text = re.sub(r"\s+", " ", raw).strip()
        fingerprint = text[:400]
        if len(text) < 300 or fingerprint in seen:  # too thin, or the same page under another address
            continue
        seen.add(fingerprint)
        path = url.split("/", 3)[-1] or "home page"
        pages.append({"title": f"Official website - {path}", "url": url, "text": text[:8000],
                      "date": "", "source": _domain(url) + " (official website)", "deep": True})
    print(f"  (read {len(pages)} pages of the official website {site})")
    return pages


def _video_descriptions(videos):
    """YouTube descriptions of videos ABOUT this thing - small businesses
    often list their coaches, address and phone numbers there."""
    import json
    import requests

    def fetch(v):
        try:
            page = requests.get(f"https://www.youtube.com/watch?v={v['id']}", timeout=10,
                                headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "en-US,en;q=0.9"}).text
            m = re.search(r'"shortDescription":"(.*?)","isCrawlable"', page)
            text = json.loads('"' + m.group(1) + '"') if m else ""
        except Exception:
            return None
        if len(text) < 80:
            return None
        return {"title": f"YouTube video description - {v['title']}", "url": f"https://www.youtube.com/watch?v={v['id']}",
                "text": text[:3000], "date": v.get("age", ""), "source": f"youtube.com ({v.get('publisher', '')})", "deep": True}

    with ThreadPoolExecutor(max_workers=4) as pool:
        found = [d for d in pool.map(fetch, videos[:4]) if d]
    print(f"  (read {len(found)} video descriptions)")
    return found


def _fill_gaps(plan, context, identity, articles):
    """Checks the checklist against what's been found and runs targeted
    searches for what's still missing (e.g. 'RFC Leeds head coach')."""
    checklist = [c for c in plan.get("checklist", []) if isinstance(c, str)]
    if not checklist:
        return []
    evidence = _listing(articles, [], [], text_chars=400)[:14000]
    gaps = ask_json([{"role": "user", "content": GAPS_PROMPT.format(
        subject=f"{plan.get('subject', '')} (identified as {identity})",
        checklist="\n".join(f"- {c}" for c in checklist), evidence=evidence)}],
        default=None, reasoning_effort="low")
    queries = [q for q in (gaps or {}).get("queries", []) if isinstance(q, str)][:3] if isinstance(gaps, dict) else []
    if not queries:
        return []
    print(f"  (still missing: {gaps.get('missing')} - searching: {queries})")
    found = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        for batch, _ in pool.map(lambda q: _tavily_search(q, {**plan, "recency": "any", "is_news": False}), queries):
            found += batch
    seen = {a["url"] for a in articles}
    found = _dedupe([a for a in found if a["url"] not in seen], "url")
    matched = _match(plan, context, found, [], [], identified_as=identity) if found else None
    extra = matched["articles"] if matched else []
    print(f"  (gap searches added {len(extra)} sources)")
    return extra


# ------------------------------------------------------------------ main entry
def build_briefing(request, context=""):
    """Researches the request and opens the briefing page. Returns the short
    spoken summary for AURA to say. `context` = what AURA knows about the
    user (location, remembered facts, recent conversation) - used to work
    out WHICH thing they mean."""
    today = time.strftime("%A %d %B %Y")
    plan = ask_json([{"role": "user", "content": PLAN_PROMPT.format(
        request=request, today=today, context=context or "(nothing known)")}],
        temperature=0.2, default=None, reasoning_effort="low")
    if not isinstance(plan, dict) or not plan.get("queries"):
        plan = {"title": request, "subject": request, "specific": False,
                "queries": [request], "recency": "any", "is_news": False}
    title = str(plan.get("title") or request)[:80]
    plan["exact_names"] = _exact_names(request)
    print(f"  (subject: {plan.get('subject')} | specific: {plan.get('specific')})")
    print(f"  (researching: {plan.get('queries')} | recency: {plan.get('recency')})")

    articles, images, videos = _gather(plan)
    if not articles and not videos:
        return f"I couldn't find anything on the web about {request} right now."
    print(f"  (found {len(articles)} articles, {len(images)} images, {len(videos)} videos - reading them)")

    if plan.get("specific"):
        articles, images, videos, identity, others, site = _focus_on_subject(plan, context, articles, images, videos)
        if site == "AMBIGUOUS":
            options = others
            listed = ", ".join(options[:-1]) + f" or {options[-1]}" if len(options) > 1 else options[0]
            return (f"I found {len(options)} places that could be what you mean: {listed}. "
                    f"Which one is it? Tell me and I'll pull up everything on that one.")
        if not identity or not (articles or videos or images):
            # Don't dump results about the wrong thing - ask instead.
            also = f" - like {', '.join(others[:3])} -" if others else ""
            return (f"I found a few different things with that name{also} but nothing that's clearly "
                    f"the one you mean. Can you tell me more - like which city it's in, or its full name?")
        plan["subject"] = f"{plan.get('subject', '')} - identified as: {identity}"
        names = _distinctive_names(plan.get("exact_names"), identity)
        before = (len(articles), len(images), len(videos))
        articles, images, videos = (_keep_named(articles, names), _keep_named(images, names),
                                    _keep_named(videos, names))
        print(f"  (name check {names}: kept {len(articles)}/{before[0]} articles, "
              f"{len(images)}/{before[1]} images, {len(videos)}/{before[2]} videos)")
        # Dig deeper the way a person would: its own website, what its videos
        # say about it, then targeted searches for anything still missing.
        site = _official_website(site, articles)
        with ThreadPoolExecutor(max_workers=2) as pool:
            site_job = pool.submit(_read_website, site) if site else None
            desc_job = pool.submit(_video_descriptions, videos)
            deep = (site_job.result() if site_job else []) + desc_job.result()
        if not site:
            print("  (no official website found - it seems to be on social media only)")
        articles = deep + articles
        articles += [a for a in _fill_gaps(plan, context, identity, articles) if _uses_name(a, names)]
        if not articles:
            print("  (no written articles about it - the page will be mostly pictures and videos)")

    checklist = [c for c in plan.get("checklist", []) if isinstance(c, str)] if plan.get("specific") else []
    brief = ask_json([{"role": "user", "content": BRIEF_PROMPT.format(
        request=request, subject=plan.get("subject", request), today=today,
        checklist="; ".join(checklist) or "(none - leave quick_facts empty)",
        listing=_listing(articles, images, videos))}],
        temperature=0.3, default=None, reasoning_effort="low")
    if not isinstance(brief, dict):
        brief = {"headline": title, "spoken_summary": "", "summary": [], "key_points": [],
                 "articles": list(range(1, min(len(articles), 10) + 1)), "images": [], "videos": []}

    if plan.get("specific"):
        # Everything left was already checked to be about the right thing.
        brief.setdefault("articles", list(range(1, len(articles) + 1)))
        if not _pick(brief.get("videos"), videos, 4):
            brief["videos"] = list(range(1, min(len(videos), 4) + 1))
    chosen_articles, image_candidates, chosen_videos = _choose(brief, articles, images, videos)
    path = _page_path(title)
    pictures = _download_pictures(image_candidates, path[:-len(".html")] + "_pictures")
    page = _build_page(title, request, brief, articles, chosen_articles, pictures, chosen_videos)
    _save_and_open(page, path, title)

    spoken = (brief.get("spoken_summary") or brief.get("headline") or "").strip()
    return (spoken + " " if spoken else "") + "I've put everything - summary, pictures, videos and sources - on a page in your browser."
