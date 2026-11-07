# web_search.py
# Real-time web search, powered by Tavily (built for AI assistants).
# Falls back to DuckDuckGo automatically if Tavily fails or has no key set.
from tavily import TavilyClient
from ddgs import DDGS
from config import TAVILY_API_KEY

tavily_client = TavilyClient(api_key=TAVILY_API_KEY) if TAVILY_API_KEY else None


def _search_tavily(query, max_results=5):
    response = tavily_client.search(query=query, max_results=max_results)
    results = []
    for r in response.get("results", []):
        title = r.get("title", "")
        content = r.get("content", "")
        url = r.get("url", "")
        results.append(f"{title}: {content} (Source: {url})")
    return "\n".join(results) if results else "No results found."


def _search_duckduckgo(query, max_results=5):
    results = []
    with DDGS() as ddgs:
        for r in ddgs.text(query, max_results=max_results):
            title = r.get("title", "")
            body = r.get("body", "")
            href = r.get("href", "")
            results.append(f"{title}: {body} (Source: {href})")
    return "\n".join(results) if results else "No results found."


def find_sites(query, max_results=6, snippet_len=200):
    """Like search_web, but returns the raw results as a list of
    {"title", "url", "snippet"} dicts - used to find a website's REAL
    address instead of guessing one. Empty list if every search fails."""
    if tavily_client:
        try:
            response = tavily_client.search(query=query, max_results=max_results)
            results = [
                {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", "")[:snippet_len]}
                for r in response.get("results", []) if r.get("url")
            ]
            if results:
                return results
        except Exception as e:
            print(f"(Tavily site search failed, falling back to DuckDuckGo: {e})")

    try:
        with DDGS() as ddgs:
            return [
                {"title": r.get("title", ""), "url": r.get("href", ""), "snippet": r.get("body", "")[:snippet_len]}
                for r in ddgs.text(query, max_results=max_results) if r.get("href")
            ]
    except Exception as e:
        print(f"(site search failed: {e})")
        return []


def search_web(query, max_results=5):
    if tavily_client:
        try:
            return _search_tavily(query, max_results)
        except Exception as e:
            print(f"(Tavily search failed, falling back to DuckDuckGo: {e})")

    try:
        return _search_duckduckgo(query, max_results)
    except Exception as e:
        return f"Search failed: {e}"