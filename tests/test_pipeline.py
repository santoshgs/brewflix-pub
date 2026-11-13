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
        assert r.get("venue_name") == c["venue"] and "purchase_venue" not in r, r  # where the beer was had is kept
        assert not c["venue"] or (r["venue_id"] == "77" + c["id"][-2:] and r["venue_url"].startswith("https://untappd.com/v/")), r
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


CHECKIN_PAGE = """<html><body><div class="checkin-top">
  <a href="/user/sample"><img alt="Sample P"></a><p><a href="/user/sample">Sample P</a></p>
  {venue}
  <a href="/b/downtown-brown/166684"><img></a><p><a href="/b/downtown-brown/166684">Downtown Brown</a></p>
  <p class="label">Flavor Profiles</p><div class="flavor"><ul><li><span>Malty</span></li></ul></div>
  {bought}
 </div><footer><a href="/v/somewhere-else/55">A venue in the footer</a></footer></body></html>"""
BOUGHT = """<div><p><strong>Purchased From</strong></p><a href="https://untappd.com/v/corner-shop/733312"><img alt="logo"></a>
  <a href="https://untappd.com/v/corner-shop/733312">Corner Shop</a><p>1 Example Ave Sampletown, ON</p><p>Category: Liquor Store</p></div>"""


def test_venue_from_checkin_page_but_never_where_it_was_bought():
    from playwright.sync_api import sync_playwright
    from common import Store

    home = '<p><a href="https://untappd.com/v/at-home/9917985">Untappd at Home</a></p>'
    assert untappd.parse_venue(CHECKIN_PAGE.format(venue=home, bought=BOUGHT)) == {
        "venue_name": "Untappd at Home", "venue_id": "9917985", "venue_url": "https://untappd.com/v/at-home/9917985"}
    pub_ = '<p><a href="/v/the-sample-arms/1234"><img></a> <a href="/v/the-sample-arms/1234">The Sample Arms</a></p>'
    assert untappd.parse_venue(CHECKIN_PAGE.format(venue=pub_, bought=""))["venue_name"] == "The Sample Arms"
    # bought somewhere, drunk nowhere in particular: the shop must not be mistaken for the location
    assert untappd.parse_venue(CHECKIN_PAGE.format(venue="", bought=BOUGHT)) == {}
    assert untappd.parse_venue("<html><body><p>nothing</p></body></html>") == {}

    base = serve({"v1.html": CHECKIN_PAGE.format(venue=home, bought=BOUGHT), "v2.html": CHECKIN_PAGE.format(venue="", bought=BOUGHT),
                  "v3.html": "<html><body><p>tags missing today</p></body></html>"})
    store = Store("backups/venue_test", "checkin_id", ["checkin_id"], drop=untappd.PRIVATE_FIELDS)
    store.records = {
        "1": {"checkin_id": "1", "checkin_url": f"{base}/v1.html", "checkin_date": "2026-01-03", "global_rating": 3.5, "details_v": 3, "flavors": "Old"},
        "2": {"checkin_id": "2", "checkin_url": f"{base}/v2.html", "checkin_date": "2026-01-02", "global_rating": 3.5, "details_v": 3},
        "3": {"checkin_id": "3", "checkin_url": f"{base}/v3.html", "checkin_date": "2026-01-01", "global_rating": 3.5, "details_v": 3,
              "flavors": "Juicy", "venue_name": "Known from the feed", "venue_id": "9"},
    }
    with sync_playwright() as p:
        b = p.chromium.launch()
        untappd.enrich_details(b.new_page(), store, limit=10)  # older parser version: all three are re-read
        b.close()
    r = store.records
    assert r["1"]["venue_name"] == "Untappd at Home" and r["1"]["venue_id"] == "9917985" and r["1"]["flavors"] == "Malty"
    assert r["2"]["venue_name"] is None and "Corner Shop" not in json.dumps(r)
    assert r["3"]["venue_name"] == "Known from the feed" and r["3"]["flavors"] == "Juicy"  # a thin page wipes nothing
    assert all(x["details_v"] == untappd.DETAILS_VERSION for x in r.values())


def test_unique_beer_added_from_checkins_when_list_lacks_it():
    target = fx.UNTAPPD_CHECKINS[0]["bid"]
    listed = [c for c in fx.UNTAPPD_CHECKINS if c["bid"] != target]  # the list hasn't caught up with this beer
    base = serve({
        "profile.html": fx.untappd_profile(),
        "beers.html": fx.untappd_beers_page(checkins=listed),
    })
    untappd.run(full=True, profile_url=f"{base}/profile.html", beers_url=f"{base}/beers.html", details_limit=0)
    rows = {r["beer_id"]: r for r in load("untappd_beers")["items"]}
    n = sum(1 for c in fx.UNTAPPD_CHECKINS if c["bid"] == target)
    row = rows[str(target)]
    assert row["checkin_count"] == n and row["beer_name"] and row["beer_url"].endswith(f"/{target}")
    assert row["first_checkin"] and row["last_checkin"]


