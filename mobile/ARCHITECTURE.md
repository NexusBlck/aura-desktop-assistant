# AURA Mobile — architecture & feature parity

AURA (the PC app) is the source of truth. The phone app is **AURA Mobile**: the same assistant,
same brain, same data, same look — reached from an iPhone (Android later).

## Constraints that shaped this

| Constraint | Consequence |
|---|---|
| Development happens on Windows, no Mac | A native Swift/Xcode build can't be compiled here. |
| "Don't pay anything" | No Apple Developer Program ($99/yr) → no App Store / TestFlight. |
| AURA's brain, data, API keys and most abilities live on the PC (Python, Windows APIs) | Re-implementing them on the phone would duplicate everything and leak keys. |
| iOS sandbox | The phone can't control other apps, read arbitrary files, or run in the background. |

## Decision

```
 iPhone (home-screen web app, standalone)          PC (AURA, unchanged brain)
 ┌───────────────────────────────┐   HTTPS   ┌────────────────────────────────────────┐
 │ AURA Mobile  (mobile/)        │ ────────▶ │ Tailscale Serve (private tailnet, TLS)  │
 │  core/      API, events, store│  Bearer   │        ↓ 127.0.0.1:8740                 │
 │  platform/  storage, audio,   │  token    │ phone_bridge.py  (REST + SSE + push)   │
 │             notify, haptics   │ ◀──────── │   ↓ same command queue as voice/HUD     │
 │  ui/, screens/                │  SSE/push │ main.py → intent → actions → speak()    │
 └───────────────────────────────┘           └────────────────────────────────────────┘
```

* **Phone app = installable PWA** (Add to Home Screen → full-screen, own icon, push notifications on
  iOS 16.4+). Free, no Mac, no developer account. Plain ES modules, no build step.
  The same code runs on Android, and can later be wrapped with **Capacitor** for App Store / Play
  builds — only `src/platform/` gets native implementations.
* **PC = server.** `phone_bridge.py` exposes AURA over a small authenticated API and serves the app.
  Every phone message goes into the *same command queue* as voice, the HUD box and Mini AURA, so all
  ~55 actions (agents, builder, email, research, PC control…) work from the phone with no duplicated
  business logic.
* **Network = Tailscale** (free for personal use): phone and PC join a private network; Tailscale Serve
  gives the bridge a real HTTPS certificate (needed for mic, install, service worker, push). Works at
  home and away. The bridge itself only listens on 127.0.0.1.
* **Security**: pairing code shown on the PC (proves physical access) → long random device token,
  stored hashed on the PC, revocable, every request needs it. No API keys ever reach the phone.
  Dangerous actions keep their PC-side confirmation (shutdown/restart ask "say yes", answered from
  the phone). File downloads are limited to AURA's own output folders.
* **Voice**: phone records (MediaRecorder) → `/api/voice` → Whisper on the PC (key stays on PC) →
  command. Replies: `/api/tts` → AURA's own neural voice (edge-tts) → played on the phone.
  A command that came from the phone is answered **on the phone** (the PC stays quiet).
* **Questions AURA asks back** (journal, log a session, confirm shutdown) use `voice.listen()`. When the
  command came from the phone, `listen()` waits for the phone's next message instead of the PC mic.
* **Realtime**: Server-Sent Events (`/api/events`) carry log lines, thinking / listening / talking,
  toasts, "AURA is asking", data changes. Reconnects resume from `Last-Event-ID`.
* **Notifications**: Web Push (VAPID) from the PC for reminders, focus timer, important emails, and
  AURA's answers when the app is in the background.
* **Offline / PC off**: last state is cached on the phone and shown with an "PC offline" banner;
  nothing pretends to work. Messages typed while offline are kept as a draft, not silently queued.

