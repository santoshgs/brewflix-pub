"""Step 3: scrape Untappd check-ins (activity feed) and unique beers.

Usage:
  python scraper/untappd.py                  # incremental (daily)
  python scraper/untappd.py --full --headful # one-time backfill; pauses for
                                             # Cloudflare / login if needed
  python scraper/untappd.py --login          # log in once, save the session
                                             # to .secrets/untappd_state.json

Logged-out pagination is capped by Untappd after a few pages. A saved
session (see --login) lifts that cap locally and, stored as the
UNTAPPD_STORAGE_STATE GitHub secret, in the daily job too.
"""
from __future__ import annotations

import argparse
import os
import random
import re
import time
from pathlib import Path

from bs4 import BeautifulSoup
from dateutil import parser as dp

from common import ROOT, TARGETS, Store, is_challenge, launch, local_date, log, to_float, wait_for_human

CFG = TARGETS["untappd"]
BASE = "https://untappd.com"
CHECKIN_FIELDS = CFG["fields"] + ["flavors", "beer_id", "checkin_url", "label_url"]
# never stored: where you drank and what you wrote are personal, not needed for a beer log
PRIVATE_FIELDS = ("comment", "venue", "venue_name", "purchase_venue", "venue_city", "venue_state", "venue_country", "venue_lat", "venue_lng")
# bump when the check-in page parser improves, so old check-ins get re-read
DETAILS_VERSION = 2  # 2: also reads the beer's global rating (the logged-out feed doesn't show it)
BEER_FIELDS = [
    "beer_id", "beer_name", "brewery_name", "style", "abv", "ibu", "user_rating",
    "global_rating", "checkin_count", "first_checkin", "last_checkin", "beer_url", "label_url",
]
STATE_PATH = os.environ.get("UNTAPPD_STATE_FILE", str(ROOT / ".secrets" / "untappd_state.json"))
MORE_CHECKINS = "a.more_checkins, .more_checkins a, a.more-checkins"
MORE_BEERS = "a.more_beers, a.more-list-items, .more_beers a"


def _abs(href: str | None) -> str | None:
    if not href:
        return None
    return href if href.startswith("http") else BASE + href


def _id_from(href: str | None, kind: str) -> str | None:
    if href and (m := re.search(rf"/{kind}/[^/]+/(\d+)", href)):
        return m.group(1)
    return None


def _img(tag) -> str | None:
    if not tag:
        return None
    src = tag.get("data-original") or tag.get("data-src") or tag.get("src") or ""
    return src if src.startswith("http") else None


def _iso(s: str | None) -> str | None:
    if not s:
        return None
    try:
        return dp.parse(s).isoformat()
    except (ValueError, OverflowError):
        return s


