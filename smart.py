# smart.py
# AURA's "think it through" brain for HARD questions (intent says difficulty = hard). Free models only.
#   1. PLAN     a fast model decides what's needed: your own material? the web? exact calculations?
#   2. GATHER   your knowledge base (knowledge.py), web search (web_search.py), a safe calculator
#   3. ANSWER   the strongest free model, thinking at full effort, with that context + your corrections
#   4. CHECK    a DIFFERENT model reviews the answer against the sources and fixes mistakes before you hear it
# Easy messages never come here - they keep the fast one-call path.
import ast
import json
import math
import operator
import re
import time

import config
from ai import ask_llm, extract_json, GEMINI_MODELS

STRONG = GEMINI_MODELS[:1] + [config.LLM_MODEL] + GEMINI_MODELS[1:2] + list(config.FALLBACK_MODELS)    # smartest first
CHECKER = [config.LLM_MODEL] + GEMINI_MODELS[:1] + list(config.FALLBACK_MODELS)                         # a second opinion, other family first
FAST = GEMINI_MODELS[-1:] + list(config.FALLBACK_MODELS[:1]) + [config.LLM_MODEL]


def _json(messages, models, effort="low", max_tokens=600):
    try:
        raw = ask_llm(messages, temperature=0.1, reasoning_effort=effort, models=models, max_tokens=max_tokens)
        data = extract_json(raw)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        print(f"(smart: {e})")
        return {}


# ------------------------------------------------------------------ the calculator (exact maths, no eval of code)
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv, ast.Pow: operator.pow,
        ast.Mod: operator.mod, ast.FloorDiv: operator.floordiv, ast.USub: operator.neg, ast.UAdd: operator.pos}
_FUNCS = {k: getattr(math, k) for k in ("sqrt", "log", "log10", "log2", "exp", "sin", "cos", "tan", "asin", "acos", "atan", "floor", "ceil", "fabs", "factorial")}
_FUNCS.update(abs=abs, round=round, min=min, max=max, pi=math.pi, e=math.e)


def calc(expr):
    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            if isinstance(n.op, ast.Pow) and abs(ev(n.right)) > 100:
                raise ValueError("power too large")
            return _OPS[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.operand))
        if isinstance(n, ast.Name) and n.id in _FUNCS and not callable(_FUNCS[n.id]):
            return _FUNCS[n.id]
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and callable(_FUNCS.get(n.func.id)):
            return _FUNCS[n.func.id](*[ev(a) for a in n.args])
        raise ValueError("not plain maths")
    expr = str(expr).replace("^", "**").replace("×", "*").replace("÷", "/")[:200]
    v = ev(ast.parse(expr, mode="eval"))
    return round(v, 6) if isinstance(v, float) else v


# ------------------------------------------------------------------ the pipeline
def _plan(question, history_text, have_material):
    return _json([{"role": "user", "content": f"""You plan how AURA (a personal AI for {config.describe_user()}) will answer a hard question.
Today is {time.strftime('%A %d %B %Y')} - "this year" / "latest" means now.
Recent conversation:
{history_text or '(none)'}
Question: "{question}"
{"The user has a personal knowledge base of their own lectures, study guides, notes and manuscripts." if have_material else ""}
Decide what's needed. Respond ONLY with JSON:
{{"use_material": true/false (their own course material would help or they ask about it),
 "material_query": "keywords to search their material with",
 "web_queries": ["0-2 web searches, only if current facts or things you might misremember matter"],
 "calculations": ["0-4 plain arithmetic expressions to compute exactly, e.g. 70*0.6+12"],
 "approach": "one line: how to answer it well"}}"""}], FAST, "low", 400)


def _gather(plan, question):
    parts, sources = [], []
    if plan.get("use_material"):
        try:
            import knowledge
            hits = knowledge.search(plan.get("material_query") or question, 8)
            best = hits[0]["score"] if hits else 0
            hits = [h for h in hits if h["score"] >= best * 0.55][:6]           # drop passages that only share a word or two
            for i, h in enumerate(hits, 1):
                tag = f"M{i}"
                parts.append(f"[{tag}] ({h['source']}: {h['title']}{' ' + h['where'] if h['where'] else ''})\n{h['text']}")
                sources.append(f"{h['title']}{' ' + h['where'] if h['where'] else ''}")
        except Exception as e:
            print(f"(smart: knowledge search failed: {e})")
    for q in (plan.get("web_queries") or [])[:2]:
        try:
            from web_search import search_web
            parts.append(f"[WEB: {q}]\n{search_web(str(q)[:200], 4)[:3000]}")
        except Exception as e:
            print(f"(smart: web search failed: {e})")
    calcs = []
    for expr in (plan.get("calculations") or [])[:4]:
        try:
            calcs.append(f"{expr} = {calc(expr)}")
        except Exception:
            pass
    if calcs:
        parts.append("[EXACT CALCULATIONS - use these numbers]\n" + "\n".join(calcs))
    return "\n\n".join(parts), sources


