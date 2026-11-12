"""Back up Foursquare / Swarm check-ins through the Foursquare v2 API.

Usage:
  python scraper/foursquare.py          # incremental (daily)
  python scraper/foursquare.py --full   # re-walk the whole history

Needs a user OAuth token in the FOURSQUARE_TOKEN environment variable (a GitHub
secret in the daily job; see scripts/foursquare_token.py). Without it the
service is skipped, so clones of this repo never fail.

Writes:
  backups/foursquare_checkins.json/.csv   one row per check-in
  backups/foursquare_places.json/.csv     one row per venue, rebuilt each run
  backups/foursquare_profile.json         name, join date and counters
  backups/foursquare_tips.json/.csv       one row per tip you wrote

Unlike Untappd, places, your own comments ("shouts"), clock times and the
friends you tagged are kept on purpose: this is a location diary. Likes and
other people's comments are never stored.
"""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import date, datetime, timedelta, timezone

from common import DATA_ROOT, TARGETS, Store, log, now_iso

CFG = TARGETS.get("foursquare", {})
API_VERSION = CFG.get("api_version", "20260101")
PAGE = 250  # documented maximum; the loop copes with a smaller real page size

CHECKIN_FIELDS = [
    "checkin_id", "created_at", "checkin_date", "venue_id", "venue_name", "category", "category_id",
    "neighborhood", "city", "state", "country", "cc", "address", "cross_street", "postal_code",
    "lat", "lng", "shout", "with_friends", "photo_count", "photo_urls", "photo_backup",
    "category_icon", "category_icon_backup", "venue_photo_backup", "is_mayor", "private", "type",
    "checkin_url", "created_ts", "source",
]
PLACE_FIELDS = [
    "venue_id", "venue_name", "category", "neighborhood", "city", "state", "country", "cc",
    "lat", "lng", "checkin_count", "first_checkin", "last_checkin", "category_icon",
    "category_icon_backup", "venue_photo_backup",
]
TIP_FIELDS = ["tip_id", "created_at", "tip_date", "text", "venue_id", "venue_name", "category", "city", "state",
              "country", "tip_url", "photo_url", "created_ts"]
# never stored: other people's reactions and words, and API plumbing
DROP_FIELDS = ("likes", "like", "comments", "posts", "source_app", "user", "entities", "score")


def api_base() -> str:
    return (os.environ.get("FOURSQUARE_API_BASE") or CFG.get("api_base") or "https://api.foursquare.com/v2").rstrip("/")


def token() -> str | None:
    return (os.environ.get("FOURSQUARE_TOKEN") or "").strip() or None


class ApiError(RuntimeError):
    pass


def api_get(path: str, params: dict | None = None, tries: int = 4) -> dict:
    """GET one v2 endpoint and return its `response` object."""
    q = urllib.parse.urlencode({"v": API_VERSION, **(params or {})})
    req = urllib.request.Request(
        f"{api_base()}/{path.lstrip('/')}?{q}",
        headers={"Authorization": f"Bearer {token()}", "Accept": "application/json",
                 "User-Agent": "brewflix-backup (personal archive)"},
    )
    for attempt in range(1, tries + 1):
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                return json.loads(r.read().decode("utf-8")).get("response", {})
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise ApiError(
                    f"Foursquare refused the token (HTTP {e.code}). Create a new one with "
                    "scripts/foursquare_token.py and update the FOURSQUARE_TOKEN secret") from None
            if e.code in (429, 500, 502, 503, 504) and attempt < tries:
                time.sleep(min(60, 5 * 2 ** (attempt - 1)))
                continue
            raise ApiError(f"Foursquare API error on {path}: HTTP {e.code}") from None
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < tries:
                time.sleep(5 * attempt)
                continue
            raise ApiError(f"Foursquare API unreachable on {path}: {e}") from None
    raise ApiError(f"Foursquare API error on {path}")


