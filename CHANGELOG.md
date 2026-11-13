# Changelog

Every commit to Brewflix gets its own entry here, newest first, added in the
same commit as the change. In your own copy, the backup job's daily data
commits (`backup: YYYY-MM-DD`) only update `backups/` and aren't listed.

## 2026-11-13

### Catch every Untappd check-in through the RSS feed
Signed out, the Untappd profile shows only the latest 5 check-ins, so a night
with more than that lost some. Each run now reads your Untappd RSS feed
(secret `UNTAPPD_RSS_URL`, latest 25 check-ins) and opens the public page of
any check-in not yet backed up for its beer, rating, serving, location, photo
and flavour tags. A saved Untappd sign-in (secret `UNTAPPD_STORAGE_STATE`,
stored by the new `scripts/untappd_save_signin.sh`) remains the fallback for
more than 25 check-ins between runs. If neither can cover a gap, the run still
passes and the site's Untappd status says check-ins may be missing. The feed
link is never written to the log.

### Keep where an Untappd beer was had
Untappd check-ins now record their location (`venue_name`, `venue_id`,
`venue_url`; for example "Untappd at Home" or a bar), so they can be lined up
with Swarm check-ins and other data. New check-ins get it from the feed;
existing ones are re-read from their check-in pages, 120 per daily run, or 600
on a manual run with *full* ticked. An Untappd data export also supplies the
venue's city and coordinates. The site shows the location under each beer and
search finds it. Comments, where a beer was bought, and clock times are still
never stored. A check-in page that loads without its flavour tags no longer
wipes tags already saved.

## 2026-11-11

### Tips and photos pop-overs, date range filter, icons on every tab
The Tips and Photos counters on the Swarm profile card now open a pop-over:
all the tips you wrote (newly backed up to `backups/foursquare_tips`, without
other people's likes), and every check-in photo in one grid that opens full
size. Both check-in tabs get From and To date boxes beside the search; the
list, the count and the stats cards follow the range. The summary tiles on the
IMDb, Untappd and Unique beers tabs get icons like the Swarm ones. If
Foursquare stops returning tips, the saved ones stay and the run still passes.

### Say that the "can't connect to localhost" page is the expected result
After approving on Foursquare the browser shows a connection error for
`localhost`, which looked like a failure. The token helper and the README now
say this is expected and that the code to paste is in that address. The helper
also accepts the address without `https://` and ignores the `#_=_` Foursquare
adds to the end.

### Foursquare setup without using your own computer
The token helper is now meant for a Codespace: it saves the token straight
into the `FOURSQUARE_TOKEN` repository secret through the GitHub CLI and only
shows it if that isn't permitted. A new manual workflow, *Foursquare check*,
runs the endpoint probe on GitHub with that secret. The setup steps in the
README are rewritten to match.

### Add Foursquare / Swarm check-ins
A third service: Swarm check-ins are backed up through Foursquare's API with
place, category, location, your comment, tagged friends, time and photos, plus
a roll-up of places and a profile file. It runs in the daily job once you add
a `FOURSQUARE_TOKEN` secret (`scripts/foursquare_token.py` creates one) and
fill in the `foursquare` section of `targets.json`; without them it is
skipped. Foursquare's own data export can be dropped into
`backups/foursquare_export/` to fill gaps. The site gains a Swarm check-ins
tab with a profile card and a Timeline card (check-ins by year, a zoomable
map, rankings by country, province/state and city, and a sortable list of
places). The Worker checks the Cloudflare Access token before serving the
Swarm data files as well as the images. Likes and other people's comments are
never stored. Venue photos are not fetched yet: the place thumbnail is the
category icon.

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
