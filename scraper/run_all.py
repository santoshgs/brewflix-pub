"""Entry point used locally and by the daily GitHub Action.

1. Normalize any native exports (Step 1).
2. Scrape each service — full backfill when there's no backup yet,
   incremental (newest items only) otherwise.
3. Write backups/status.json and copy everything into site/data/ for
   the Cloudflare Pages viewer.

One service failing never discards the other's data; the exit code is
non-zero so the Action run shows red and GitHub emails you.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import traceback

from common import DATA_ROOT, ROOT, TARGETS, now_iso, log
import foursquare
import imdb
import normalize_exports
import untappd

SERVICES = {"imdb": imdb.run, "untappd": untappd.run, "foursquare": foursquare.run}
BACKUP_FILE = {"imdb": "imdb_ratings", "untappd": "untappd_checkins", "foursquare": "foursquare_checkins"}


def set_up(name: str) -> bool:
    """A service is on when targets.json has its section with no YOUR_… placeholders."""
    return name in TARGETS and "YOUR_" not in json.dumps(TARGETS[name])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=SERVICES)
    ap.add_argument("--full", action="store_true", help="force a full re-walk")
    ap.add_argument("--headful", action="store_true")
    a = ap.parse_args()

    configured = [n for n in SERVICES if set_up(n)]
    if not configured:
        log("Nothing set up yet: put your IMDb user ID, Untappd username and/or Foursquare profile in targets.json")
        return 0

    status_path = DATA_ROOT / "backups" / "status.json"
    status = json.loads(status_path.read_text()) if status_path.exists() else {}
    normalize_exports.main()

    failed = []
    for name, fn in SERVICES.items():
        if a.only and name != a.only:
            continue
        if not set_up(name):
            log(f"{name}: not set up in targets.json (missing, or still has YOUR_… placeholders); skipping")
            continue
        has_backup = (DATA_ROOT / f"backups/{BACKUP_FILE[name]}.json").exists()
        try:
            added = fn(full=a.full or not has_backup, headful=a.headful)
            if name == "foursquare" and foursquare.LAST_RUN.get("skipped"):
                continue  # no token: nothing was attempted, so leave any earlier status as it is
            status[name] = {"ok": True, "last_success": now_iso(), "added_last_run": added,
                            "last_error": None}
            if name == "foursquare":
                status[name].update({k: foursquare.LAST_RUN.get(k) for k in ("api_count", "stored_count")})
        except Exception as e:  # keep going; record why
            traceback.print_exc()
            failed.append(name)
            status.setdefault(name, {})
            status[name].update({"ok": False, "last_attempt": now_iso(), "last_error": str(e)[:300]})

    # copy your own check-in photos (Untappd and Swarm) to the private R2 bucket (skipped without R2 secrets)
    do_untappd = (not a.only or a.only == "untappd") and set_up("untappd")
    do_swarm = (not a.only or a.only == "foursquare") and set_up("foursquare") and (DATA_ROOT / "backups/foursquare_checkins.json").exists()
    if do_untappd or do_swarm:
        try:
            import photos

            n = (photos.backup() + photos.backup_labels() if do_untappd else 0) + (photos.backup_foursquare() if do_swarm else 0)
            status["photos"] = {"ok": True, "last_success": now_iso(), "added_last_run": n, "last_error": None}
        except Exception as e:
            traceback.print_exc()
            failed.append("photos")
            status.setdefault("photos", {}).update({"ok": False, "last_attempt": now_iso(), "last_error": str(e)[:300]})

    # profile links for the site header (skipped while targets.json has placeholders)
    profiles = {}
    imdb_id = TARGETS.get("imdb", {}).get("user_id", "")
    if imdb_id and "YOUR_" not in imdb_id:
        profiles["imdb"] = f"https://www.imdb.com/user/{imdb_id}/"
    up = TARGETS.get("untappd", {}).get("profile_url", "")
    if up and "YOUR_" not in up:
        profiles["untappd"] = up
    fp = TARGETS.get("foursquare", {}).get("profile_url", "")
    if fp and "YOUR_" not in fp:
        profiles["foursquare"] = fp
    status["profiles"] = profiles

    status["generated_at"] = now_iso()
    status_path.parent.mkdir(exist_ok=True)
    status_path.write_text(json.dumps(status, indent=2))

    data = DATA_ROOT / "site" / "data"
    data.mkdir(parents=True, exist_ok=True)
    for f in (DATA_ROOT / "backups").glob("*.*"):
        if f.suffix in (".json", ".csv"):
            shutil.copy2(f, data / f.name)
    log(f"done; failed: {failed or 'none'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
