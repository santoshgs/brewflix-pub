# Working on Brewflix

- **Changelog:** every commit that changes code or docs adds its own entry at
  the top of `CHANGELOG.md` (under today's date heading), in the same commit.
  One entry per commit: a short title and a few plain sentences on what changed
  and why. Daily `backup: YYYY-MM-DD` data commits aren't listed.
- **Privacy rules:** Untappd: keep where the beer was had (`venue_name`); never
  store comments, where it was bought, or clock times (see
  `PRIVATE_FIELDS` and `fix_checkin` in `scraper/untappd.py`). Foursquare/Swarm:
  places, the owner's own comments, times and tagged friends are stored by
  design; never store likes or other people's comments (`DROP_FIELDS` in
  `scraper/foursquare.py`).
- **No personal data in this template:** keep `targets.json` placeholders,
  empty `backups/` and `imports/`, and blank Access values in `wrangler.jsonc`.
- Run `python -m pytest -q tests/` before committing.
