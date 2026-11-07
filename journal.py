# journal.py
# A short, on-demand reflective check-in - only runs when YOU ask for it,
# never unprompted. Three simple, neutral questions.
import re
from voice import speak, listen
from life_data import log_journal_entry


def run_journal_session():
    speak("Alright, quick check-in. On a scale of 1 to 10, how are you feeling today?")
    mood_raw = listen(timeout=8, phrase_time_limit=6)
    mood = None
    if mood_raw:
        match = re.search(r"\d+", mood_raw)
        if match:
            mood = int(match.group())

    speak("What's one good thing about today?")
    good_thing = listen(timeout=10, phrase_time_limit=15) or ""

    speak("Anything on your mind for tomorrow?")
    on_mind = listen(timeout=10, phrase_time_limit=15) or ""

    log_journal_entry(mood, good_thing, on_mind)
    speak("Got it, logged. Rest well.")