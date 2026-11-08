# Working on Brewflix

- **Changelog:** every commit that changes code or docs adds its own entry at
  the top of `CHANGELOG.md` (under today's date heading), in the same commit.
  One entry per commit: a short title and a few plain sentences on what changed
  and why. Daily `backup: YYYY-MM-DD` data commits aren't listed.
- **Privacy rules:** never store venues, comments or clock times (see
  `PRIVATE_FIELDS` and `fix_checkin` in `scraper/untappd.py`).
- **No personal data in this template:** keep `targets.json` placeholders,
  empty `backups/` and `imports/`, and blank Access values in `wrangler.jsonc`.
- Run `python -m pytest -q tests/` before committing.
