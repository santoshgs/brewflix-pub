"""Step 1: turn native exports into clean backups/ files.

Looks in the repo root and imports/ for:
  imdb_ratings.csv        (IMDb > Your Ratings > Export)
  untappd_export.json/.csv (Untappd Insiders > Download History)
and in backups/ or imports/ for:
  foursquare_export/ or foursquare_export.zip (Foursquare > Settings > data export)

Rows are merged into the same stores the scrapers use, so an export
gives a full history and the daily scrape keeps it current.
Prints which services were covered (one per line) so run_all.py can
decide what still needs a full scrape.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from common import DATA_ROOT, TARGETS, Store, log, to_float
from common import local_date
from untappd import BEER_FIELDS, CHECKIN_FIELDS, PRIVATE_FIELDS, fix_beer, fix_checkin

SEARCH_DIRS = [DATA_ROOT, DATA_ROOT / "imports"]


def find(name: str) -> Path | None:
    for d in SEARCH_DIRS:
        p = d / name
        if p.exists():
            return p
    return None


def norm_date(v: str | None) -> str | None:
    if not v:
        return None
    from dateutil import parser as dp  # tolerant of IMDb/Untappd formats

    try:
        return dp.parse(v).isoformat()
    except (ValueError, OverflowError):
        return v


def imdb_from_export(path: Path) -> int:
    cfg = TARGETS["imdb"]
    store = Store(cfg["output_base"], "const_id", cfg["fields"], sort_by="date_rated")
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            const = r.get("Const") or r.get("const")
            if not const:
                continue
            store.upsert(
                {
                    "const_id": const,
                    "title": r.get("Title"),
                    "year": int(r["Year"]) if (r.get("Year") or "").isdigit() else None,
                    "user_rating": to_float(r.get("Your Rating")),
                    "imdb_rating": to_float(r.get("IMDb Rating")),
                    "date_rated": (r.get("Date Rated") or "")[:10] or None,
                    "title_type": r.get("Title Type"),
                    "url": r.get("URL") or f"https://www.imdb.com/title/{const}/",
                    "genres": r.get("Genres"),
                    "runtime_mins": to_float(r.get("Runtime (mins)")),
                    "directors": r.get("Directors"),
                    "num_votes": to_float(r.get("Num Votes")),
                    "source": "export",
                },
                save_every=0,
            )
    store.save()
    log(f"IMDb export: {store.added} new rows from {path.name}")
    return len(store)


def _untappd_rows(path: Path) -> list[dict]:
    if path.suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else data.get("items", [])
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def untappd_from_export(path: Path) -> int:
    cfg = TARGETS["untappd"]
    checkins = Store(cfg["output_base"], "checkin_id", CHECKIN_FIELDS, sort_by="checkin_date", drop=PRIVATE_FIELDS, fix=fix_checkin)
    beers = Store("backups/untappd_beers", "beer_id", BEER_FIELDS, sort_by="last_checkin", fix=fix_beer)
    for r in _untappd_rows(path):
        cid = r.get("checkin_id")
        if not cid:
            continue
        rec = {
            "checkin_id": str(cid),
            "beer_id": str(r.get("bid") or "") or None,
            "beer_name": r.get("beer_name"),
            "brewery_name": r.get("brewery_name"),
            "style": r.get("beer_type"),
            "abv": to_float(r.get("beer_abv")),
            "ibu": to_float(r.get("beer_ibu")),
            "user_rating": to_float(r.get("rating_score")),
            "global_rating": to_float(r.get("global_weighted_rating_score") or r.get("global_rating_score")),
            "checkin_date": local_date(r.get("created_at")),
            "serving_type": r.get("serving_type"),
            "photo_url": r.get("photo_url"),
            "checkin_url": r.get("checkin_url"),
            # where the beer was had; the export also knows the venue's city and coordinates
            "venue_name": r.get("venue_name"),
            "venue_city": r.get("venue_city"),
            "venue_state": r.get("venue_state"),
            "venue_country": r.get("venue_country"),
            "venue_lat": to_float(r.get("venue_lat")),
            "venue_lng": to_float(r.get("venue_lng")),
            "source": "export",
        }
        checkins.upsert(rec, save_every=0)
        if rec["beer_id"]:
            prev = beers.records.get(rec["beer_id"], {})
            dates = [d for d in (prev.get("first_checkin"), prev.get("last_checkin"), rec["checkin_date"]) if d]
            beers.upsert(
                {
                    "beer_id": rec["beer_id"],
                    "beer_name": rec["beer_name"],
                    "brewery_name": rec["brewery_name"],
                    "style": rec["style"],
                    "abv": rec["abv"],
                    "ibu": rec["ibu"],
                    "user_rating": rec["user_rating"] or prev.get("user_rating"),
                    "global_rating": rec["global_rating"],
                    "checkin_count": (prev.get("checkin_count") or 0) + 1,
                    "first_checkin": min(dates) if dates else None,
                    "last_checkin": max(dates) if dates else None,
                    "beer_url": r.get("beer_url"),
                },
                save_every=0,
            )
    checkins.save()
    beers.save()
    log(f"Untappd export: {checkins.added} new check-ins from {path.name}")
    return len(checkins)


def main() -> list[str]:
    covered = []
    if p := find("imdb_ratings.csv"):
        imdb_from_export(p)
        covered.append("imdb")
    for name in ("untappd_export.json", "untappd_export.csv"):
        if p := find(name):
            untappd_from_export(p)
            covered.append("untappd")
            break
    try:
        import foursquare

        if foursquare._export_sources() and "YOUR_" not in json.dumps(TARGETS.get("foursquare", {"x": "YOUR_"})):
            foursquare.import_export()
            covered.append("foursquare")
    except Exception as e:  # an odd export must never stop the daily backup
        log(f"Foursquare export: could not be read ({e}); the API backup still runs")
    if not covered:
        log("no native exports found; scrapers will do a full backfill")
    return covered


if __name__ == "__main__":
    print("\n".join(main()))
