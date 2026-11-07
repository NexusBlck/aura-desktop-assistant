# AURA — a voice-first AI desktop assistant for Windows

AURA is a personal AI assistant that lives on your Windows PC. You talk to it (push-to-talk) or type, and
it acts: it opens apps and types for you, operates desktop apps and websites step by step, finds your
files, answers from your own notes, triages your Gmail, keeps tasks and reminders, builds small apps,
makes images, charts and documents, and shows interactive 3D models. A companion phone app reaches it
privately over Tailscale.

AURA runs locally and uses **your own** API keys for free-tier AI services (Groq, optionally Google
Gemini). It was built for one student's daily use and is shared so others can run and adapt it.

> **Status:** personal project, shared as-is. Developed and tested on Windows 11 with Python 3.13.
> Expect rough edges. Read [Security & privacy](#security--privacy) before running it.

---

## Contents

- [Features](#features)
- [How it works](#how-it-works)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
- [Security & privacy](#security--privacy)
- [Known limitations](#known-limitations)
- [Roadmap](#roadmap)
- [Contributing](#contributing)
- [Third-party services & licences](#third-party-services--licences)
- [License](#license)

## Features

| Area | What AURA does |
|---|---|
| **Talking to AURA** | Push-to-talk hotkey (`Ctrl+Alt+A`), typing in the HUD or Mini AURA window, optional wake word. Speech-to-text with Whisper on Groq; replies spoken with Microsoft neural voices (edge-tts) or Groq Orpheus. Say "stop" to interrupt. |
| **PC control** | Open/close apps, type into the open window, press shortcuts, volume, screenshots. A **desktop agent** reads windows through Windows UI Automation to carry out multi-step tasks ("open Word, write a leave letter and save it as PDF"). |
| **Web** | Quick spoken answers with live search (Tavily/DuckDuckGo), full **web briefings** (an article/picture/video page on any topic), and a **browser agent** (Playwright + Edge) that navigates sites and reports back. |
| **Your files & knowledge** | Finds documents on your PC by name or topic; answers questions from your own material (PDF, Word, PowerPoint, text) with a local BM25 index — no extra API. |
| **Productivity** | Tasks, timed reminders, focus/pomodoro timer, Google Calendar events, a daily briefing, journal check-ins and a life dashboard. |
| **Email** | Gmail triage: announces important mail, reminds you until it's handled, summarises senders, **drafts** replies (it never sends or permanently deletes). |
| **Studio** | Images (Pollinations), charts (matplotlib), diagrams (Kroki), Word, PowerPoint, Excel, web pages, SVG, QR codes, narration; edit results with words. |
| **Spatial** | "Bring up a human heart" — finds a real 3D model (Smithsonian, RCSB, PubChem, Sketchfab, Wikimedia…) and shows it in an interactive viewer you can steer by voice. |
| **App builder** | Describe an app; AURA writes it, installs its packages, runs and browser-tests it, then opens it. Uses Claude Code if installed, otherwise its own builder. **Asks first** in safe mode. |
| **Research tools** | PubMed search, PDF analysis (PICO, bias), paper comparison, a reference library with citations, PRISMA diagrams, CDC WONDER mortality-data extraction. |
| **Vision** | Webcam object detection (TensorFlow.js COCO-SSD) with AI "what is this?" lookups. |
| **Overdrive** *(off until you turn them on)* | **Total Recall** screen memory you can ask questions about, **Aegis** guard mode (locks the PC and takes a webcam photo if someone touches it while you're away), **Air Control** (hand gestures via webcam). |
| **Explain anywhere** | Highlight text in any app, press `Ctrl+Alt+E`, and AURA's cat explains it in a speech bubble. |
| **Phone app** | Installable web app for iPhone/Android over Tailscale: chat and voice, tasks, files, notifications, camera vision, lecture recording (ECHO), interpreter (BABEL), nearby places (RADAR), and a "phone brain" that keeps working when the PC is off. |

## How it works

```
 you ──voice/text──▶ main.py ──▶ intent.py (LLM classifies the request into an action + deterministic guards)
                         │
                         ├─▶ safety.py          (SAFE_MODE confirmations / internet allow-list)
                         ├─▶ pc_control.py      apps, typing, hotkeys, files
                         ├─▶ desktop_agent.py   UI Automation agent for desktop apps
                         ├─▶ web_agent.py       Playwright browser agent
                         ├─▶ studio.py · spatial.py · autocoder.py · gmail_watch.py · smart.py · …
                         └─▶ voice.py           speech in (Whisper) / speech out (edge-tts / Orpheus)

 Face/        desktop UI (HTML/CSS/JS rendered by pywebview: HUD, Mini AURA, sign-in, windows)
 mobile/      phone web app, served by phone_bridge.py on 127.0.0.1 and reached through Tailscale
 ai.py        one place for model calls: Gemini (if configured) → Groq → fallbacks, with rate-limit handling
 config.py    settings with safe defaults, read from .env / environment variables
```

Everything AURA remembers (memory, chats, tasks, email state, paired devices) is stored as JSON files in the
AURA folder or under `Documents\AURA …` on your PC. See [`mobile/ARCHITECTURE.md`](mobile/ARCHITECTURE.md)
for the phone side.

## Requirements

- **Windows 10 or 11.** AURA uses Windows-only APIs (UI Automation, WASAPI volume, Win32). macOS/Linux are not supported.
- **Python 3.13 (64-bit)** from [python.org](https://www.python.org/downloads/) ("Add python.exe to PATH" ticked)
- **Microsoft Edge** (preinstalled on Windows; used by the browser agent and 3D windows)
- A **microphone** for voice (optional — typing works too)
- A free **Groq API key** — <https://console.groq.com/keys>

## Installation

```powershell
git clone https://github.com/<your-username>/aura-desktop-assistant.git
cd aura-desktop-assistant
python -m venv .venv
.\.venv\Scripts\activate
python -m pip install -r requirements.txt
python -m playwright install msedge
copy .env.example .env
notepad .env
```

In `.env`, set at least `LLM_API_KEY`. Then start AURA:

```powershell
python main.py
```

If the key is missing, AURA shows a message telling you what to set instead of starting.

**Start with Windows (optional):** `python aura_launcher.pyw --install` — undo with `python aura_launcher.pyw --uninstall`.

## Configuration

All settings live in **`.env`** (copied from [`.env.example`](.env.example)). `.env` is git-ignored — never
commit it. Environment variables override `.env`; [`config.py`](config.py) holds the defaults.

| Setting | Required? | Purpose |
|---|---|---|
| `LLM_API_KEY` | **Yes** | Groq key: the main brain and speech recognition |
| `USER_NAME`, `USER_ABOUT`, `VOCAB_WORDS` | Recommended | Personalised answers, email drafts, better speech recognition |
| `LOCAL_TIMEZONE`, `LOCATION` | Recommended | Correct times, reminders and weather |
| `GEMINI_API_KEY` | Optional | Smarter answers and vision (Google AI Studio, free tier) |
| `TAVILY_API_KEY` | Optional | Better live web search |
| `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` | Optional | Play songs on Spotify |
| `HF_TOKEN` | Optional | Studio photo editing / video via Hugging Face Spaces |
| `SKETCHFAB_TOKEN` | Optional | More 3D models in Spatial |
| `FIREBASE_API_KEY` | Optional | Sign-in screen. Without it AURA runs in single-user mode with no sign-in. |
| `WAKE_WORD_ENABLED` | Default `false` | Always-on "AURA, …" wake word (see privacy note below) |
| `PHONE_REMOTE_CONTROL` | Default `false` | Lets a paired phone view and control the PC (LINK) |
| `SAFE_MODE` | Default `true` | Extra confirmations and internet restrictions — see [SECURITY.md](SECURITY.md) |

### Google Calendar & Gmail (optional)

1. In [Google Cloud Console](https://console.cloud.google.com/) create a project and enable the **Gmail API** and **Google Calendar API**.
2. **APIs & Services → OAuth consent screen**: set it up (External, add yourself as a test user).
3. **Credentials → Create credentials → OAuth client ID → Desktop app**. Download the JSON.
4. Save it in the AURA folder as `google_credentials.json` (git-ignored).
5. Say "connect my Gmail" or "what's my schedule". A browser window asks you to allow access once.

### Phone app (optional)

Follow [`mobile/SETUP.md`](mobile/SETUP.md). It uses **Tailscale Serve**, so only devices on your own tailnet can
reach AURA. Pair a phone from the tray icon → *Pair a phone*.

## Usage

Press **`Ctrl+Alt+A`** and speak, or type in the AURA window (`Ctrl+Alt+Space` brings it up). Examples:

| Say / type | What happens |
|---|---|
| "open Spotify and play lo-fi beats" | Opens Spotify, searches and plays |
| "open Word and write a short leave letter" | Desktop agent writes it in Word |
| "find everything I have about systematic reviews" | Opens matching documents from your PC |
| "remind me in 20 minutes to call Sam" | Timed reminder (toast + voice) |
| "start a 25 minute focus session on anatomy" | Focus timer in the HUD, logged to the dashboard |
| "any important emails?" | Gmail summary (after connecting Gmail) |
| "bring up everything from the web about the James Webb telescope" | Web briefing page with articles, pictures and videos |
| "make a bar chart of my study hours: Mon 2, Tue 3, Wed 1" | Studio chart you can keep editing |
| "bring up a human heart" | Interactive 3D model |
| "build me a pomodoro timer web app" | App builder (asks first in safe mode) |
| "turn on total recall" / "what was I working on yesterday at 3?" | Screen memory (opt-in) |

Emergency stop for the desktop agent: **`Ctrl+Alt+X`** (or move the mouse into the top-left corner).

## Security & privacy

AURA is powerful by design. Before running it, understand what it can do — full details in **[SECURITY.md](SECURITY.md)**.

- **It controls your PC** (mouse, keyboard, windows). The desktop and browser agents refuse risky clicks
  (delete, send, pay, submit, sign out…) and never type into password fields.
- **It can write and run code.** The app builder installs packages and runs programs it generated. With
  `SAFE_MODE=true` (default) AURA asks you before every build or change. Only build things you'd run yourself.
- **Shutdown and restart always ask for confirmation.** Force-closing an app asks in safe mode.
- **Microphone:** push-to-talk by default. If you enable the wake word, *everything the mic picks up* is sent
  to the speech-to-text service to listen for "AURA".
- **Screen & camera:** Total Recall, Aegis and Vision are off until you turn them on. Their captures stay on your PC.
  AURA shows a "Privacy check" notice at start-up listing sensitive features that are on.
- **Cloud services:** your requests are sent to the AI and web services you configure (Groq, Gemini, Tavily,
  and others listed [below](#third-party-services--licences)). Don't say anything you wouldn't send to them.
- **Your data stays local:** memory, chats and settings are local files, all excluded by `.gitignore`.
- **Phone:** paired phones receive your Groq/Gemini keys and AURA's memory so the phone brain can work when the
  PC is off. Only pair devices you own; unpair them in the phone app's Settings.
- **Antivirus tools may flag AURA** because it uses global keyboard hooks and input automation.

Found a vulnerability? See [SECURITY.md](SECURITY.md#reporting-a-vulnerability).

## Known limitations

- Windows only; tested on Windows 11 + Python 3.13.
- Depends on free-tier cloud APIs, which have daily limits and can change or deprecate models without notice
  (model names are configurable in `.env`).
- The desktop UI is pywebview + HTML; there are no automated UI tests — the unit tests cover logic only.
- Speech output uses edge-tts, an **unofficial** client for Microsoft's online voices that may stop working.
- Ports 8734 (local UI server) and 8740 (phone bridge) must be free; only one AURA can run per Windows user.
- The desktop/browser agents are best-effort: complex apps or unusual websites may need several attempts.
- Some features (vision lab study modes, research tools) were designed around a medical-student workflow.

## Roadmap

Ideas, not commitments:

- A one-click Windows installer and a first-run setup window (instead of editing `.env`)
- A settings screen for the safety options and per-feature permissions
- Configurable UI-server port and running side-by-side instances
- More automated tests (intent routing, agents with recorded UI trees)
- Local/offline speech-to-text as an alternative to cloud Whisper

## Contributing

Issues and pull requests are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md). Please never include API keys,
`.env` files or personal data (memory/chat JSON files) in issues or PRs.

## Third-party services & licences

AURA's own code is MIT-licensed. Python dependencies are installed from PyPI under their own licences (mostly
MIT/BSD/Apache-2.0; `edge-tts` and `pystray` are LGPL-3.0, `pygame` is LGPL-2.1, `pywebpush`/`py-vapid` are
MPL-2.0) — they are not redistributed in this repository. Frontend libraries (three.js, TensorFlow.js, COCO-SSD,
flatpickr) and fonts (Google Fonts) are loaded from public CDNs at runtime.

External services AURA can contact, depending on what you use: Groq, Google Gemini, Tavily, DuckDuckGo,
Google (Gmail, Calendar, Firebase, speech recognition), Microsoft Edge TTS, Pollinations (images), Kroki
(diagrams), Hugging Face Spaces, Sketchfab, Smithsonian 3D, RCSB PDB, PubChem, Wikimedia/Wikipedia, NCBI PubMed,
CDC WONDER, Open-Meteo, OpenStreetMap (Nominatim/Overpass), ip-api.com (approximate location), Spotify, Roblox.
Each has its own terms of use. CDC WONDER data has its own **data-use restrictions** that AURA asks you to
accept before extracting. Images and 3D models AURA finds belong to their owners.

AI output can be wrong. Medical and research features are **for education only** — not for clinical decisions.

## License

[MIT](LICENSE) © 2026 Ahsan Arif
