# Changelog

Every commit to Brewflix gets its own entry here, newest first, added in the
same commit as the change. In your own copy, the backup job's daily data
commits (`backup: YYYY-MM-DD`) only update `backups/` and aren't listed.

## 2026-11-09

### Report how many Untappd check-in pages were actually read
The run log could say 120 pages were read when Untappd stopped the run early.
The count now reports the pages actually read, out of those queued.

### Add unique beers from check-ins when the list hasn't caught up
The unique-beers list can only be read logged in, so beers rated since the last
logged-in backfill were left out. Any beer that has check-ins but no row now
gets one built from those check-ins, with ABV and IBU read from the beer page.
Style is filled in by a logged-in backfill. Adds a test where the list leaves a
beer out.

### Read the beer page's star rating for global ratings
The beer page has no "Global" label: its rating is stars with the number in
brackets beside the rating count. The job now reads that row, and logs it if
it can't. Bumping `DETAILS_VERSION` to 3 gives check-ins tried under the old
parser another attempt, so existing check-ins are re-read over the next few
daily runs.

### Read missing Untappd global ratings from the beer page
The check-in page doesn't carry a beer's global rating, so when it has none the
job now reads it from the beer's own page, once per parser version, so a rating
that never appears isn't re-read every day. The run log shows the beer page
markup if that also fails. Adds a `global_v` column to the check-in backup to
record that attempt.

### Log check-in page markup when no global rating is found
The first daily run after the global-rating fix filled none, so the run log
now shows the page markup around the word "global" when a check-in page
yields no rating, so the parser can be adjusted from the real page.

### Fill in global ratings for new Untappd check-ins
Check-ins rated after the last logged-in backfill were saved with no global
rating. The check-in feed doesn't show it, and the daily job can't read the
unique-beers list while logged out, so nothing ever filled it in. The job now
reads the global rating from each check-in page it already opens for flavour
tags, and only fills a blank, never overwrites one. Bumping `DETAILS_VERSION`
to 2 re-reads existing check-ins once, so earlier gaps close over the next few
daily runs (120 pages per run). Adds a test with a check-in page that shows a
global rating.

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