def _ts(v) -> int | None:
    """Epoch seconds from an int, a numeric string or a date string."""
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)) or str(v).strip().lstrip("-").isdigit():
        n = int(float(v))
        return n // 1000 if n > 10 ** 11 else n  # tolerate milliseconds
    from dateutil import parser as dp

    try:
        dt = dp.parse(str(v))
    except (ValueError, OverflowError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def _name(u: dict) -> str:
    return " ".join(x for x in (u.get("firstName"), u.get("lastName")) if x).strip()


def normalise(item: dict) -> dict | None:
    """One API (or export) check-in object -> one flat backup row."""
    cid = item.get("id") or item.get("checkinId")
    ts = _ts(item.get("createdAt"))
    if not cid or ts is None:
        return None
    offset = item.get("timeZoneOffset")
    offset = int(offset) if isinstance(offset, (int, float)) or str(offset or "").lstrip("-").isdigit() else 0
    # the time on the clock where the check-in happened, not where the backup runs
    local = datetime.fromtimestamp(ts, timezone(timedelta(minutes=offset)))
    venue = item.get("venue") or {}
    loc = venue.get("location") or {}
    cats = venue.get("categories") or []
    cat = next((c for c in cats if c.get("primary")), cats[0] if cats else {})
    icon = cat.get("icon") or {}
    photos = (item.get("photos") or {}).get("items") or []
    urls = [f"{p['prefix']}original{p['suffix']}" for p in photos if p.get("prefix") and p.get("suffix")]
    friends = [n for n in (_name(u) for u in (item.get("with") or [])) if n]
    lat = loc.get("lat", item.get("lat"))
    lng = loc.get("lng", item.get("lng"))
    return {
        "checkin_id": str(cid),
        "created_at": local.isoformat(),
        "checkin_date": local.date().isoformat(),
        "venue_id": venue.get("id"),
        "venue_name": venue.get("name"),
        "category": cat.get("name"),
        "category_id": cat.get("id"),
        "neighborhood": loc.get("neighborhood"),
        "city": loc.get("city"),
        "state": loc.get("state"),
        "country": loc.get("country"),
        "cc": loc.get("cc"),
        "address": loc.get("address"),
        "cross_street": loc.get("crossStreet"),
        "postal_code": loc.get("postalCode"),
        "lat": round(float(lat), 6) if lat not in (None, "") else None,
        "lng": round(float(lng), 6) if lng not in (None, "") else None,
        "shout": item.get("shout"),
        "with_friends": "; ".join(friends) or None,
        "photo_count": len(urls) or None,
        "photo_urls": " ".join(urls) or None,
        "category_icon": f"{icon['prefix']}bg_64{icon['suffix']}" if icon.get("prefix") and icon.get("suffix") else None,
        "is_mayor": True if item.get("isMayor") else None,
        "private": True if item.get("private") or item.get("visibility") == "private" else None,
        "type": item.get("type"),
        "checkin_url": item.get("checkinShortUrl") or item.get("canonicalUrl") or f"https://www.swarmapp.com/c/{cid}",
        "created_ts": ts,
    }


def checkin_store() -> Store:
    return Store(CFG.get("output_base", "backups/foursquare_checkins"), "checkin_id", CHECKIN_FIELDS,
                 sort_by="created_ts", drop=DROP_FIELDS)


def place_store() -> Store:
    return Store("backups/foursquare_places", "venue_id", PLACE_FIELDS, sort_by="last_checkin")


def tip_store() -> Store:
    return Store("backups/foursquare_tips", "tip_id", TIP_FIELDS, sort_by="created_ts", drop=DROP_FIELDS)


def normalise_tip(item: dict) -> dict | None:
    """One tip you wrote -> one flat row. Other people's likes and replies are left out."""
    tid, ts = item.get("id"), _ts(item.get("createdAt"))
    if not tid or ts is None or not (item.get("text") or "").strip():
        return None
    when = datetime.fromtimestamp(ts, timezone.utc)
    venue = item.get("venue") or {}
    loc = venue.get("location") or {}
    cats = venue.get("categories") or []
    cat = next((c for c in cats if c.get("primary")), cats[0] if cats else {})
    photo = item.get("photo") or {}
    return {
        "tip_id": str(tid),
        "created_at": when.isoformat(),
        "tip_date": when.date().isoformat(),
        "text": item["text"].strip(),
        "venue_id": venue.get("id"),
        "venue_name": venue.get("name"),
        "category": cat.get("name"),
        "city": loc.get("city"),
        "state": loc.get("state"),
        "country": loc.get("country"),
        "tip_url": item.get("canonicalUrl"),
        "photo_url": (f"{photo['prefix']}original{photo['suffix']}" if photo.get("prefix") and photo.get("suffix")
                      else item.get("photourl")),
        "created_ts": ts,
    }


def fetch_tips(delay: float = 0.5) -> int | None:
    """Back up every tip you wrote. There are few, so the whole list is read each run.

    Returns how many are stored, or None if Foursquare wouldn't return them (the
    check-in backup is not affected).
    """
    store = tip_store()
    offset = 0
    try:
        while True:
            res = api_get("users/self/tips", {"limit": 200, "offset": offset, "sort": "recent"}).get("tips") or {}
            items = res.get("items") or []
            if not items:
                break
            for it in items:
                rec = normalise_tip(it)
                if rec:
                    store.upsert(rec, save_every=0)
            offset += len(items)
            if res.get("count") and offset >= res["count"]:
                break
            time.sleep(delay)
    except ApiError as e:
        log(f"Foursquare tips: not backed up this run ({e})")
        return None
    store.save()
    log(f"Foursquare tips: {store.added} new, {len(store)} total")
    return len(store)


def fetch_checkins(store: Store, full: bool, delay: float = 0.5) -> dict:
    """Walk the history newest first. Returns {'api_count': n} for the status file."""
    offset, api_count = 0, None
    while True:
        res = (api_get("users/self/checkins", {"limit": PAGE, "offset": offset}).get("checkins") or {})
        items = res.get("items") or []
        api_count = res.get("count", api_count)
        if not items:
            break
        new = 0
        for it in items:
            rec = normalise(it)
            if rec:
                new += store.upsert(rec, save_every=250)
        offset += len(items)
        log(f"Foursquare: read {offset}{f' of {api_count}' if api_count else ''} check-ins")
        if not full and not new:
            break  # a whole page already backed up: everything older is too
        if api_count and offset >= api_count:
            break
        time.sleep(delay)
    return {"api_count": api_count}


def rebuild_places(checkins: Store, places: Store | None = None) -> Store:
    places = places or place_store()
    kept = {k: {f: v.get(f) for f in ("venue_photo_backup",)} for k, v in places.records.items()}
    places.records = {}
    for r in sorted(checkins.records.values(), key=lambda r: r.get("created_ts") or 0):
        vid = r.get("venue_id")
        if not vid:
            continue
        p = places.records.setdefault(str(vid), {"venue_id": vid, "checkin_count": 0, "first_checkin": r.get("checkin_date"),
                                                 **(kept.get(str(vid)) or {})})
        p["checkin_count"] += 1
        p["last_checkin"] = r.get("checkin_date")
        for f in ("venue_name", "category", "neighborhood", "city", "state", "country", "cc", "lat", "lng",
                  "category_icon", "category_icon_backup"):
            if r.get(f) not in (None, ""):
                p[f] = r[f]  # the most recent check-in's details win
    places.save()
    return places


def longest_streak(days: set[str]) -> int:
    best = run = 0
    prev = None
    for d in sorted(days):
        cur = date.fromisoformat(d)
        run = run + 1 if prev and (cur - prev).days == 1 else 1
        best, prev = max(best, run), cur
    return best


def derived_stats(checkins: Store) -> dict:
    rows = list(checkins.records.values())
    years = Counter((r.get("checkin_date") or "")[:4] for r in rows if r.get("checkin_date"))
    stats = {
        "places": len({r["venue_id"] for r in rows if r.get("venue_id")}),
        "countries": len({r["country"] for r in rows if r.get("country")}),
        "cities": len({(r.get("city"), r.get("state"), r.get("cc")) for r in rows if r.get("city")}),
        "categories": len({r["category"] for r in rows if r.get("category")}),
        "photos": sum(int(r.get("photo_count") or 0) for r in rows),
        "longest_streak_days": longest_streak({r["checkin_date"] for r in rows if r.get("checkin_date")}),
        "first_checkin": min((r["checkin_date"] for r in rows if r.get("checkin_date")), default=None),
        "busiest_year": years.most_common(1)[0][0] if years else None,
    }
    return {k: {"value": v, "source": "derived"} for k, v in stats.items() if v not in (None, 0)}


def _count(v):
    """A counter from the shapes the API uses: 12, {'count': 12} or {'items': [...]}."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return int(v)
    if isinstance(v, dict):
        for k in ("count", "balance", "total"):
            if isinstance(v.get(k), (int, float)):
                return int(v[k])
        if isinstance(v.get("items"), list):
            return len(v["items"])
    return None


def fetch_profile(checkins: Store) -> dict:
    """Name, join date and counters. Counters the API no longer returns are left out."""
    path = DATA_ROOT / "backups" / "foursquare_profile.json"
    old = json.loads(path.read_text()) if path.exists() else {}
    user = api_get("users/self").get("user") or {}
    stats: dict = {}
    for key, field in (("checkins", "checkins"), ("mayorships", "mayorships"), ("tips", "tips"), ("friends", "friends"),
                       ("lists", "lists"), ("coins", "coins"), ("stickers", "stickers"), ("photos_on_foursquare", "photos")):
        n = _count(user.get(field))
        if n is not None:
            stats[key] = {"value": n, "source": "api"}
    # counters that used to have their own endpoint; each is optional and may be gone
    for key, ep, field in (("mayorships", "users/self/mayorships", "mayorships"), ("stickers", "users/self/stickers", "stickers"),
                           ("coins", "users/self/coins", "coins")):
        if key in stats:
            continue
        try:
            n = _count(api_get(ep, tries=1).get(field))
        except ApiError:
            n = None  # endpoint gone or closed to this token; the counter is simply left out
        if n is not None:
            stats[key] = {"value": n, "source": "api"}
    stats.update(derived_stats(checkins))
    photo = user.get("photo") or {}
    profile = {
        "name": _name(user) or old.get("name"),
        "home_city": user.get("homeCity") or old.get("home_city"),
        "joined": (datetime.fromtimestamp(_ts(user["createdAt"]), timezone.utc).date().isoformat()
                   if _ts(user.get("createdAt")) else old.get("joined")),
        "profile_url": (CFG.get("profile_url") if "YOUR_" not in (CFG.get("profile_url") or "") else None) or user.get("canonicalUrl"),
        "avatar_url": f"{photo['prefix']}300x300{photo['suffix']}" if photo.get("prefix") and photo.get("suffix") else old.get("avatar_url"),
        "avatar_backup": old.get("avatar_backup"),
        "stats": stats,
        "updated_at": now_iso(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(profile, indent=2, ensure_ascii=False))
    return profile


def _export_sources() -> list:
    """Foursquare's own data export, dropped (unzipped or as a .zip) into backups/ or imports/."""
    out = []
    for parent in (DATA_ROOT / "backups", DATA_ROOT / "imports"):
        for name in ("foursquare_export", "foursquare_export.zip"):
            if (parent / name).exists():
                out.append(parent / name)
    return out


def _export_items(src) -> list[dict]:
    """Every check-in object found in the export's checkins*.json files."""
    import zipfile

    docs = []
    if src.is_dir():
        for f in sorted(src.rglob("*.json")):
            if f.name.lower().startswith("checkins"):
                docs.append((f.name, f.read_text(encoding="utf-8")))
    else:
        with zipfile.ZipFile(src) as z:
            for n in sorted(z.namelist()):
                if n.lower().endswith(".json") and n.rsplit("/", 1)[-1].lower().startswith("checkins"):
                    docs.append((n, z.read(n).decode("utf-8")))
    items = []
    for name, text in docs:
        try:
            data = json.loads(text)
        except ValueError:
            log(f"Foursquare export: {name} isn't valid JSON; skipped")
            continue
        if isinstance(data, dict):
            data = data.get("items") or (data.get("checkins") or {}).get("items") or (data.get("response", {}).get("checkins") or {}).get("items") or []
        items += [d for d in data if isinstance(d, dict)]
    return items


def import_export() -> int:
    """Merge the data export into the check-in backup without overriding API values.

    New check-ins are added; for ones already backed up only empty fields are
    filled in. The export itself stays where it was put, as the raw copy.
    Returns how many check-ins were added.
    """
    sources = _export_sources()
    if not sources:
        return 0
    store = checkin_store()
    filled = skipped = 0
    for src in sources:
        items = _export_items(src)
        for it in items:
            rec = normalise(it)
            if not rec:
                skipped += 1
                continue
            old = store.records.get(rec["checkin_id"])
            if old is None:
                store.upsert({**rec, "source": "export"}, save_every=0)
                continue
            for k, v in rec.items():
                if v not in (None, "") and old.get(k) in (None, ""):
                    old[k] = v
                    filled += 1
        log(f"Foursquare export: {len(items)} check-ins read from {src.name}")
    store.save()
    rebuild_places(store)
    log(f"Foursquare export: {store.added} check-ins added, {filled} empty fields filled"
        + (f", {skipped} entries without an id or time skipped" if skipped else ""))
    return store.added


# filled by run() and read by run_all.py for status.json
LAST_RUN: dict = {}


def run(full: bool = False, headful: bool = False, delay: float = 0.5) -> int:
    if not token():
        log("Foursquare: no FOURSQUARE_TOKEN set; skipping (see scripts/foursquare_token.py)")
        LAST_RUN.clear()
        LAST_RUN["skipped"] = True
        return 0
    store = checkin_store()
    info = fetch_checkins(store, full=full or not len(store), delay=delay)
    store.save()
    rebuild_places(store)
    fetch_profile(store)
    fetch_tips(delay=delay)
    LAST_RUN.clear()
    LAST_RUN.update(api_count=info.get("api_count"), stored_count=len(store))
    if info.get("api_count") and len(store) < info["api_count"]:
        log(f"Foursquare: WARNING the API reports {info['api_count']} check-ins but {len(store)} are stored; "
            "run once with --full, or add the Foursquare data export to fill the gap")
    log(f"Foursquare: {store.added} new check-ins, {len(store)} total")
    return store.added


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true")
    a = ap.parse_args()
    run(full=a.full)
