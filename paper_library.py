# paper_library.py
# A simple persistent reference manager - save papers, tag them, generate
# citations. Same JSON-file pattern as tasks.py.
import os
import json
import datetime

LIBRARY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aura_paper_library.json")


def load_library():
    if os.path.exists(LIBRARY_FILE):
        try:
            with open(LIBRARY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []


def save_library(papers):
    try:
        with open(LIBRARY_FILE, "w", encoding="utf-8") as f:
            json.dump(papers, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"(Couldn't save paper library: {e})")


def add_paper(title, authors="", year="", journal="", url=""):
    papers = load_library()
    papers.append({
        "title": title, "authors": authors, "year": year, "journal": journal,
        "url": url, "tags": [], "added": datetime.datetime.now().isoformat(),
    })
    save_library(papers)


def tag_paper(title_fragment, tag):
    papers = load_library()
    fragment = (title_fragment or "").lower()
    for p in papers:
        if fragment and fragment in p["title"].lower():
            if tag not in p["tags"]:
                p["tags"].append(tag)
            save_library(papers)
            return p["title"]
    return None


def list_papers(tag_filter=None):
    papers = load_library()
    if tag_filter:
        return [p for p in papers if tag_filter.lower() in [t.lower() for t in p["tags"]]]
    return papers


def find_paper(title_fragment):
    fragment = (title_fragment or "").lower()
    for p in load_library():
        if fragment and fragment in p["title"].lower():
            return p
    return None


def generate_citation(paper, style="APA"):
    authors = paper.get("authors", "") or "Unknown authors"
    year = paper.get("year", "") or "n.d."
    title = paper.get("title", "")
    journal = paper.get("journal", "")
    style = (style or "APA").upper()

    if style == "VANCOUVER":
        return f"{authors}. {title}. {journal}. {year}."
    return f"{authors} ({year}). {title}. {journal}."