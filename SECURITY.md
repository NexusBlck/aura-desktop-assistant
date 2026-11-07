# Security & privacy

AURA is a desktop agent: it can see, hear and operate your computer. This document lists what it can do, the
protections it has, and what remains your responsibility.

## Capabilities and protections

| Capability | Where | Protection |
|---|---|---|
| **Mouse & keyboard control** | `pc_control.py`, `desktop_agent.py`, `vision.py` | The desktop agent has hard-coded checks: it refuses to click anything that deletes, uninstalls, formats, sends, pays, submits, signs out, shuts down or discards work, never types into password fields and never presses Delete outside a text box. The screenshot-based fallback (`vision.py`) is only *instructed* by its prompt not to buy, enter passwords or delete — there is no hard-coded check there. Emergency stop: `Ctrl+Alt+X` or mouse to the top-left corner (PyAutoGUI fail-safe). |
| **Web automation** | `web_agent.py` (Playwright + Edge) | Refuses submit/save/delete/pay/send/sign-out style buttons, never types passwords; asks you to complete sign-ins and security checks yourself. Uses its own browser profile in `browser_data/` (git-ignored). |
| **Running AI-generated code** | `autocoder.py`, `build_verify.py`, `claude_builder.py` | Projects are created in `Desktop\AURA_Projects`. Builds install packages and run the generated program and shell commands to test it. **With `SAFE_MODE=true` AURA asks before every build or modification.** Claude Code (if installed) runs with an allow-list of tools/commands and `acceptEdits` permissions inside the project folder. |
| **Shutdown / restart** | `pc_control.py` | Always asks for spoken (or phone) confirmation. |
| **Force-closing apps** | `pc_control.close_app` (`taskkill /f`) | Asks first with `SAFE_MODE=true` (unsaved work in that app would be lost). |
| **Microphone** | `voice.py`, `main.py` | Push-to-talk by default. `WAKE_WORD_ENABLED=true` turns on continuous listening, which sends every phrase the mic hears to Groq Whisper (or Google's recogniser) to detect "AURA". While AURA is speaking, short clips are checked for "stop". |
| **Screen capture** | `recall.py` (Total Recall), screenshots, `vision.py` | Total Recall is **off by default**, skips windows that look like passwords/banking/private browsing, pauses when you're away or the PC is locked, and deletes itself after `keep_days`. Captures stay in `Documents\AURA Recall`. Screen questions send selected screenshots to the AI provider. |
| **Webcam** | `aegis.py`, Vision, Air Control | Off until you start them. Aegis only switches the camera on for a few seconds when someone touches an armed PC. |
| **Files** | `file_search.py`, `knowledge.py`, `phone_bridge.py` | Search reads documents in your user folders and the folders you configure. The phone app can only open files inside AURA's own output folders, via short-lived tickets with path-traversal checks. |
| **Email** | `gmail_watch.py` | Uses the `gmail.modify` scope: reads, labels, archives and creates **drafts**. AURA never sends email or permanently deletes it. |
| **Phone app** | `phone_bridge.py` | Listens on `127.0.0.1` only. Reached through Tailscale Serve (your tailnet only). Phones pair with a 6-digit code shown on the PC and get a revocable token (stored hashed). |
| **Phone remote control (LINK)** | `nexus.py` | **Off by default** (`PHONE_REMOTE_CONTROL=false`). When on, a paired phone can view the screen and send mouse/keyboard input; AURA shows a notice on the PC. |
| **Apple Watch / public internet** | `phone_bridge.py`, `safety.py` | Only if you enable **Tailscale Funnel**. From the internet only the watch endpoint answers, it needs the watch's own key and is rate-limited. With `SAFE_MODE=true` internet requests may only use an allow-list of simple actions (chat, reminders, tasks, schedule…) — no PC control, code, screen or settings. |

At start-up AURA shows a **Privacy check** notice listing any sensitive features that are on (wake word,
Total Recall, LINK, Gmail watching, SAFE_MODE off).

## Data AURA stores

All local, all excluded by `.gitignore`:

- In the AURA folder: `aura_*.json` (memory, chat log, lessons, tasks, reminders, email state, paired devices and
  the phone push key, sign-in session, settings), `google_*token*.json` (Google OAuth tokens), `browser_data/`
  (browser profiles with cookies), `logs/`, `search_results/`, `Face/briefings/`.
- In your user folders: `Documents\AURA …` (Recall, Studio, Spatial, Uploads, Echo, Vision, CDC WONDER…),
  `Desktop\AURA_Projects`, `Pictures\AURA_Screenshots`.

**Paired phones** receive your Groq/Gemini keys, AURA's memory, notes and protocols so the phone brain works
when the PC is off. Unpair a phone in the app's Settings, or delete `aura_phone_devices.json` to unpair all.

## Data sent to third parties

Your messages and, for some features, screenshots, photos, audio or document text are sent to the services you
use — primarily **Groq** (LLM + Whisper) and **Google Gemini** if configured. Web features contact search,
image, diagram and 3D-model services (see the README). `location.py` asks ip-api.com for your approximate
location from your IP address.

## Your responsibilities

- Keep `.env`, `google_credentials.json` and all `aura_*.json` files private. **Never commit them.**
- If a key is ever exposed (pushed to GitHub, pasted in an issue, shared in a zip), **revoke it and create a new
  one** at the provider — deleting it from the repo is not enough, because Git history keeps it.
- Leave `SAFE_MODE=true` unless you understand the risks.
- Only pair devices you own, and leave Tailscale Funnel off unless you use the watch.

## Reporting a vulnerability

Please **don't open a public issue** for security problems. Use GitHub's
[private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
on this repository (Security → Report a vulnerability). Include steps to reproduce; never include real keys or
personal data.
