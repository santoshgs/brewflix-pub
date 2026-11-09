"""Offline end-to-end tests: real Playwright + real pagination against fixture pages.

Run:  python -m pytest -q tests/     (or: python tests/test_pipeline.py)
"""
import csv
import functools
import http.server
import json
import os
import sys
import tempfile
import threading
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="backup-test-"))
os.environ["BACKUP_ROOT"] = str(TMP)
os.environ.setdefault("BACKUP_TZ", "America/Toronto")  # example zone the date tests are written for
os.environ["UNTAPPD_STATE_FILE"] = str(TMP / "none.json")
os.environ["IMDB_DETAILS_PER_RUN"] = "0"  # don't reach the live site from tests
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scraper"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fixtures as fx  # noqa: E402
import imdb  # noqa: E402
import normalize_exports  # noqa: E402
import untappd  # noqa: E402

SITE = TMP / "www"


def serve(pages: dict[str, str]) -> str:
    SITE.mkdir(exist_ok=True)
    for name, html in pages.items():
        (SITE / name).write_text(html)
    h = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(SITE))
    h.log_message = lambda *a: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), h)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}"


def load(name):
    return json.loads((TMP / "backups" / f"{name}.json").read_text())


def csv_rows(name):
    with open(TMP / "backups" / f"{name}.csv", newline="") as f:
        return list(csv.DictReader(f))


def test_imdb_full_then_incremental():
    base = serve({"imdb.html": fx.imdb_page()})
    added = imdb.run(full=True, url=f"{base}/imdb.html")
    data = load("imdb_ratings")
    assert added == 120 and data["count"] == 120, data["count"]
    by = {r["const_id"]: r for r in data["items"]}
    exp = {c: (t, y, ty, ir, m, d) for c, t, y, ty, ir, m, d in fx.IMDB_TITLES}
    for c, (t, y, ty, ir, m, d) in exp.items():
        r = by[c]
        assert r["title"] == t and r["year"] == y and r["user_rating"] == m, r
        assert r["imdb_rating"] == ir and r["date_rated"] == d, r
    assert by["tt1000004"]["title_type"] == "TV Series"
    assert by["tt1000001"]["genres"] == "Drama, Comedy"  # from __NEXT_DATA__ (first page)
    assert all(r["poster_url"].startswith("https://m.media-amazon.com/") for r in data["items"])
    assert len(csv_rows("imdb_ratings")) == 120

    # a new rating appears at the top: incremental run picks it up and stops early
    newer = [("tt9999999", "Brand New Film", 2026, "Movie", 7.7, 9, "2026-11-06")] + fx.IMDB_TITLES
    (SITE / "imdb.html").write_text(fx.imdb_page(newer))
    added = imdb.run(full=False, url=f"{base}/imdb.html")
    assert added == 1 and load("imdb_ratings")["count"] == 121
    assert load("imdb_ratings")["items"][0]["const_id"] == "tt9999999"  # sorted newest-rated first


def test_imdb_ratings_that_load_late():
    """The live page fills in your rating and 'Rated on' date after first paint."""
    titles = [("tt7000001", "Late Film", 2026, "Movie", 6.4, 6, "2026-11-08"),
              ("tt7000002", "Late Show", 2025, "TV Series", 8.0, 9, "2026-11-08")]
    base = serve({"late.html": fx.imdb_page(titles, late_ratings=True)})
    imdb.run(full=True, url=f"{base}/late.html")
    by = {r["const_id"]: r for r in load("imdb_ratings")["items"]}
    assert by["tt7000001"]["user_rating"] == 6 and by["tt7000001"]["date_rated"] == "2026-11-08"
    assert by["tt7000002"]["user_rating"] == 9 and by["tt7000002"]["date_rated"] == "2026-11-08"


