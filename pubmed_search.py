# pubmed_search.py
# Real literature search via PubMed's official, free E-utilities API.
# (Google Scholar has no legitimate free API - anything claiming to search
# it is scraping, which breaks Google's terms and gets blocked constantly -
# so PubMed is the reliable, grounded choice here.)
import requests
from ai import ask_llm

ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
ESUMMARY_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"

_last_results = {"query": None, "papers": []}


def search_pubmed(query, max_results=10):
    """Returns a list of paper dicts, or a string starting with __ERROR__.
    Also caches the results so a follow-up 'give me the full list' can
    reuse them without re-searching or guessing."""
    try:
        search_resp = requests.get(ESEARCH_URL, params={
            "db": "pubmed", "term": query, "retmax": max_results, "retmode": "json",
        }, timeout=10)
        ids = search_resp.json().get("esearchresult", {}).get("idlist", [])
        if not ids:
            _last_results["query"] = query
            _last_results["papers"] = []
            return []

        summary_resp = requests.get(ESUMMARY_URL, params={
            "db": "pubmed", "id": ",".join(ids), "retmode": "json",
        }, timeout=10)
        result = summary_resp.json().get("result", {})

        papers = []
        for pid in ids:
            info = result.get(pid, {})
            authors = ", ".join(a.get("name", "") for a in info.get("authors", [])[:3])
            papers.append({
                "pmid": pid,
                "title": info.get("title", "Untitled"),
                "authors": authors,
                "journal": info.get("fulljournalname", ""),
                "year": (info.get("pubdate", "") or "").split(" ")[0],
                "url": f"https://pubmed.ncbi.nlm.nih.gov/{pid}/",
            })

        _last_results["query"] = query
        _last_results["papers"] = papers
        return papers
    except Exception as e:
        return f"__ERROR__: {e}"


def get_last_results():
    return _last_results["papers"], _last_results["query"]


def format_full_list(papers):
    if not papers:
        return "I don't have a recent PubMed search to list - search for something first."
    lines = [f"{p['title']} ({p['year']}, {p['journal']})" for p in papers]
    return f"Here's the full list of {len(papers)}: " + "; ".join(lines)


def summarize_pubmed_results(papers, query):
    """Condenses a broad search into a short spoken summary (headline-first,
    not a wall of citations) - full list available on request."""
    if isinstance(papers, str) and papers.startswith("__ERROR__"):
        return f"PubMed search failed: {papers.replace('__ERROR__: ', '')}"
    if not papers:
        return f"I couldn't find any studies on PubMed about {query}."

    listing = "\n".join(f"- {p['title']} ({p['year']}, {p['journal']})" for p in papers)
    prompt = f"""The user searched PubMed for "{query}" and got these {len(papers)} results:

{listing}

Give a short, natural spoken summary: mention how many were found and name
a couple of the most relevant/notable ones. Keep it to 2-3 sentences, then
mention they can ask for the full list if they want it."""
    return ask_llm([{"role": "user", "content": prompt}])


def format_verification_result(papers, query):
    """For 'is this study published' checks - honest either way, and clear
    that a miss on PubMed doesn't mean the study definitely doesn't exist."""
    if isinstance(papers, str) and papers.startswith("__ERROR__"):
        return f"Couldn't check PubMed: {papers.replace('__ERROR__: ', '')}"
    if not papers:
        return (
            f"I couldn't find '{query}' on PubMed. That doesn't necessarily mean it's "
            f"unpublished, though - it could be indexed under different wording, "
            f"published somewhere PubMed doesn't cover, or a preprint not yet indexed."
        )
    top = papers[0]
    return f"Yes - found it: \"{top['title']}\" ({top['year']}, {top['journal']}). Link: {top['url']}"