def answer(question, history=None, facts=None, draft=""):
    """A careful answer to a hard question. Returns (reply_text, info) - info says what it used."""
    t0 = time.time()
    history = [m for m in (history or []) if m.get("role") in ("user", "assistant")][-6:]
    history_text = "\n".join(f"{'User' if m['role'] == 'user' else 'AURA'}: {str(m['content'])[:300]}" for m in history)
    try:
        import knowledge
        have_material = knowledge.stats()["passages"] > 0
    except Exception:
        have_material = False
    plan = _plan(question, history_text, have_material)
    context, sources = _gather(plan, question)
    try:
        from lessons import relevant_for
        learned = relevant_for(question)
    except Exception:
        learned = []
    about = "\n".join(f"- {f}" for f in (facts or [])[-15:])

    system = f"""You are AURA, a brilliant personal AI and tutor for {config.describe_user()}. Think carefully before you answer.
Today is {time.strftime('%A %d %B %Y')}.
About the user:
{about or '(nothing yet)'}
{('Lessons from their past corrections - follow them:' + chr(10) + chr(10).join('- ' + l for l in learned)) if learned else ''}
How to answer:
- Be correct above all. Reason step by step privately; give the user the result, not your scratch work.
{{material_rule}}
- Use the exact calculations given; never do arithmetic in your head when a result is provided. Don't mention
  calculations, tools, searches or tags - just answer naturally.
- Never write source tags like [M1] or (M2).
- It's read aloud: plain natural sentences in paragraphs - NO bullet points, lists, markdown or tables. Lead with the answer, then the key reasoning.
  Usually 3-8 sentences; longer only if they asked for a lot (a quiz, a plan, step-by-step). For a quiz, ask ONE question at a time.
- If you're not sure, say what you're unsure about instead of guessing.
Plan for this question: {plan.get('approach', '')}"""
    material_rule = ("- Passages from THEIR OWN material are below (tagged M1, M2...). Base your answer on them where they're relevant and say so\n"
                     "  naturally (\"your study guide says...\", \"in your lecture...\"). If their material and standard knowledge disagree, say so."
                     if sources else
                     "- None of the user's own material was used for this answer, so NEVER say \"your notes / study guide / lecture says\".")
    system = system.replace("{material_rule}", material_rule)
    user = f"{('Recent conversation:' + chr(10) + history_text + chr(10) * 2) if history_text else ''}Question: {question}"
    if context:
        user += f"\n\nMaterial and tools:\n{context[:14000]}"
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    try:
        usage = {}
        reply = ask_llm(messages, temperature=0.3, reasoning_effort="medium", models=STRONG, max_tokens=4000, usage=usage)
        model = usage.get("model", "")
    except Exception as e:
        print(f"(smart: strong models unavailable: {e})")
        return draft, {"used": "fast", "error": str(e)}

    # ---- 4. the second opinion
    check = _json([{"role": "user", "content": f"""You are a strict expert reviewer (medicine, science, maths). Check this answer for factual errors,
wrong numbers, unsafe medical claims, or not answering the question. Use the material as ground truth where relevant.
Question: "{question}"
{('Material:' + chr(10) + context[:8000]) if context else ''}
Answer to check:
\"\"\"{reply}\"\"\"
Respond ONLY with JSON: {{"verdict": "ok" | "fix", "problems": ["each real problem, briefly"],
"fixed": "if verdict is fix: the full corrected answer in the same spoken style; else empty"}}
Only say "fix" for real mistakes, not style."""}], [m for m in CHECKER if m != model] or CHECKER, "medium", 3500)
    fixed = False
    if check.get("verdict") == "fix" and len(str(check.get("fixed") or "")) > 40:
        reply, fixed = str(check["fixed"]).strip(), True
        print(f"(smart: reviewer fixed: {'; '.join(map(str, check.get('problems') or []))[:200]})")
    info = {"used": "deep", "model": model, "material": sources, "web": plan.get("web_queries") or [], "fixed": fixed,
            "seconds": round(time.time() - t0, 1)}
    print(f"(smart: {json.dumps(info)[:300]})")
    reply = re.sub(r"\n\s*(?:[•\-*]|\d+[.)])\s+", "\n", reply)              # spoken: no bullet markers
    reply = re.sub(r"\*\*|__|#+ ", "", reply)
    reply = re.sub(r"\s*[\[(]\s*(?:M\d+(?:\s*[,;&]\s*M\d+)*|WEB[^\])]*)\s*[\])]", "", reply)
    return re.sub(r"[ \t]{2,}", " ", reply).strip(), info
