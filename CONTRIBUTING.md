# Contributing to AURA

Thanks for your interest! Bug reports, fixes and improvements are welcome.

## Before you start

- AURA is Windows-only (Windows 10/11, Python 3.13).
- Set up a development copy as in the README (`python -m venv .venv`, `pip install -r requirements.txt`,
  `copy .env.example .env`).
- **Never commit secrets or personal data:** `.env`, `google_*.json`, `aura_*.json`, `browser_data/`, logs.
  They are in `.gitignore` — please don't force-add them. Use obviously fake values in tests.

## Making changes

1. Fork the repo and create a branch: `git checkout -b fix/short-description`.
2. Keep changes focused; match the style of the surrounding code (plain Python, comments that explain *why*).
3. Run the tests:
   ```powershell
   python -m unittest discover -s tests
   ```
4. Add or update tests for logic you change (`tests/`). The tests must not need real API keys or network access.
5. If your change touches PC control, code execution, the microphone, camera, screen or networking, explain the
   safety impact in the PR and keep the protections in `safety.py`, `desktop_agent.py` and `web_agent.py` intact.
6. Open a pull request describing what changed and how you tested it.

## Reporting bugs

Open an issue with your Windows and Python versions, what you said/typed, what you expected and what happened,
plus the relevant console output. **Remove keys, email addresses and personal content** from logs before
posting. Security problems: see [SECURITY.md](SECURITY.md).

## Licence

By contributing you agree that your contributions are licensed under the [MIT License](LICENSE).
