# code_helper.py
# Explains errors using the ACTUAL text you copied (clipboard) - more
# reliable than the vision/screenshot approach for this specific case,
# since it reads real text instead of guessing from a screenshot.
import pyperclip
from ai import ask_llm


def explain_clipboard_error():
    try:
        text = pyperclip.paste()
    except Exception as e:
        return f"Couldn't read your clipboard: {e}"

    if not text or not text.strip():
        return "Your clipboard looks empty - copy the error text first, then ask me again."

    prompt = f"""The user copied this from their terminal or editor. If it's an
error/traceback, explain clearly: what went wrong, the likely cause, and a
concrete fix. If it's not actually an error, just say what it looks like instead.

Clipboard content:
{text[:6000]}"""
    return ask_llm([{"role": "user", "content": prompt}])