def test_global_rating_from_checkin_page():
    from playwright.sync_api import sync_playwright
    from common import Store

    page_html = """<html><body><div class="ratings">
      <div class="you"><p>Your Rating</p><div class="caps" data-rating="3"></div></div>
      <div class="you"><p>Global Rating</p><div class="caps" data-rating="3.64643"></div></div>
    </div></body></html>"""
    assert untappd.parse_global(page_html) == 3.64643
    assert untappd.parse_global("<html><body><p>nothing</p></body></html>") is None
    assert untappd.parse_beer_rating("<p>3.4 Ratings</p>") is None

    base = serve({"c1.html": page_html, "c2.html": "<html><body><p>no rating yet</p></body></html>",
                  "c4.html": "<html><body><p>no rating here either</p></body></html>",
                  "beer9.html": """<html><body><div class="beer-stats"><span>5% ABV</span>
      <div class="ratings-row"><span class="caps" data-rating="3.65"></span><span>(3.65)</span>
      <div><b>691</b> Ratings</div></div></div></body></html>"""})
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
    assert store.records["4"]["global_rating"] == 3.65  # from the beer page

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
        "beer_abv": "5.2", "beer_ibu": "40", "comment": "from export", "venue_name": "Home", "venue_city": "Toronto", "venue_lat": "43.65", "venue_lng": "-79.38",
        "purchase_venue": "Corner Shop",
        "checkin_url": "https://untappd.com/c/1", "created_at": "2019-05-04 18:22:01",
        "rating_score": "3.75", "checkin_id": "1", "bid": "42", "photo_url": "",
        "global_weighted_rating_score": "3.66", "serving_type": "Can"}]))
    covered = normalize_exports.main()
    assert covered == ["imdb", "untappd"]
    m = {r["const_id"]: r for r in load("imdb_ratings")["items"]}["tt0133093"]
    assert m["user_rating"] == 10 and m["genres"] == "Action, Sci-Fi" and m["year"] == 1999
    e = {r["checkin_id"]: r for r in load("untappd_checkins")["items"]}["1"]
    assert e["abv"] == 5.2 and e["user_rating"] == 3.75 and e["checkin_date"] == "2019-05-04" and "comment" not in e
    assert e["venue_name"] == "Home" and e["venue_city"] == "Toronto" and e["venue_lat"] == 43.65 and "purchase_venue" not in e


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

# ---------------------------------------------------------------- Foursquare / Swarm
class FsqApi:
    """A tiny stand-in for api.foursquare.com/v2 (checkins paging, profile, 401 mode)."""

    def __init__(self, total=620, max_page=250):
        self.total, self.max_page, self.reject, self.calls = total, max_page, False, []
        self.tips, self.tips_gone = 9, False
        self.extra = []  # newer check-ins put in front of the generated ones
        api = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                from urllib.parse import parse_qs, urlparse

                u = urlparse(self.path)
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                api.calls.append((u.path, q))
                if api.reject or self.headers.get("Authorization") != "Bearer test-token":
                    return self.send(401, {"meta": {"code": 401, "errorType": "invalid_auth"}})
                if u.path == "/v2/users/self":
                    return self.send(200, {"response": {"user": fx.FSQ_USER}})
                if u.path == "/v2/users/self/checkins":
                    items = api.extra + [fx.fsq_checkin(i) for i in range(api.total)]
                    off, lim = int(q.get("offset", 0)), min(int(q.get("limit", 20)), api.max_page)
                    return self.send(200, {"response": {"checkins": {"count": len(items), "items": items[off:off + lim]}}})
                if u.path == "/v2/users/self/tips" and not api.tips_gone:
                    items = [fx.fsq_tip(i) for i in range(api.tips)]
                    off, lim = int(q.get("offset", 0)), min(int(q.get("limit", 20)), 4)
                    return self.send(200, {"response": {"tips": {"count": len(items), "items": items[off:off + lim]}}})
                self.send(404, {"meta": {"code": 404, "errorType": "endpoint_error"}})

            def send(self, code, body):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{srv.server_address[1]}/v2"


def _fsq_reset():
    for f in (TMP / "backups").glob("foursquare_*"):
        if f.is_file():
            f.unlink()