### Code layout (`mobile/`)
| Folder | Platform-independent? | What |
|---|---|---|
| `src/core/` | yes | API client, SSE event stream, state store, formatting, models |
| `src/platform/` | **no** — the only place iOS/Android/web differ | storage, audio record/play, notifications/push, haptics, device |
| `src/ui/` | yes | design tokens (from the HUD), components |
| `src/screens/` | yes | one module per screen |

## Feature inventory & parity

Legend — **A** portable · **B** mobile-specific implementation · **C** via the PC (companion) ·
**D** iOS API · **E** Android API later · **F** platform-independent backend.
Status — ✓ built (what exactly was tested is in the test log at the end) ·
→ OS limitation (documented below) · ! to do.
**Not yet tried on a physical iPhone** (Safari specifics: mic recording format, push via Apple, Home Screen install).

### Talking to AURA
| PC feature | Class | Mobile implementation | Status |
|---|---|---|---|
| Typed commands (HUD box, Mini, terminal) | C | Comms screen command box → same queue | ✓ |
| Command history (↑) & suggestions | A | History + suggestion list above the input | ✓ |
| Quick chips (briefing, research, files, remind, focus, vision, CDC, dashboard, journal, log …) | A | Same chips on Core + Comms | ✓ |
| Wake word "AURA" (always listening) | → | iOS doesn't allow an always-on mic for apps in the background. Replaced by: tap the core or the mic (push-to-talk) | → |
| Push-to-talk (Ctrl+Alt+A, Mini mic) | B | Hold/tap mic → record → Whisper on PC | ✓ |
| AURA's voice replies | B | `/api/tts` audio played on phone; toggle in settings | ✓ |
| Interrupt ("stop") | B | Tap the reactor / stop button stops phone playback (+ PC speech) | ✓ |
| Conversation log (shared with Mini) | C | Comms log, synced live, persisted on PC | ✓ |
| Thinking / listening / talking states | C | Status pill + reactor animation via SSE | ✓ |
| AURA asking a follow-up (journal, log session, confirm shutdown) | C | "AURA is asking" banner, reply by text or voice | ✓ |
| Ctrl+Alt+Space summon | D | Home-screen icon opens straight into AURA | ✓ |

### Every intent action (run on the PC)
open/close app, open/save website, open & type, YouTube, Spotify, Roblox, write note, type text, hotkey,
mouse, desktop agent (`screen_action`), browser agent (`web_task`), app builder
(build/modify), explain copied error, PubMed, paper library, PDF analysis/compare, PRISMA, CDC WONDER,
phone (ADB), email, file search, web search, web briefing, greeting/full schedule, date/time, tasks,
reminders, focus, calendar event, memory forget/wipe, location, volume, screenshot, shutdown/restart, DND,
dashboard, weekly report, journal, log session, camera.
→ **C** for all: sent through the bridge, executed by the PC exactly as before (tested: date/time, add task by voice, shutdown confirmation answered "no" from the phone). ✓

Results that are *files on the PC* get a phone view:
| Output | Mobile | Status |
|---|---|---|
| Web briefing pages (`Face/briefings`) | Recent tab → open the briefing inside the app | ✓ |
| Screenshots (`Pictures/AURA_Screenshots`) | Viewable / downloadable on the phone | ✓ |
| File-search results (`search_results/…`) | List the found files, download to the phone | ✓ |
| PRISMA diagram, CDC WONDER outputs | Open / download on the phone | ✓ |
| PDFs for analysis | Upload a PDF from iOS Files into AURA's library folder | ✓ |

### HUD panels
| PC | Mobile | Status |
|---|---|---|
| Boot sequence, top bar, status pill, clock | Boot splash, top bar | ✓ |
| Reactor core + callouts (CPU, RAM, open tasks, next reminder) | Core screen | ✓ |
| System core gauges, disk, power, network, processes | System screen | ✓ |
| Focus timer (start/pause/resume/stop) | Core card + controls | ✓ |
| Today stats, Dev status (last build) | Core cards | ✓ |
| Tasks / Reminders / Notes / Recent tabs | Lists screen (segmented) | ✓ |
| Module status strip, uptime | System screen | ✓ |
| Toasts | In-app toasts + push notifications | ✓ |
| HUD cat | Cat on the Core screen | ✓ |