def test_untappd_full_incremental_and_cap():
    base = serve({
        "profile.html": fx.untappd_profile(),
        "beers.html": fx.untappd_beers_page(),
    })
    added = untappd.run(full=True, profile_url=f"{base}/profile.html", beers_url=f"{base}/beers.html", details_limit=0)
    data = load("untappd_checkins")
    assert added == 130 and data["count"] == 130
    beers = load("untappd_beers")
    assert beers["count"] == 70
    by = {r["checkin_id"]: r for r in data["items"]}
    for c in fx.UNTAPPD_CHECKINS:
        r = by[c["id"]]
        assert r["beer_name"] == c["beer"] and r["brewery_name"] == c["brewery"], r
        assert r["user_rating"] == c["rating"] and "venue" not in r, r
        assert r["photo_url"] == c["photo"] and "comment" not in r, r
        assert (r.get("serving_type") or "") == c["serving"], r
        assert r["beer_id"] == c["bid"] and len(r["checkin_date"]) == 10, r  # day only, no clock time
        assert r["style"] and r["abv"] and r["global_rating"] == 3.81, r  # enriched from beers list
        assert r["label_url"].endswith(f"beer-{c['bid']}.jpeg"), r
    assert len(csv_rows("untappd_checkins")) == 130

    # logged-out cap: list stops growing; scraper keeps what it got and exits cleanly
    newer = [dict(fx.UNTAPPD_CHECKINS[0], id="999999", when="Sat, 07 Nov 2026 01:00:00 +0000")] + fx.UNTAPPD_CHECKINS
    (SITE / "profile.html").write_text(fx.untappd_profile(newer, cap=2))
    added = untappd.run(full=False, profile_url=f"{base}/profile.html", beers_url=f"{base}/beers.html", details_limit=0)
    assert added == 1 and load("untappd_checkins")["items"][0]["checkin_id"] == "999999"

    # full walk into the cap: stops cleanly (headless/CI can't pause for login) and keeps data
    untappd.run(full=True, profile_url=f"{base}/profile.html", beers_url=f"{base}/beers.html", details_limit=0)
    assert load("untappd_checkins")["count"] == 131


def test_flavours_from_checkin_pages():
    from playwright.sync_api import sync_playwright
    from common import Store

    page_html = """<html><body><div class="checkin-info">
      <p class="label">Flavor Profiles</p>
      <div class="flavor"><ul><li><span>Juicy</span></li><li><span>Hazy</span></li><li><span>Juicy</span></li></ul></div>
    </div></body></html>"""
    plain = "<html><body><p>No tags here</p></body></html>"
    assert untappd.parse_flavors(page_html) == ["Juicy", "Hazy"]
    assert untappd.parse_flavors(plain) == []
    # text-label fallback when the class name is something else
    alt = "<div><h4>Flavor Profiles</h4><ul><li>Citrus</li><li>Pine</li></ul></div>"
    assert untappd.parse_flavors(alt) == ["Citrus", "Pine"]

    base = serve({"c1.html": page_html, "c2.html": plain})
    store = Store("backups/flavour_test", "checkin_id", ["checkin_id"])
    store.records = {
        "1": {"checkin_id": "1", "checkin_url": f"{base}/c1.html", "checkin_date": "2026-01-02"},
        "2": {"checkin_id": "2", "checkin_url": f"{base}/c2.html", "checkin_date": "2026-01-01"},
    }
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        assert untappd.enrich_details(pg, store, limit=10) == 1
        assert untappd.enrich_details(pg, store, limit=10) == 0  # nothing left to read
        b.close()
    assert store.records["1"]["flavors"] == "Juicy, Hazy"
    assert store.records["2"]["flavors"] is None and store.records["2"]["details_v"] == untappd.DETAILS_VERSION


def test_global_rating_from_checkin_page():
    from playwright.sync_api import sync_playwright
    from common import Store

    page_html = """<html><body><div class="ratings">
      <div class="you"><p>Your Rating</p><div class="caps" data-rating="3"></div></div>
      <div class="you"><p>Global Rating</p><div class="caps" data-rating="3.64643"></div></div>
    </div></body></html>"""
    assert untappd.parse_global(page_html) == 3.64643
    assert untappd.parse_global("<html><body><p>nothing</p></body></html>") is None

    base = serve({"c1.html": page_html, "c2.html": "<html><body><p>no rating yet</p></body></html>",
                  "c4.html": "<html><body><p>no rating here either</p></body></html>",
                  "beer9.html": page_html})
    untappd.beer_url = lambda bid: f"{base}/beer{bid}.html"  # the beer page is the fallback source
    store = Store("backups/global_test", "checkin_id", ["checkin_id"])
    store.records = {
        "1": {"checkin_id": "1", "checkin_url": f"{base}/c1.html", "checkin_date": "2026-01-02", "global_rating": None},
        "2": {"checkin_id": "2", "checkin_url": f"{base}/c2.html", "checkin_date": "2026-01-01", "global_rating": None},
        "3": {"checkin_id": "3", "checkin_url": f"{base}/c1.html", "checkin_date": "2025-12-01", "global_rating": 3.8},
        "4": {"checkin_id": "4", "checkin_url": f"{base}/c4.html", "checkin_date": "2025-11-01", "beer_id": "9", "global_rating": None},
    }
    with sync_playwright() as p:
        b = p.chromium.launch()
        untappd.enrich_details(b.new_page(), store, limit=10)
        b.close()
    assert store.records["1"]["global_rating"] == 3.64643
    assert store.records["2"]["global_rating"] is None  # no rating on either page; left empty
    assert store.records["3"]["global_rating"] == 3.8  # already known; not overwritten
    assert store.records["4"]["global_rating"] == 3.64643  # from the beer page