def test_foursquare_full_incremental_and_privacy():
    import foursquare

    _fsq_reset()
    api = FsqApi(max_page=100)  # a smaller real page size than asked for must still walk everything
    os.environ["FOURSQUARE_API_BASE"], os.environ["FOURSQUARE_TOKEN"] = api.base, "test-token"
    try:
        assert foursquare.run(full=True, delay=0) == 620
        data = load("foursquare_checkins")
        assert data["count"] == 620 and len(csv_rows("foursquare_checkins")) == 620
        items = data["items"]
        assert [r["created_ts"] for r in items] == sorted((r["created_ts"] for r in items), reverse=True)
        by = {r["checkin_id"]: r for r in items}
        first = by[fx.fsq_checkin(0)["id"]]
        assert first["venue_name"] == "Sample Place 0" and first["category"] == "Bakery" and first["neighborhood"] == "Leslieville"
        assert first["city"] == "Toronto" and first["state"] == "ON" and first["country"] == "Canada" and first["cc"] == "CA"
        assert first["shout"] == "Shout number 0 & more" and first["with_friends"] == "Alex Example; Sam"
        assert first["photo_count"] == 2 and len(first["photo_urls"].split()) == 2 and "/original/" in first["photo_urls"]
        assert first["category_icon"].endswith("x_bg_64.png") and first["is_mayor"] is True and first["private"] is True
        assert first["created_at"].endswith("-04:00") and first["checkin_date"] == first["created_at"][:10]
        # other people's likes and comments are never stored, anywhere
        raw = (TMP / "backups" / "foursquare_checkins.json").read_text() + (TMP / "backups" / "foursquare_checkins.csv").read_text()
        assert "Nosy" not in raw and "someone else" not in raw and '"likes"' not in raw and '"comments"' not in raw
        # a shout with no venue is kept, with empty place fields
        lone = by[fx.fsq_checkin(39)["id"]]
        assert not lone.get("venue_id") and lone["checkin_date"]
        # places roll-up
        places = {p["venue_id"]: p for p in load("foursquare_places")["items"]}
        assert len(places) == 45 and sum(p["checkin_count"] for p in places.values()) == 620 - 620 // 40
        p0 = places[fx.fsq_checkin(0)["venue"]["id"]]
        assert p0["first_checkin"] < p0["last_checkin"] and p0["last_checkin"] == first["checkin_date"]
        # profile: API counters and derived ones are told apart; missing ones are simply absent
        prof = json.loads((TMP / "backups" / "foursquare_profile.json").read_text())
        st = prof["stats"]
        assert prof["name"] == "Sample Person" and prof["joined"] == "2010-03-07" and prof["profile_url"].startswith("https://app.foursquare.com/user/")
        assert st["mayorships"] == {"value": 3, "source": "api"} and st["tips"]["value"] == 11 and st["checkins"]["value"] == 620
        assert "coins" not in st and "stickers" not in st
        assert st["places"] == {"value": 45, "source": "derived"} and st["countries"]["value"] == 4
        assert foursquare.LAST_RUN == {"api_count": 620, "stored_count": 620}
        # tips you wrote: all pages read, your words kept, other people's likes not
        tips = load("foursquare_tips")
        assert tips["count"] == 9 and len(csv_rows("foursquare_tips")) == 9
        t0 = tips["items"][0]
        assert t0["text"].startswith("Sample tip 0") and t0["venue_name"] == "Sample Place 0" and t0["city"] == "Toronto"
        assert t0["tip_date"] == "2026-09-21" and t0["tip_url"].endswith("/item/0") and t0["photo_url"].endswith("/original/tip_0.jpg")
        assert "Nosy" not in (TMP / "backups" / "foursquare_tips.json").read_text() and "agreeCount" not in json.dumps(tips)
        # if Foursquare stops returning tips, the saved ones stay and the run still succeeds
        api.tips_gone = True
        assert foursquare.fetch_tips(delay=0) is None and load("foursquare_tips")["count"] == 9
        api.tips_gone = False

        # nothing new: one page is read and the run stops
        api.calls.clear()
        assert foursquare.run(full=False, delay=0) == 0
        assert len([c for c in api.calls if c[0].endswith("/checkins")]) == 1

        # one new check-in on top: exactly one is added, backup markers on old rows survive
        st2 = foursquare.checkin_store()
        st2.records[first["checkin_id"]]["photo_backup"] = 2
        st2.save()
        newer = fx.fsq_checkin(0)
        newer.update(id="6a" + "0" * 22, createdAt=1793600000, shout="Newest one")
        api.extra = [newer]
        assert foursquare.run(full=False, delay=0) == 1
        data = load("foursquare_checkins")
        assert data["count"] == 621 and data["items"][0]["shout"] == "Newest one"
        assert {r["checkin_id"]: r for r in data["items"]}[first["checkin_id"]]["photo_backup"] == 2

        # a rejected token fails loudly and leaves the saved data alone
        api.reject = True
        try:
            foursquare.run(full=False, delay=0)
            raise AssertionError("expected the rejected token to raise")
        except foursquare.ApiError as e:
            assert "FOURSQUARE_TOKEN" in str(e)
        assert load("foursquare_checkins")["count"] == 621

        # no token at all: skipped quietly (clones and the public copy)
        os.environ["FOURSQUARE_TOKEN"] = ""
        assert foursquare.run() == 0 and foursquare.LAST_RUN == {"skipped": True}
    finally:
        os.environ.pop("FOURSQUARE_API_BASE", None)
        os.environ.pop("FOURSQUARE_TOKEN", None)


