# pdf_analysis.py
# Reads an actual PDF's real content (grounded) and does the specific
# research-assistant task requested - summarizing a section, extracting
# PICO, listing study characteristics, flagging bias, or comparing papers.
from pypdf import PdfReader
from ai import ask_llm, ask_json
from relevance import find_relevant_files


def find_pdf(name_hint):
    """Finds a PDF on the user's computer matching the given name/title hint."""
    paths, _ = find_relevant_files(name_hint, max_results=1)
    if paths and paths[0].lower().endswith(".pdf"):
        return paths[0]
    return None


def extract_full_text(pdf_path, max_pages=60):
    try:
        reader = PdfReader(pdf_path)
        text = ""
        for page in reader.pages[:max_pages]:
            text += (page.extract_text() or "") + "\n"
        return text
    except Exception as e:
        return f"__ERROR__: {e}"


def _ai_analyze(text, instruction, max_chars=15000):
    trimmed = text[:max_chars]
    messages = [{"role": "user", "content": f"{instruction}\n\nPaper text:\n{trimmed}"}]
    return ask_llm(messages)


def summarize_section(pdf_path, section_hint):
    text = extract_full_text(pdf_path)
    if text.startswith("__ERROR__"):
        return text
    return _ai_analyze(
        text,
        f"Find and summarize the section '{section_hint}' of this paper clearly and "
        "concisely. If you can't find that exact section, summarize the closest "
        "matching part and say so plainly."
    )


def extract_pico(pdf_path):
    text = extract_full_text(pdf_path)
    if text.startswith("__ERROR__"):
        return text
    return _ai_analyze(
        text,
        "Extract the PICO (Population, Intervention, Comparison, Outcome) from this "
        "paper. Present it clearly labeled. If any element isn't clearly stated, say "
        "so rather than guessing."
    )


def list_study_characteristics(pdf_path):
    text = extract_full_text(pdf_path)
    if text.startswith("__ERROR__"):
        return text
    return _ai_analyze(
        text,
        "List the key study characteristics: study design, sample size, setting, "
        "population, duration, and key methods. Use real details from the text, "
        "be concise."
    )


def flag_bias(pdf_path):
    text = extract_full_text(pdf_path)
    if text.startswith("__ERROR__"):
        return text
    return _ai_analyze(
        text,
        "Identify potential sources of bias in this study (selection, performance, "
        "detection, attrition, reporting, funding bias, etc.) based on what's "
        "ACTUALLY described in the text. Be specific about what in the paper "
        "suggests each concern - don't produce generic boilerplate that could "
        "apply to any paper."
    )


def compare_papers(pdf_path1, pdf_path2):
    text1 = extract_full_text(pdf_path1)
    text2 = extract_full_text(pdf_path2)
    if text1.startswith("__ERROR__"):
        return text1
    if text2.startswith("__ERROR__"):
        return text2

    prompt = f"""Compare these two research papers - their methods, findings, and
conclusions. Highlight key similarities and differences.

PAPER 1:
{text1[:8000]}

PAPER 2:
{text2[:8000]}"""
    return ask_llm([{"role": "user", "content": prompt}])


ROUTE_PROMPT = """
Determine what PDF analysis the user wants, and which paper.
Respond ONLY with JSON:
{
  "operation": "summarize_section" | "extract_pico" | "study_characteristics" | "flag_bias",
  "paper_hint": "the paper name/title as best identified from the request",
  "section_hint": "the section mentioned, only if operation is summarize_section, else empty string"
}
"""


def route_pdf_request(instruction):
    messages = [
        {"role": "system", "content": ROUTE_PROMPT},
        {"role": "user", "content": instruction},
    ]
    routed = ask_json(messages)
    return routed if isinstance(routed, dict) else None


def handle_pdf_request(instruction):
    routed = route_pdf_request(instruction)
    if not routed:
        return "I couldn't figure out which paper or what analysis you wanted."

    pdf_path = find_pdf(routed.get("paper_hint", ""))
    if not pdf_path:
        return f"I couldn't find a PDF matching '{routed.get('paper_hint', '')}' on your computer."

    op = routed.get("operation")
    if op == "summarize_section":
        return summarize_section(pdf_path, routed.get("section_hint") or "the requested section")
    elif op == "extract_pico":
        return extract_pico(pdf_path)
    elif op == "study_characteristics":
        return list_study_characteristics(pdf_path)
    elif op == "flag_bias":
        return flag_bias(pdf_path)
    return "I couldn't determine which analysis you wanted."


COMPARE_ROUTE_PROMPT = """
Identify the two paper names/titles being compared from this instruction.
Respond ONLY with JSON: {"paper1_hint": "...", "paper2_hint": "..."}
"""


def handle_compare_request(instruction):
    messages = [
        {"role": "system", "content": COMPARE_ROUTE_PROMPT},
        {"role": "user", "content": instruction},
    ]
    routed = ask_json(messages)
    if not isinstance(routed, dict):
        return "I couldn't figure out which two papers to compare."

    path1 = find_pdf(routed.get("paper1_hint", ""))
    path2 = find_pdf(routed.get("paper2_hint", ""))

    if not path1:
        return f"Couldn't find a PDF matching '{routed.get('paper1_hint', '')}'."
    if not path2:
        return f"Couldn't find a PDF matching '{routed.get('paper2_hint', '')}'."

    return compare_papers(path1, path2)