def test_local_date_keeps_only_the_local_day():
    from common import local_date

    assert local_date("Sat, 07 Nov 2026 03:00:00 +0000") == "2026-11-06"  # 10pm that evening in Toronto
    assert local_date("2026-11-07T04:02:28+00:00") == "2026-11-06"
    assert local_date("2019-05-04 18:22:01") == "2019-05-04"              # naive = UTC
    assert local_date("2026-11-07") == "2026-11-07" and local_date(None) is None


def test_photo_backup_strips_metadata_and_runs_once():
    import io
    from PIL import Image

    import photos
    from common import Store

    # a JPEG carrying EXIF (including a GPS block) like a phone photo would
    img = Image.new("RGB", (1200, 900), (180, 120, 40))
    exif = Image.Exif()
    exif[0x010F] = "PhoneMaker"
    exif[0x8825] = {1: "N", 2: (43.0, 39.0, 0.0)}
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif)
    base = serve({})
    (SITE / "p1.jpg").write_bytes(buf.getvalue())

    st = Store("backups/untappd_checkins", "checkin_id", ["checkin_id"])
    st.records = {
        "11": {"checkin_id": "11", "checkin_date": "2026-01-02", "photo_url": f"{base}/p1.jpg"},
        "12": {"checkin_id": "12", "checkin_date": "2026-01-01", "photo_url": f"{base}/missing.jpg"},
        "13": {"checkin_id": "13", "checkin_date": "2026-01-01"},
    }
    st.save()
    uploaded = {}
    assert photos.backup(put=lambda k, d: uploaded.__setitem__(k, d), delay=0) == 1
    assert set(uploaded) == {"photos/11.webp", "thumbs/11.webp"}
    full = Image.open(io.BytesIO(uploaded["photos/11.webp"]))
    thumb = Image.open(io.BytesIO(uploaded["thumbs/11.webp"]))
    assert full.format == "WEBP" and max(full.size) == 640 and thumb.size == (160, 160)
    assert not full.getexif() and "exif" not in full.info and "exif" not in thumb.info
    recs = {r["checkin_id"]: r for r in load("untappd_checkins")["items"]}
    assert recs["11"]["photo_backup"] == "photos/11.webp" and not recs["12"].get("photo_backup")
    uploaded.clear()
    assert photos.backup(put=lambda k, d: uploaded.__setitem__(k, d), delay=0) == 0  # 12 retried, still missing
    assert uploaded == {}
    assert photos.backup() == 0  # no R2 secrets here: skipped quietly


def test_labels_backed_up_once_per_image():
    import io
    from PIL import Image

    import photos
    from common import Store

    base = serve({})
    logo = io.BytesIO(); Image.new("RGBA", (400, 400), (10, 20, 30, 0)).save(logo, "PNG")
    (SITE / "beer-1.png").write_bytes(logo.getvalue())
    url = f"{base}/beer-1.png"
    ck = Store("backups/untappd_checkins", "checkin_id", ["checkin_id"])
    ck.records = {str(i): {"checkin_id": str(i), "checkin_date": "2026-01-01", "label_url": url} for i in (21, 22)}
    ck.save()
    bs = Store("backups/untappd_beers", "beer_id", ["beer_id"])
    bs.records = {"1": {"beer_id": "1", "label_url": url}, "2": {"beer_id": "2", "label_url": f"{base}/gone.png"}}
    bs.save()
    up = {}
    assert photos.backup_labels(put=lambda k, d: up.__setitem__(k, d), delay=0) == 1
    key = photos.label_key(url)
    assert list(up) == [key] and key.startswith("labels/") and len(key) == len("labels/") + 16 + 5
    im = Image.open(io.BytesIO(up[key]))
    assert im.format == "WEBP" and max(im.size) == 200 and im.mode == "RGBA"  # transparency kept
    assert all(r["label_backup"] == key for r in load("untappd_checkins")["items"])
    beers = {r["beer_id"]: r for r in load("untappd_beers")["items"]}
    assert beers["1"]["label_backup"] == key and not beers["2"].get("label_backup")
    up.clear()
    assert photos.backup_labels(put=lambda k, d: up.__setitem__(k, d), delay=0) == 0 and up == {}