def test_foursquare_times_are_local_to_the_place():
    import foursquare

    late = foursquare.normalise({"id": "a" * 24, "createdAt": 1793503800, "timeZoneOffset": -240})  # 2026-11-01 03:30 UTC
    assert late["created_at"] == "2026-10-31T23:30:00-04:00" and late["checkin_date"] == "2026-10-31"
    tokyo = foursquare.normalise({"id": "b" * 24, "createdAt": "1522771200", "timeZoneOffset": 540})  # 2018-04-03 16:00 UTC
    assert tokyo["created_at"] == "2018-04-04T01:00:00+09:00" and tokyo["checkin_date"] == "2018-04-04"
    assert foursquare.normalise({"id": "c" * 24}) is None and foursquare.normalise({"createdAt": 1}) is None
    assert foursquare.longest_streak({"2026-01-01", "2026-01-02", "2026-01-03", "2026-01-05"}) == 3


def test_foursquare_export_fills_gaps_without_overriding():
    import zipfile

    import foursquare

    _fsq_reset()
    store = foursquare.checkin_store()
    known = fx.fsq_checkin(1)
    rec = foursquare.normalise(known)
    rec["shout"] = None            # a gap the export can fill
    rec["venue_name"] = "Name from the API"
    store.upsert(rec, save_every=0)
    store.save()
    export_known = dict(known, shout="Only in the export")
    export_known["venue"] = dict(known["venue"], name="Older name in the export")
    older = fx.fsq_checkin(700)    # not in the API backup at all
    exp = TMP / "backups" / "foursquare_export"
    exp.mkdir(parents=True, exist_ok=True)
    (exp / "checkins1.json").write_text(json.dumps({"items": [export_known, {"no": "id"}]}))
    (exp / "tips.json").write_text(json.dumps({"items": [{"id": "x", "createdAt": 1}]}))  # not check-ins: ignored
    with zipfile.ZipFile(TMP / "backups" / "foursquare_export.zip", "w") as z:
        z.writestr("export/checkins2.json", json.dumps([older]))
    assert foursquare.import_export() == 1
    by = {r["checkin_id"]: r for r in load("foursquare_checkins")["items"]}
    assert len(by) == 2
    assert by[known["id"]]["shout"] == "Only in the export" and by[known["id"]]["venue_name"] == "Name from the API"
    assert by[older["id"]]["source"] == "export" and by[older["id"]]["venue_name"]
    assert foursquare.import_export() == 0  # safe to leave the export in place
    assert (exp / "checkins1.json").exists() and len(load("foursquare_places")["items"]) == 2
    import shutil

    shutil.rmtree(exp)
    (TMP / "backups" / "foursquare_export.zip").unlink()