### Other windows
| PC | Mobile | Status |
|---|---|---|
| Mini AURA (chat, mic, DND, info chips, email list) | Comms + chips + email sheet | ✓ |
| Important emails (list, dismiss one / all) | Emails sheet | ✓ |
| Do Not Disturb | Toggle (top bar) | ✓ |
| Dashboard (weekly summary, 8-week trends, profile, papers, sessions) | Dashboard screen | ✓ |
| Schedule / daily briefing card | Briefing card on Core | ✓ |
| Log-a-session dialog (time + rating) | Log sheet | ✓ |
| Vision mode (live camera, identify, deep scan) | Phone camera photo → identify / deep scan (same AI), or view the PC camera | ✓ |
| Explain cat (Ctrl+Alt+E on highlighted text) | Explain screen: paste / share text → same explainer, follow-ups, sources | ✓ |
| CDC WONDER extractor | CDC screen using the same job API; outputs downloadable | ✓ |
| Login (Firebase) | Pairing replaces it for the phone (device must be approved on the PC) | ✓ |
| Tray menu (show HUD / dashboard / schedule / quit) | More screen actions | ✓ |

### Background behaviour
| PC | Mobile | Status |
|---|---|---|
| Reminders firing | Push notification | ✓ |
| Focus session / break finished | Push notification | ✓ |
| Important email announcements | Push notification | ✓ |
| Away / DND catch-up | Unchanged on PC; DND toggle on phone | ✓ |
| Morning briefing | Unchanged on PC; available from phone chip | ✓ |

### Documented OS limitations
* **Always-listening wake word** — iOS stops web apps' microphone in the background, and Apple doesn't
  let third-party apps run a wake word anyway. Alternatives: mic button, Siri Shortcut.
* **Haptics** — Safari has no vibration API; `platform/haptics` is a no-op on iOS web and becomes real
  with Capacitor (`@capacitor/haptics`).
* **Share sheet into the app** — iOS web apps can't be share targets; the Explain screen has a Paste
  button instead.
* **Service worker in Claude's in-app browser** — not supported there (works in Edge/Chrome/Safari).

## Test log (2026-10-02)
Run against AURA's real code (`main.process_command`, real Groq / Whisper / edge-tts) through a test
copy of the bridge, in Claude's in-app browser and Microsoft Edge (Playwright) at iPhone size.

