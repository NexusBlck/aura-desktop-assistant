# ai.py
import re
import json
import time
from openai import OpenAI, RateLimitError, APIStatusError, APIConnectionError, BadRequestError
import config
from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, FALLBACK_MODELS

# (a placeholder lets AURA import without a key; main.py tells the user to add one)
client = OpenAI(api_key=LLM_API_KEY or "missing-key", base_url=LLM_BASE_URL)

# Google Gemini (free tier) through its OpenAI-compatible endpoint. Its models go in the
# list as "gemini:<model>" so they're never confused with Groq's names.
GEMINI_KEY = getattr(config, "GEMINI_API_KEY", "") or ""
_gemini = OpenAI(api_key=GEMINI_KEY, base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
                 max_retries=0, timeout=40) if GEMINI_KEY else None
GEMINI_MODELS = ["gemini:" + m for m in getattr(config, "GEMINI_MODELS", [])] if _gemini else []

# Smartest first (Gemini, when a key is set), then Groq's models. If one is rate-limited,
# busy or unreachable, the next one answers - so AURA keeps working on free tiers.
MODELS_TO_TRY = GEMINI_MODELS + [LLM_MODEL] + FALLBACK_MODELS

# A model that just said "limit reached" is skipped for a while instead of being asked
# (and waited on) again for every message.
_resting_until = {}


def _rest(model, error):
    text = str(error)
    per_day = "PerDay" in text or "per day" in text.lower() or "(RPD)" in text or "(TPD)" in text
    bad_key = "API key" in text or "API_KEY" in text
    _resting_until[model] = time.time() + (3600 if per_day or bad_key else 60)
    if model.startswith("gemini:"):
        why = ("key wasn't accepted - check GEMINI_API_KEY in .env" if bad_key
               else "used up for today" if per_day else "busy")
        print(f"({model[7:]} {why} - using the backup brain for now)")


def _client_for(model):
    if model.startswith("gemini:"):
        return _gemini, model[len("gemini:"):]
    return client, model


class EmptyReplyError(RuntimeError):
    """The model answered but gave no text (reasoning models sometimes
    spend their whole budget 'thinking' and return content=None)."""


def _create(model, messages, temperature, reasoning_effort, max_tokens=None):
    api, name = _client_for(model)
    if api is None:
        raise APIConnectionError(message=f"{model} isn't set up", request=None)
    if model.startswith("gemini:"):
        # Gemini "thinks" at length by default - fine for hard questions, too slow for a voice
        # assistant's everyday replies. Low thinking unless a caller asks for more.
        reasoning_effort = reasoning_effort or "low"
        if max_tokens:
            max_tokens += 1500   # its thinking counts against max_tokens - don't let that cut the answer
    kwargs = dict(model=name, messages=messages, temperature=temperature)
    if max_tokens:
        kwargs["max_tokens"] = max_tokens
    if reasoning_effort:
        try:
            return api.chat.completions.create(reasoning_effort=reasoning_effort, **kwargs)
        except BadRequestError as e:
            if "reasoning" not in str(e).lower():
                raise  # a real problem, not just "this model doesn't take that option"
    return api.chat.completions.create(**kwargs)


def ask_llm(messages, temperature=0.7, reasoning_effort=None, models=None, usage=None, max_tokens=None):
    """Send a list of chat messages to the LLM and return the reply text.
    Automatically retries with fallback models if the current one is
    rate-limited, unreachable, or returns an empty reply. Always returns a
    string - never None - or raises.
    reasoning_effort="low" makes reasoning models (gpt-oss) think less -
    much faster for simple sorting/judging jobs.
    models: try these instead of the default list. usage: a dict that gets
    filled with {"model", "total_tokens", "finish_reason"} of the call that answered."""
    last_error = None

    candidates = models or MODELS_TO_TRY
    awake = [m for m in candidates if _resting_until.get(m, 0) < time.time()]
    for model in awake or candidates[-1:]:
        try:
            response = _create(model, messages, temperature, reasoning_effort, max_tokens)
            content = response.choices[0].message.content
            if content:
                content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
            if usage is not None:
                usage["model"] = model
                usage["total_tokens"] = getattr(response.usage, "total_tokens", None) if response.usage else None
                usage["finish_reason"] = response.choices[0].finish_reason  # "length" = reply was cut off
            if content:
                return content
            last_error = EmptyReplyError(f"{model} returned an empty reply")
        except RateLimitError as e:
            last_error = e
            _rest(model, e)
            continue
        except (APIStatusError, APIConnectionError) as e:
            last_error = e
            if model.startswith("gemini:"):
                _rest(model, e)   # e.g. Google's servers overloaded - don't make every message wait on it
            continue

    raise last_error or EmptyReplyError("No model returned a reply")


def extract_json(raw):
    """Pulls the first JSON object/array out of a model reply, tolerating
    markdown code fences and extra text before/after. Returns the parsed
    value, or raises ValueError."""
    if raw is None:
        raise ValueError("no text to parse")
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)

    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch in "{[":
            try:
                value, _ = decoder.raw_decode(text[i:])
                return value
            except json.JSONDecodeError:
                continue
    raise ValueError(f"no JSON found in reply: {text[:200]!r}")


def ask_json(messages, temperature=0, default=None, reasoning_effort=None):
    """Like ask_llm, but for prompts that must answer with JSON. Returns the
    parsed value, or `default` if the call or the parsing fails."""
    try:
        return extract_json(ask_llm(messages, temperature=temperature, reasoning_effort=reasoning_effort))
    except Exception as e:
        print(f"(ask_json: couldn't get valid JSON: {e})")
        return default
