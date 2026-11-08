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
import imdb
import normalize_exports
import untappd

SERVICES = {"imdb": imdb.run, "untappd": untappd.run}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=SERVICES)
    ap.add_argument("--full", action="store_true", help="force a full re-walk")
    ap.add_argument("--headful", action="store_true")
    a = ap.parse_args()

    status_path = DATA_ROOT / "backups" / "status.json"
    status = json.loads(status_path.read_text()) if status_path.exists() else {}
    normalize_exports.main()

    failed = []
    for name, fn in SERVICES.items():
        if a.only and name != a.only:
            continue
        if "YOUR_" in json.dumps(TARGETS.get(name, {})):
            log(f"{name}: not set up in targets.json (still has YOUR_… placeholders); skipping")
            continue
        has_backup = (DATA_ROOT / f"backups/{'imdb_ratings' if name == 'imdb' else 'untappd_checkins'}.json").exists()
        try:
            added = fn(full=a.full or not has_backup, headful=a.headful)
            status[name] = {"ok": True, "last_success": now_iso(), "added_last_run": added,
                            "last_error": None}
        except Exception as e:  # keep going; record why
            traceback.print_exc()
            failed.append(name)
            status.setdefault(name, {})
            status[name].update({"ok": False, "last_attempt": now_iso(), "last_error": str(e)[:300]})

    # copy your own check-in photos to the private R2 bucket (skipped without R2 secrets)
    if (not a.only or a.only == "untappd") and "YOUR_" not in json.dumps(TARGETS["untappd"]):
        try:
            import photos

            n = photos.backup() + photos.backup_labels()
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
