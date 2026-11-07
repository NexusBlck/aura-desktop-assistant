# list_voices.py
# Run this to see every available neural voice, so you can pick one you like
# and set it as VOICE_NAME in config.py.
import asyncio
import edge_tts


async def main():
    voices = await edge_tts.list_voices()
    for v in sorted(voices, key=lambda x: x["ShortName"]):
        print(f"{v['ShortName']}  -  {v['Gender']}  -  {v['Locale']}")


asyncio.run(main())