# paper_library_action.py
# Routes natural-language paper library requests ("save this paper as...",
# "tag X as methodology", "cite Y in APA") to the right library operation.
from ai import ask_json
from paper_library import add_paper, tag_paper, list_papers, generate_citation

ROUTE_PROMPT = """
Determine what the user wants to do with their paper reference library.
Respond ONLY with JSON:
{
  "operation": "add" | "tag" | "list" | "cite",
  "title": "paper title, for add/cite",
  "authors": "authors if mentioned, else empty string",
  "year": "year if mentioned, else empty string",
  "journal": "journal if mentioned, else empty string",
  "tag": "tag name, for tag operation, else empty string",
  "paper_hint": "fragment identifying an EXISTING paper, for tag/cite operations",
  "style": "citation style if mentioned (APA/Vancouver), default APA"
}
"""


def route_library_request(instruction):
    messages = [
        {"role": "system", "content": ROUTE_PROMPT},
        {"role": "user", "content": instruction},
    ]
    routed = ask_json(messages)
    return routed if isinstance(routed, dict) else None


def handle_library_request(instruction):
    routed = route_library_request(instruction)
    if not routed:
        return "I couldn't figure out what you wanted to do with your paper library."

    op = routed.get("operation")

    if op == "add":
        title = routed.get("title") or instruction
        add_paper(title, routed.get("authors", ""), routed.get("year", ""), routed.get("journal", ""))
        return f"Saved '{title}' to your paper library."

    if op == "tag":
        result = tag_paper(routed.get("paper_hint", ""), routed.get("tag", ""))
        if result:
            return f"Tagged '{result}' as {routed.get('tag', '')}."
        return f"Couldn't find a paper matching '{routed.get('paper_hint', '')}' in your library."

    if op == "list":
        papers = list_papers(routed.get("tag") or None)
        if not papers:
            tag_note = f" tagged '{routed.get('tag')}'" if routed.get("tag") else ""
            return f"Your paper library{tag_note} is empty."
        names = "; ".join(p["title"] for p in papers[:10])
        tag_note = f" tagged '{routed.get('tag')}'" if routed.get("tag") else ""
        return f"You have {len(papers)} papers{tag_note}: {names}."

    if op == "cite":
        papers = list_papers()
        fragment = (routed.get("paper_hint", "") or "").lower()
        match = next((p for p in papers if fragment and fragment in p["title"].lower()), None)
        if not match:
            return f"Couldn't find '{routed.get('paper_hint', '')}' in your library."
        return generate_citation(match, routed.get("style", "APA"))

    return "I couldn't determine what to do with your paper library."