| Area | How it was tested | Result |
|---|---|---|
| Pairing | wrong code, code reuse, 5-miss lockout, expiry, revoke (unit tests + UI) | ✓ |
| Auth | every /api route without / with bad token → 401 | ✓ |
| Typed command | "what time is it" from COMMS → real reply streamed back, status PROCESSING → STANDING BY | ✓ |
| Voice in | spoken clip → `/api/voice` → Whisper → "add … to my list" → task added | ✓ |
| Voice out | `/api/tts` returns AURA's neural voice (MP3) | ✓ (playback on a real iPhone not yet heard) |
| Ask-back | "shut down my PC" → AURA asks → "no" from the phone → "Okay, cancelled." | ✓ |
| Tasks | add / mark done from LISTS | ✓ |
| Explain | explanation + 3 sources + follow-up (API) | ✓ |
| Vision | photo → identify (3 s) and deep scan (API) | ✓ (live camera UI not run - no camera in the test browser) |
| CDC WONDER | setup, ICD-10 check incl. a bad code (API + page opens) | ✓ (no extraction started - takes hours and hits CDC) |
| Files | list briefings, signed view link, single-file scoping, traversal blocked | ✓ |
| Upload | file → Documents\AURA Uploads → readable on PC (then deleted) | ✓ |
| Dashboard, Emails, Settings, Journal, Log session | pages open with real data | ✓ (journal / session not saved, to keep your data clean) |
| Push | Edge subscribed → PC sent → notification shown by the service worker | ✓ (Apple's push service not yet tried) |
| Offline | service worker caches 38 files; with network cut the app opens and explains | ✓ |
| Themes | dark (default) and light | ✓ |
| PC regression | all modules compile, 63/63 unit tests (54 existing + 9 bridge) | ✓ |

## Phone is its own device (2026-10-03)
Asked on the phone → it happens **on the phone**; the PC is used only when the message says
"on my PC / laptop / computer" (`phone_actions.py`, `phone_bridge.phone_target()`).
* While AURA handles a phone request, `webbrowser.open` / `os.startfile` are caught: websites, YouTube,
  Spotify (as open.spotify.com links → the app), web briefings, PRISMA, found files and email digests
  open on the phone (one tap - iOS needs a real tap to open another app).
* Apps: official iPhone URL schemes (WhatsApp, Instagram, Maps, Gmail, Notes, Spotify, Shortcuts…).
* "run shortcut <name>" runs an iOS Shortcut - the way to control phone settings, alarms, messages.
* Camera / dashboard / CDC / journal → the phone's own screens; the PC's windows stay closed.
* PC-only actions (typing, mouse, screen control, browser agent, building apps, shutdown…) are
  not done from the phone without "on my PC" - AURA offers a one-tap **Do it on my PC**.
* **iOS limit:** no app can see or control the iPhone's screen or other apps. AURA says so and offers
  the closest thing (screenshot → VISION, Shortcuts).
Tested: 10 phone commands through the real brain → correct phone actions, nothing opened on the PC.

## Vision on the phone (2026-10-03)
Better than the PC camera window: on-device detection + tracking (COCO-SSD lite) and recognition of the
user's own things (MobileNet fingerprints, matched on the phone; stored on the PC in
`aura_vision_memory.json`), front/back camera, zoom, freeze, torch, library photos, and 9 modes backed by
`vision_lab.py` (Gemini vision when a key is set, else Groq). AI libraries are cached by the service worker.
Tested (Edge, fake camera from a real photo): engine online, 9 FPS live detection, learn → recognise ★,
READ, flip + zoom, and every analysis mode's JSON (ask, translate→Urdu, tutor, flashcards, quiz, solve, food).
Not yet tried on the iPhone camera.

## Start-up: one pack, kept on the phone (2026-10-03)
The iPhone kept failing to start ("Failed to load app.js"): the request log showed some of the ~35 module
files never reached the PC over the VPN (worst right after the phone restarts). Fix:
* `app_bundle.py` packs every module (+ CSS + the HUD cat) into ONE download (`/app-pack.json`, ~285 KB),
  rebuilt automatically when files change. Modules become functions in a tiny registry (live-getter exports).
* `mobile/boot.js` saves the pack in the phone's localStorage and **starts from it** - opening AURA no longer
  needs the network; `/app-version` is checked in the background and a newer pack applies on the next launch.
  First download retries 8×.
* The service worker only keeps the start-up files (page, boot.js, errors.js) and never answers a script
  request with the HTML page (the old fallback that broke start-up). `errors.js` self-repairs once.
Tested in Edge (iPhone UA): fresh start; relaunch with every app file blocked (starts from the saved pack);
first 3 downloads failing (retries, starts); pairing + lazily-loaded tools.

## NEXUS - extended systems (2026-10-03)
Seven modules under MORE → NEXUS (`src/screens/tools/nexus/`), brain in `nexus.py`, routes `/api/nexus/*`.
The phone senses, the PC thinks.

