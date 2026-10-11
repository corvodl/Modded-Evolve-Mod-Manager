# Project conventions

- Keep public What's New entries brief: at most three short bullets per version.
- Maintain public notes in `source/release_notes.py`; keep README notes and release descriptions synchronized with it.
- Exclude Developer tab features, access details and changes from all What's New content, release descriptions and update/release names. Use curated notes, never automatically generated commit summaries.
- Never store the Developer access code in plaintext, examples or tests. Do not persist unlocked state. Require authentication on every entry and discard protected content when leaving.
- Preserve the stable-only automatic updater and optional experimental offers during manual checks.
- Windows releases must pass the existing build, regression, screenshot and package checks before publication.