def parse_checkins(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    out = []
    for item in soup.select("div.item[data-checkin-id], div.item[id^='checkin_']"):
        cid = item.get("data-checkin-id") or item["id"].removeprefix("checkin_")
        text = item.select_one("p.text") or item
        beer_a = text.select_one("a[href*='/b/']")
        links = [a for a in text.select("a") if a is not beer_a and "user" not in (a.get("class") or [])]
        brewery_a = next((a for a in links if not re.match(r"^/(v|b|user)/", a.get("href", ""))), None)
        caps = item.select_one(".caps[data-rating]")
        serving = item.select_one(".serving span, p.serving")
        photo = item.select_one(".photo img")
        t = item.select_one("a.time[data-gregtime], .time[data-gregtime], [data-gregtime]")
        link = item.select_one("a[href*='/checkin/']")
        label_img = item.select_one("a.label img, .label img")
        out.append(
            {
                "checkin_id": str(cid),
                "beer_id": _id_from(beer_a.get("href") if beer_a else None, "b"),
                "beer_name": beer_a.get_text(strip=True) if beer_a else None,
                "brewery_name": brewery_a.get_text(strip=True) if brewery_a else None,
                "user_rating": to_float(caps["data-rating"]) if caps else None,
                "checkin_date": local_date(t.get("data-gregtime")) if t else None,
                "serving_type": serving.get_text(strip=True) if serving else None,
                "photo_url": (photo.get("data-original") or photo.get("src")) if photo else None,
                "checkin_url": _abs(link["href"]) if link else f"{BASE}/user/{CFG['username']}/checkin/{cid}",
                "label_url": _img(label_img),
            }
        )
    return out


def parse_beers(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    out = []
    for item in soup.select(".beer-item[data-bid], .distinct-list-item[data-bid]"):
        name_a = item.select_one(".name a, p.name a")
        label_img = item.select_one("a.label img, .label img")
        ratings = item.select(".ratings .caps[data-rating], .ratings [data-rating]")
        rating_text = {}
        for blk in item.select(".ratings .you, .ratings > div"):
            label = blk.get_text(" ", strip=True).lower()
            caps = blk.select_one("[data-rating]")
            if caps:
                rating_text["global" if "global" in label else "user"] = to_float(caps["data-rating"])
        dates = {}
        for p in item.select("p.date"):
            lbl = p.get_text(" ", strip=True).lower()
            abbr = p.select_one("abbr, .date-time")
            val = local_date((abbr.get("title") if abbr and abbr.get("title") else abbr.get_text(strip=True)) if abbr else None)
            dates["first" if lbl.startswith("first") else "last"] = val
        total = item.select_one(".check-ins")
        out.append(
            {
                "beer_id": str(item["data-bid"]),
                "beer_name": name_a.get_text(strip=True) if name_a else None,
                "brewery_name": (b.get_text(strip=True) if (b := item.select_one(".brewery")) else None),
                "style": (s.get_text(strip=True) if (s := item.select_one(".style")) else None),
                "abv": to_float(a.get_text()) if (a := item.select_one(".abv")) else None,
                "ibu": to_float(i.get_text()) if (i := item.select_one(".ibu")) and "N/A" not in i.get_text() else None,
                "user_rating": rating_text.get("user", to_float(ratings[0]["data-rating"]) if ratings else None),
                "global_rating": rating_text.get("global", to_float(ratings[1]["data-rating"]) if len(ratings) > 1 else None),
                "checkin_count": int(to_float(total.get_text()) or 0) if total else None,
                "first_checkin": dates.get("first"),
                "last_checkin": dates.get("last"),
                "beer_url": _abs(name_a["href"]) if name_a else None,
                "label_url": _img(label_img),
            }
        )
    return out


FLAVOR_LABEL = re.compile(r"flavou?r\s*profiles?", re.I)


def parse_flavors(html: str) -> list[str]:
    """Flavour tags from a single check-in page ('Flavor Profiles' section)."""
    soup = BeautifulSoup(html, "lxml")
    out: list[str] = []

    def take(container):
        for t in container.select("li, span, a"):
            if t.find(["li", "span", "a"]):
                continue  # only leaf tags
            txt = t.get_text(" ", strip=True)
            if txt and len(txt) <= 30 and not FLAVOR_LABEL.fullmatch(txt):
                out.append(txt)

    for el in soup.select("[class*='flavor'], [class*='Flavor'], [class*='flavour']"):
        take(el)
    if not out:  # fall back to the text label and the list right after it
        lab = soup.find(string=FLAVOR_LABEL)
        if lab and lab.parent:
            nxt = lab.parent.find_next(["ul", "div", "p"])
            if nxt:
                take(nxt)
    seen, uniq = set(), []
    for f in out:
        if f.lower() not in seen:
            seen.add(f.lower())
            uniq.append(f)
    return uniq


def parse_global(html: str) -> float | None:
    """The beer's global rating from a page, if the page shows one."""
    soup = BeautifulSoup(html, "lxml")
    for blk in soup.select(".ratings > div, .ratings .you"):
        if "global" in blk.get_text(" ", strip=True).lower():
            caps = blk.select_one("[data-rating]")
            if caps:
                return to_float(caps["data-rating"])
    return None


def enrich_details(page, checkins: Store, limit: int) -> int:
    """Open individual check-in pages (oldest-unread first) to read flavour tags."""
    todo = [r for r in checkins.records.values() if r.get("details_v") != DETAILS_VERSION and r.get("checkin_url")]
    todo.sort(key=lambda r: str(r.get("checkin_date") or ""), reverse=True)
    todo = todo[:limit]
    found = globals_found = 0
    for i, rec in enumerate(todo, 1):
        try:
            page.goto(rec["checkin_url"], wait_until="domcontentloaded", timeout=45_000)
        except Exception as e:
            log(f"Untappd details: {rec['checkin_id']} failed to load ({e}); will retry")
            continue
        if is_challenge(page) or "/login" in page.url:
            log("Untappd details: blocked or login required; stopping for this run")
            break
        html = page.content()
        flavors = parse_flavors(html)
        if i == 1 and not flavors and (m := re.search(r"(?i)flavou?r", html)):
            # help future parser fixes: show the markup around the word
            log("Untappd details: 'flavor' found but no tags parsed; context:\n"
                + html[max(0, m.start() - 300): m.start() + 600])
        rec["flavors"] = ", ".join(flavors) or None
        if rec.get("global_rating") in (None, ""):
            rec["global_rating"] = parse_global(html)
            globals_found += rec["global_rating"] is not None
        rec["details_v"] = DETAILS_VERSION
        found += bool(flavors)
        if i % 25 == 0:
            checkins.save()
            log(f"Untappd details: {i}/{len(todo)} read, {found} with flavours, {globals_found} with global rating")
        time.sleep(random.uniform(1.5, 2.0))
    if todo:
        log(f"Untappd details: read {len(todo)} check-in pages, {found} had flavour tags, {globals_found} global ratings filled")
    return found


def fix_checkin(r: dict):
    """Keep only the calendar day of a check-in (also cleans older backups)."""
    if r.get("checkin_date"):
        r["checkin_date"] = local_date(r["checkin_date"])


def fix_beer(r: dict):
    for f in ("first_checkin", "last_checkin"):
        if r.get(f):
            r[f] = str(r[f])[:10]


def _paginate(page, item_sel: str, more_sel: str, parse, store: Store, full: bool, headful: bool, label: str):
    start = len(store)
    rounds, stale = 0, 0
    while True:
        before = len(page.query_selector_all(item_sel))
        new_here = sum(store.upsert(r) for r in parse(page.content()))
        log(f"Untappd {label}: {before} on page, {len(store) - start} new so far")
        if not full and start and new_here == 0:
            log(f"Untappd {label}: caught up with existing backup; stopping")
            break
        btn = page.query_selector(more_sel)
        if not btn or not btn.is_visible():
            log(f"Untappd {label}: no more 'Show More' button — end of list")
            break
        btn.scroll_into_view_if_needed()
        btn.click()
        time.sleep(random.uniform(1.5, 2.0))
        try:
            page.wait_for_function(
                f"document.querySelectorAll({item_sel!r}).length > {before}", timeout=15_000
            )
            stale = 0
        except Exception:
            stale += 1
            if "/login" in page.url or page.query_selector("form[action*='login']"):
                wait_for_human(page, "Untappd wants a login to load more (log in, then go back to the list)", not headful)
            elif is_challenge(page):
                wait_for_human(page, "Cloudflare challenge", not headful)
            elif stale >= 2:
                log(f"Untappd {label}: list stopped growing (logged-out cap?). Use --login once for full history.")
                break
        rounds += 1
    store.save()


def login(state_path: str = STATE_PATH):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, ctx = launch(p, headless=False)
        page = ctx.new_page()
        page.goto(f"{BASE}/login")
        input("Log in to Untappd in the browser window, then press Enter here... ")
        Path(state_path).parent.mkdir(parents=True, exist_ok=True)
        ctx.storage_state(path=state_path)
        browser.close()
    log(f"saved session to {state_path} (keep it private — never commit it)")


def run(full: bool = False, headful: bool = False, profile_url: str | None = None, beers_url: str | None = None,
        details_limit: int = int(os.environ.get("UNTAPPD_DETAILS_PER_RUN", "120"))) -> int:
    from playwright.sync_api import sync_playwright

    checkins = Store(CFG["output_base"], "checkin_id", CHECKIN_FIELDS, sort_by="checkin_date", drop=PRIVATE_FIELDS, fix=fix_checkin)
    beers = Store("backups/untappd_beers", "beer_id", BEER_FIELDS, sort_by="last_checkin", fix=fix_beer)
    start = len(checkins)

    with sync_playwright() as p:
        browser, ctx = launch(p, headless=not headful, storage_state=STATE_PATH)
        page = ctx.new_page()
        for label, url, item_sel, more_sel, parse, store in (
            ("check-ins", profile_url or CFG["profile_url"], "div.item[data-checkin-id], div.item[id^='checkin_']", MORE_CHECKINS, parse_checkins, checkins),
            ("beers", beers_url or CFG["beers_url"], ".beer-item[data-bid], .distinct-list-item[data-bid]", MORE_BEERS, parse_beers, beers),
        ):
            log(f"Untappd: opening {url}")
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            if is_challenge(page):
                wait_for_human(page, "Cloudflare challenge on Untappd", not headful)
            try:
                page.wait_for_selector(item_sel, timeout=30_000)
            except Exception:
                if label == "beers":  # often login-only; check-ins still count as a success
                    log("Untappd beers: list not visible (login required?) — skipping; style/ABV not enriched")
                    continue
                raise
            _paginate(page, item_sel, more_sel, parse, store, full, headful, label)
        try:
            enrich_details(page, checkins, limit=details_limit)
        except Exception as e:  # never lose the feed data over this extra step
            log(f"Untappd details: stopped ({e})")
        browser.close()

    # enrich check-ins with beer metadata only the unique-beers list shows
    for c in checkins.records.values():
        b = beers.records.get(str(c.get("beer_id")))
        if b:
            for f in ("style", "abv", "ibu", "global_rating", "label_url"):
                if c.get(f) in (None, ""):
                    c[f] = b.get(f)
    checkins.save()
    log(f"Untappd: {len(checkins)} check-ins ({len(checkins) - start} new), {len(beers)} unique beers")
    return len(checkins) - start


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--headful", action="store_true")
    ap.add_argument("--login", action="store_true")
    a = ap.parse_args()
    login() if a.login else run(full=a.full, headful=a.headful)