| Module | Phone | PC (`nexus.py`) |
|---|---|---|
| LIVE | `audio.listen()` (always-open mic, voice detection, 16 kHz WAV with pre-roll) + optional camera frame | Whisper → AI (vision model when a frame is sent) → spoken answer + optional AURA command (sent through the normal queue) |
| BABEL | same mic loop, split face-to-face UI | Whisper with language auto-detect → translate → native edge-tts voice per language |
| ECHO | continuous 22-30 s clips, upload queue with retries | per-clip transcript; notes / flashcards / Q&A over one or all recordings; `Documents\AURA Echo` (+ .md) |
| RADAR | GPS, compass (`sensors.js`), scope / AR / list, sun-moon-Qibla maths on the phone | OpenStreetMap places (Overpass, retries + area cache), reverse geocode (Nominatim), "ask about here" |
| PULSE | camera PPG (fingertip, torch if allowed), accelerometer breathing, reaction test; autocorrelation + sub-sample peak | readings in `aura_bio.json` |
| LINK | live JPEG screen, touch / trackpad / gyro air-mouse, keys, clipboard | `ImageGrab` + pyautogui (clamped away from fail-safe corners), PC toast when the phone connects; off with `PHONE_REMOTE_CONTROL = False` |
| PROTOCOLS | list, editor, MISSION sheet, live progress from `protocol` events | steps run one at a time through the command queue (waits for AURA to go idle); "run X protocol" by voice on phone or PC; mission planner |

iOS limits: the mic stops when the app is in the background (modules keep the screen awake with the Wake Lock API);
motion/compass need one tap to allow; torch support depends on iOS.
Tested: 72/72 unit tests; real Whisper/AI/TTS/OSM for every PC function; phone UI at iPhone size on a test bridge (input handler stubbed);
PULSE maths on synthetic signals. Not yet on the physical iPhone.

## Phone brain - AURA with the PC off (2026-10-03)
The original decision ("everything runs on the PC, no keys on the phone") meant PC off = no AURA. Now the phone
also has its own brain (`src/brain/`), used whenever the PC is unreachable:

* **Kit**: `/api/brain/kit` (paired devices only) gives the phone the Groq/Gemini keys + models, memory facts, notes
  and protocols; refreshed every ~10 min while the PC is reachable. Unpairing deletes it.
* **Routing**: `core/api.js` hands every request (except pairing / state / events / sync) to `brain/local.js` while
  `conn === 'offline'`, or when a request fails on the network. Screens don't change - local answers have the PC's shapes.
* **Thinking**: `brain/llm.js` calls Gemini (native API, images) then Groq (chat, vision, Whisper, built-in
  `browser_search` for web answers) straight from the phone - CORS allows both; CSP `connect-src` lists them.
  `brain/mind.js` = the phone's intent + actions; `brain/skills.js` = vision_lab / nexus / explainer prompts.
* **Data + sync**: tasks, reminders, notes, chat, memories, journal, sessions, PULSE, ECHO and protocol edits go into an
  outbox, replayed by `/api/brain/sync` (`phone_sync.apply`, idempotent by op id). PC-only requests become "jobs
  waiting for the PC" - offered (not auto-run) when it's back.
* **Voice**: replies use the iPhone's speech synthesis (`platform/speech.js`); `audio.playSequence` accepts `{ speak }`.
* **Start-up with the PC off** already worked: page/boot from the service worker, app from the saved pack.

Tested: Edge with an iPhone UA, PC really off (bridge killed) - app opens, chat/tasks/reminder/web search/memory work,
PC-only job queued; PC back → outbox synced, job offered. Skills with live AI in Node; maps from a browser. 75/75 unit tests.


## Start-up never waits for the PC (2026-10-04)
Bug: with the PC off behind Tailscale, requests to it HANG (they don't fail), and the service worker was network-first
with no time limit - the app stayed on a black screen. Now `sw.js` (v10) serves the start-up files from the cache first
and refreshes them in the background (4 s limit; install keeps old copies if the PC can't be reached; old caches are only
deleted once the new one has the page). `app.js` probes `/api/hello` with a 3.5 s limit at start-up / on return, and
`api.js` hands requests stuck on an unreachable PC to the phone brain when it switches. Tested with a "blackhole" server:
UI in 0.2 s, phone brain in 3.7 s.
