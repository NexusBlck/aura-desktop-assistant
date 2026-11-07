# list_mics.py
# Run this once to see all microphones Windows detects, with their index numbers.
import speech_recognition as sr

print("Available microphones:\n")
for i, name in enumerate(sr.Microphone.list_microphone_names()):
    print(f"{i}: {name}")