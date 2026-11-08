# Changelog

Every commit to Brewflix gets its own entry here, newest first, added in the
same commit as the change. In your own copy, the backup job's daily data
commits (`backup: YYYY-MM-DD`) only update `backups/` and aren't listed.

## 2026-11-08

### Add this changelog
Started `CHANGELOG.md` with an entry for every earlier commit, and a
`CLAUDE.md` note so changes keep it up to date.

### Fix new IMDb ratings saved without rating or date · `e15d129`
IMDb's ratings page fills in your rating and "Rated on" date after the page
first appears. Daily runs read it too early, so new titles were saved without
them and sank to the bottom of the list. The scraper now waits for the page to
finish loading, re-reads any title still missing a rating or date on later
runs, and logs the page structure if one is missing. Director and runtime
lookups now go through the browser (plain requests were blocked by IMDb) and
are retried after a block. A copy that isn't set up yet (placeholders still in
`targets.json`) no longer writes or commits a status file every day. Adds a
test where ratings arrive late.

### Director and runtime under each IMDb title; profile links · `7b6ea43`
A third line under each title shows the director and runtime in minutes, and
directors are searchable. New titles get both from IMDb's page data; titles
still missing them are looked up once. The header links to your IMDb and
Untappd profiles, using each site's own icon, and stays hidden until
`targets.json` is filled in.

## 2026-11-07

### First public version · `ebdffec`
Daily Playwright scrapers in GitHub Actions save IMDb ratings and Untappd
check-ins as JSON and CSV. A Cloudflare Worker serves a searchable site with
IMDb Insights and Untappd Drink stats. Photos, beer labels and brewery logos
are copied to a private Cloudflare R2 bucket and served only behind Cloudflare
Access. Venues, comments and clock times are never stored. Includes a
Codespace setup for the one-time logged-in Untappd backfill and offline
end-to-end tests.