def test_imdb_director_and_runtime():
    from common import Store

    # JSON page data, both shapes IMDb uses
    blob = {"t": {"id": "tt0000001", "titleText": {"text": "A"}, "runtime": {"seconds": 5880},
                  "directors": [{"credits": [{"name": {"nameText": {"text": "Jane Doe"}}}]}]},
            "u": {"id": "tt0000002", "titleText": {"text": "B"},
                  "principalCredits": [{"category": {"id": "director"}, "credits": [
                      {"name": {"nameText": {"text": "Ann Lee"}}}, {"name": {"nameText": {"text": "Bo Kim"}}}]},
                      {"category": {"id": "cast"}, "credits": [{"name": {"nameText": {"text": "Actor"}}}]}]}}
    got = imdb.parse_json_titles(blob)
    assert got["tt0000001"]["runtime_mins"] == 98 and got["tt0000001"]["directors"] == "Jane Doe"
    assert got["tt0000002"]["directors"] == "Ann Lee, Bo Kim"

    page = '<script type="application/ld+json">{"director":[{"@type":"Person","name":"Mark Vanselow"}],"duration":"PT1H38M"}</script>'
    assert imdb.parse_ld_json(page) == {"directors": "Mark Vanselow", "runtime_mins": 98}
    assert imdb.parse_ld_json('<script type="application/ld+json">{"duration":"PT45M"}</script>') == {"runtime_mins": 45}

    st = Store("backups/details_test", "const_id", ["const_id"])
    st.records = {"tt1": {"const_id": "tt1", "date_rated": "2026-01-02"},
                  "tt2": {"const_id": "tt2", "date_rated": "2026-01-01", "directors": "X", "runtime_mins": 90}}
    calls, real = [], imdb.fetch_title_details
    imdb.fetch_title_details = lambda t: calls.append(t) or {"directors": "Jane Doe", "runtime_mins": 101}
    try:
        assert imdb.backfill_details(st, limit=10, delay=0) == 1 and calls == ["tt1"]
        assert imdb.backfill_details(st, limit=10, delay=0) == 0 and calls == ["tt1"]  # not retried
    finally:
        imdb.fetch_title_details = real
    assert st.records["tt1"]["directors"] == "Jane Doe" and st.records["tt1"]["runtime_mins"] == 101


def test_native_exports_are_normalized():
    imports = TMP / "imports"
    imports.mkdir(exist_ok=True)
    (imports / "imdb_ratings.csv").write_text(
        "Const,Your Rating,Date Rated,Title,Original Title,URL,Title Type,IMDb Rating,Runtime (mins),Year,Genres,Num Votes,Release Date,Directors\n"
        "tt0133093,10,2024-02-01,The Matrix,The Matrix,https://www.imdb.com/title/tt0133093/,Movie,8.7,136,1999,\"Action, Sci-Fi\",2100000,1999-03-31,Lana Wachowski\n"
    )
    (imports / "untappd_export.json").write_text(json.dumps([{
        "beer_name": "Export Ale", "brewery_name": "Export Co", "beer_type": "Pale Ale - American",
        "beer_abv": "5.2", "beer_ibu": "40", "comment": "from export", "venue_name": "Home",
        "checkin_url": "https://untappd.com/c/1", "created_at": "2019-05-04 18:22:01",
        "rating_score": "3.75", "checkin_id": "1", "bid": "42", "photo_url": "",
        "global_weighted_rating_score": "3.66", "serving_type": "Can"}]))
    covered = normalize_exports.main()
    assert covered == ["imdb", "untappd"]
    m = {r["const_id"]: r for r in load("imdb_ratings")["items"]}["tt0133093"]
    assert m["user_rating"] == 10 and m["genres"] == "Action, Sci-Fi" and m["year"] == 1999
    e = {r["checkin_id"]: r for r in load("untappd_checkins")["items"]}["1"]
    assert e["abv"] == 5.2 and e["user_rating"] == 3.75 and e["checkin_date"] == "2019-05-04" and "comment" not in e


def test_poster_backfill_marks_misses_and_skips_done(monkeypatch=None):
    from common import Store

    store = Store("backups/poster_test", "const_id", ["const_id", "poster_url"])
    store.upsert({"const_id": "tt1", "poster_url": "https://m.media-amazon.com/images/M/x._V1_.jpg"}, save_every=0)
    store.records["tt2"] = {"const_id": "tt2", "poster_url": None}
    store.records["tt3"] = {"const_id": "tt3", "poster_url": None}
    calls = []
    real = imdb.fetch_poster
    imdb.fetch_poster = lambda tid: calls.append(tid) or ("https://img/" + tid if tid == "tt2" else None)
    try:
        imdb.backfill_posters(store, delay=0)
    finally:
        imdb.fetch_poster = real
    assert calls == ["tt2", "tt3"]
    assert store.records["tt2"]["poster_url"] == "https://img/tt2"
    assert store.records["tt3"]["poster_url"] == ""  # no poster on IMDb: not retried
    calls.clear()
    imdb.fetch_poster = lambda tid: calls.append(tid)
    try:
        imdb.backfill_posters(store, delay=0)
    finally:
        imdb.fetch_poster = real
    assert calls == []


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
    print("outputs in", TMP)
