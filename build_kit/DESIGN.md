# AURA build standards

These are the standards every project AURA builds must meet. The goal is not
"it runs" - it is "someone would happily use this and show it to a friend".

## Product
- Build the real thing, not a demo of it. Think about what a user of this
  app actually does all day and make those flows complete and pleasant:
  create / edit / delete / search / filter where they make sense, sensible
  defaults, keyboard support (Enter submits, Esc closes), confirmation for
  destructive actions, and useful seed/sample data so the app never opens
  to a dead, empty screen on first run.
- Every state is designed: loading (skeletons or spinners), empty (friendly
  message + the action that fills it), error (what went wrong + what to do),
  success (a toast or inline confirmation).
- Persist real data (SQLite / JSON file / localStorage as appropriate).
  Nothing important should vanish on refresh.

## Backend
- One coherent stack. Clear layers: routes/handlers -> logic -> storage.
- Validate input and return proper status codes with JSON error bodies
  (`{"error": "..."}`); never let a bad request crash the server or return
  a 500 with a stack trace.
- Port comes from the `PORT` environment variable with a sensible default.
- The server serves the frontend too, so one command starts everything.
- No secrets in code: read them from environment variables / a `.env`
  file and ship a `.env.example` with `[YOUR_..._HERE]` placeholders.

## Frontend - look like a real product
- **Pick a design direction first** (e.g. "calm productivity, soft
  neutrals + one confident accent", "bold dark dashboard", "warm editorial")
  and commit to it across every screen.
- **Colour:** a deliberate palette - one primary accent, a neutral scale for
  backgrounds/surfaces/borders/text, and semantic success/warning/danger.
  Define them as CSS custom properties. Support dark mode via
  `prefers-color-scheme` when it fits the product. Text contrast >= 4.5:1.
- **Type:** one good font family (e.g. Inter, Plus Jakarta Sans, Manrope,
  DM Sans from Google Fonts) with a clear scale - page title, section title,
  body, small/muted. Tight letter-spacing on large headings, comfortable
  line-height (1.5) on body text.
- **Space & layout:** a 4/8px spacing rhythm, generous padding, aligned
  edges, max content width (~1100-1200px) on wide screens, cards with
  subtle borders/shadows and 10-16px radii. Nothing touches the window
  edge. Fully responsive down to 375px wide (no horizontal scroll).
- **Detail:** hover/active/focus-visible states on everything clickable,
  150-250ms transitions, icons (Lucide / Heroicons / inline SVG - never
  emoji as UI icons), proper favicon and `<title>`.
- **Hierarchy:** each screen has one obvious primary action. Secondary
  actions are visually quieter. Numbers and key info are easy to scan.
- Avoid: default browser-styled buttons/inputs, Times New Roman, pure
  #000 on #fff walls of text, random colours, cramped forms, alert() for
  feedback, lorem ipsum, broken images.

## Quality gate - done means verified
- It installs and starts with the documented command, with no errors.
- Every API route the frontend calls exists and returns the shape the
  frontend expects (frontend and backend agree on paths, field names, types).
- The page loads in a real browser with **zero console errors** and no
  failed requests, and nothing on screen reads `undefined`, `NaN`,
  `null` or `[object Object]`.
- The main user flows work end-to-end (e.g. add an item -> it appears ->
  it survives a refresh).
- A `README.md` explains what it is, how to run it, and the features.