def test_swarm_photos_icons_and_avatar_go_to_private_bucket():
    import io
    from PIL import Image

    import foursquare
    import photos

    _fsq_reset()
    img = Image.new("RGB", (1200, 900), (40, 120, 180))
    exif = Image.Exif()
    exif[0x8825] = {1: "N", 2: (43.0, 39.0, 0.0)}
    jpg = io.BytesIO(); img.save(jpg, "JPEG", exif=exif)
    png = io.BytesIO(); Image.new("RGBA", (64, 64), (1, 2, 3, 0)).save(png, "PNG")
    base = serve({})
    for name, data in (("a.jpg", jpg), ("b.jpg", jpg), ("icon.png", png), ("me.jpg", jpg)):
        (SITE / name).write_bytes(data.getvalue())
    store = foursquare.checkin_store()
    cid, cid2 = "5f" + "0" * 22, "5f" + "0" * 21 + "1"
    store.records = {
        cid: {"checkin_id": cid, "created_ts": 2, "checkin_date": "2026-01-02", "venue_id": "v1", "venue_name": "P",
              "photo_urls": f"{base}/a.jpg {base}/b.jpg", "photo_count": 2, "category_id": "4bf58dd8d48988d16a941735", "category_icon": f"{base}/icon.png"},
        cid2: {"checkin_id": cid2, "created_ts": 1, "checkin_date": "2026-01-01", "venue_id": "v1", "venue_name": "P",
               "photo_urls": f"{base}/gone.jpg", "photo_count": 1, "category_id": "4bf58dd8d48988d16a941735", "category_icon": f"{base}/icon.png"},
    }
    store.save()
    (TMP / "backups" / "foursquare_profile.json").write_text(json.dumps({"name": "S", "avatar_url": f"{base}/me.jpg", "stats": {}}))
    up = {}
    assert photos.backup_foursquare(put=lambda k, d: up.__setitem__(k, d), delay=0) == 4  # 2 photos, 1 icon, 1 avatar
    assert set(up) == {f"fsq-photos/{cid}-1.webp", f"fsq-photos/{cid}-2.webp", f"fsq-thumbs/{cid}-1.webp", f"fsq-thumbs/{cid}-2.webp",
                       "fsq-cats/4bf58dd8d48988d16a941735.webp", "fsq-cats/avatar.webp"}
    full = Image.open(io.BytesIO(up[f"fsq-photos/{cid}-1.webp"]))
    assert full.format == "WEBP" and max(full.size) == 640 and not full.getexif() and "exif" not in full.info
    by = {r["checkin_id"]: r for r in load("foursquare_checkins")["items"]}
    assert by[cid]["photo_backup"] == 2 and not by[cid2].get("photo_backup")
    assert by[cid2]["category_icon_backup"] == "fsq-cats/4bf58dd8d48988d16a941735.webp"
    assert load("foursquare_places")["items"][0]["category_icon_backup"] == "fsq-cats/4bf58dd8d48988d16a941735.webp"
    assert json.loads((TMP / "backups" / "foursquare_profile.json").read_text())["avatar_backup"] == "fsq-cats/avatar.webp"
    up.clear()
    assert photos.backup_foursquare(put=lambda k, d: up.__setitem__(k, d), delay=0) == 0 and up == {}  # the missing one is retried, nothing redone
    assert photos.backup_foursquare() == 0  # no R2 secrets: skipped quietly


def test_worker_only_serves_swarm_paths_it_knows():
    """The Worker's path patterns, checked from here so a typo can't open the bucket."""
    import re

    src = (Path(__file__).resolve().parent.parent / "worker" / "index.js").read_text()
    swarm = re.compile(re.search(r"const SWARM = /(.+)/;", src).group(1).replace("\\/", "/"))
    data = re.compile(re.search(r"const SWARM_DATA = /(.+)/;", src).group(1).replace("\\/", "/"))
    cid = "5f" + "0" * 22
    for ok in (f"/fsq-photos/{cid}-1.webp", f"/fsq-thumbs/{cid}-12.webp", f"/fsq-venues/{cid}.webp", "/fsq-cats/avatar.webp", "/fsq-cats/4bf58dd8d48988d16a941735.webp"):
        assert swarm.match(ok), ok
    for bad in (f"/fsq-photos/{cid}.webp", "/fsq-photos/../photos/1.webp", f"/fsq-photos/{cid}-1.jpg", "/fsq-cats/a/b.webp", "/fsq-other/x.webp"):
        assert not swarm.match(bad), bad
    assert data.match("/data/foursquare_checkins.json") and data.match("/data/foursquare_places.csv") and data.match("/data/foursquare_profile.json")
    assert not data.match("/data/imdb_ratings.json") and not data.match("/data/foursquare_x/../status.json")
    cfg = (Path(__file__).resolve().parent.parent / "wrangler.jsonc").read_text()
    assert all(p in cfg for p in ('"/fsq-photos/*"', '"/fsq-thumbs/*"', '"/fsq-cats/*"', '"/data/foursquare_*"'))



def test_location_diary_never_ships_with_placeholder_targets():
    """Guards the public copy: a repo still on YOUR_… placeholders must not carry anyone's Swarm data."""
    root = Path(__file__).resolve().parent.parent
    if "YOUR_" in (root / "targets.json").read_text():
        assert not list((root / "backups").glob("foursquare_*")), "Swarm data found in a repo with placeholder targets"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
    print("outputs in", TMP)
