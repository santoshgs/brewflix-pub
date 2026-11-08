"""Step 2: scrape public IMDb ratings with Playwright.

Data comes from two places, merged by title id (tt…):
  1. JSON: the page's __NEXT_DATA__ blob plus any GraphQL responses fired
     while paginating (richest source: ids, titles, years, types, ratings).
  2. DOM:  .ipc-metadata-list-summary-item cards (fallback, and the source
     of "Rated on <date>").

Usage:
  python scraper/imdb.py              # incremental: stop once known titles appear
  python scraper/imdb.py --full       # walk every page
  python scraper/imdb.py --headful    # visible browser (WAF / debugging)
"""
from __future__ import annotations

import argparse
import json
import re
import time

from bs4 import BeautifulSoup
from dateutil import parser as dp

from common import TARGETS, Store, is_challenge, launch, log, to_float, wait_for_human

CFG = TARGETS["imdb"]
TT = re.compile(r"\btt\d{5,10}\b")
MORE_BTN = "button.ipc-see-more__button, span.ipc-see-more button, button:has-text('more')"


# ---------- JSON (NEXT_DATA / GraphQL) ----------

def _txt(v):
    if isinstance(v, dict):
        return v.get("text") or v.get("displayableProperty", {}).get("value", {}).get("plainText")
    return v


def _rating_from(d: dict):
    """Pull a user rating out of an edge/node dict, whatever IMDb calls it."""
    for k, v in d.items():
        kl = k.lower()
        if "ratingssummary" in kl or kl == "aggregaterating":
            continue
        if "userrating" in kl or kl in ("rating", "otheruserrating", "authorrating"):
            if isinstance(v, (int, float)):
                return v
            if isinstance(v, dict):
                for kk in ("value", "rating", "score"):
                    if isinstance(v.get(kk), (int, float)):
                        return v[kk]
    return None


def _date_from(d: dict):
    for k, v in d.items():
        kl = k.lower()
        if isinstance(v, str) and ("date" in kl or kl in ("createdat", "ratedat")) and re.match(r"\d{4}-\d{2}-\d{2}", v):
            return v[:10]
        if isinstance(v, dict):
            for dk in ("date", "createdAt", "ratedAt"):
                if isinstance(v.get(dk), str) and re.match(r"\d{4}-\d{2}-\d{2}", v[dk]):
                    return v[dk][:10]
    return None


def parse_json_titles(blob) -> dict[str, dict]:
    """Walk any IMDb JSON payload and return {const_id: record}."""
    out: dict[str, dict] = {}

    def walk(o, ancestors):
        if isinstance(o, dict):
            tid = o.get("id") or o.get("const")
            if isinstance(tid, str) and TT.fullmatch(tid) and ("titleText" in o or "originalTitleText" in o):
                rec = out.setdefault(tid, {"const_id": tid, "url": f"https://www.imdb.com/title/{tid}/"})
                rec["title"] = rec.get("title") or _txt(o.get("titleText")) or _txt(o.get("originalTitleText"))
                ry = o.get("releaseYear")
                if isinstance(ry, dict) and ry.get("year"):
                    rec["year"] = ry["year"]
                tt = o.get("titleType")
                if isinstance(tt, dict):
                    rec["title_type"] = tt.get("text") or tt.get("id")
                tg = o.get("titleGenres")
                if isinstance(tg, dict) and isinstance(tg.get("genres"), list):
                    names = [(g.get("genre") or {}).get("text") for g in tg["genres"] if isinstance(g, dict)]
                    if any(names):
                        rec["genres"] = ", ".join(n for n in names if n)
                pi = o.get("primaryImage")
                if isinstance(pi, dict) and pi.get("url"):
                    rec["poster_url"] = pi["url"]
                rs = o.get("ratingsSummary")
                if isinstance(rs, dict) and rs.get("aggregateRating") is not None:
                    rec["imdb_rating"] = rs["aggregateRating"]
                for anc in [o, *reversed(ancestors[-4:])]:
                    if rec.get("user_rating") is None and (r := _rating_from(anc)) is not None:
                        rec["user_rating"] = r
                    if not rec.get("date_rated") and (dt := _date_from(anc)):
                        rec["date_rated"] = dt
            for v in o.values():
                walk(v, ancestors + [o])
        elif isinstance(o, list):
            for v in o:
                walk(v, ancestors)

    walk(blob, [])
    return out


# ---------- DOM cards ----------

def parse_cards(html: str) -> dict[str, dict]:
    soup = BeautifulSoup(html, "lxml")
    out: dict[str, dict] = {}
    for card in soup.select(".ipc-metadata-list-summary-item"):
        link = card.select_one("a[href*='/title/tt']")
        if not link or not (m := TT.search(link["href"])):
            continue
        tid = m.group()
        h = card.select_one("h3, .ipc-title__text")
        title = re.sub(r"^\d+\.\s*", "", h.get_text(strip=True)) if h else None
        rec = {"const_id": tid, "title": title, "url": f"https://www.imdb.com/title/{tid}/"}
        img = card.select_one("img.ipc-image")
        if img and img.get("src", "").startswith("http"):
            rec["poster_url"] = img["src"]

        meta = [s.get_text(" ", strip=True) for s in card.select(".dli-title-metadata-item, .cli-title-metadata-item")]
        for s in meta:
            if re.fullmatch(r"\d{4}(–\d{0,4})?", s) and "year" not in rec:
                rec["year"] = int(s[:4])
            elif s in ("TV Series", "TV Mini Series", "TV Episode", "TV Movie", "Short", "Video", "Video Game", "TV Special", "Podcast Series"):
                rec["title_type"] = s
        rec.setdefault("title_type", None)

        imdb = card.select_one("[data-testid='ratingGroup--imdb-rating'] .ipc-rating-star--rating, .ipc-rating-star--imdb .ipc-rating-star--rating")
        if imdb:
            rec["imdb_rating"] = to_float(imdb.get_text())
        mine = card.select_one(
            "[data-testid='ratingGroup--other-user-rating'] .ipc-rating-star--rating, "
            ".ipc-rating-star--otherUserAlsoRated .ipc-rating-star--rating"
        )
        if mine:
            rec["user_rating"] = to_float(mine.get_text())

        text = card.get_text(" ", strip=True)
        if dm := re.search(r"Rated on\s+([A-Z][a-z]{2,8}\.? \d{1,2},? \d{4})", text):
            rec["date_rated"] = dp.parse(dm.group(1)).date().isoformat()
        out[tid] = rec
    return out


def parse_next_data(html: str) -> dict[str, dict]:
    soup = BeautifulSoup(html, "lxml")
    tag = soup.find("script", id="__NEXT_DATA__")
    if not tag or not tag.string:
        return {}
    return parse_json_titles(json.loads(tag.string))


def merge(*sources: dict[str, dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for src in sources:
        for k, rec in src.items():
            cur = out.setdefault(k, {})
            for f, v in rec.items():
                if v not in (None, "") and cur.get(f) in (None, ""):
                    cur[f] = v
    return out


# ---------- posters for titles that came from the CSV export ----------

def fetch_poster(tid: str) -> str | None:
    """IMDb's search-suggestion JSON includes each title's poster URL."""
    import urllib.request

    from common import DESKTOP_UA

    req = urllib.request.Request(
        f"https://v3.sg.media-imdb.com/suggestion/x/{tid}.json", headers={"User-Agent": DESKTOP_UA}
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read())
    for d in data.get("d", []):
        if d.get("id") == tid and isinstance(d.get("i"), dict):
            return d["i"].get("imageUrl")
    return None


def backfill_posters(store: Store, limit: int = 2000, delay: float = 0.25) -> int:
    """Fill poster_url for titles that don't have one yet (export rows, mostly).

    Titles with no poster on IMDb are marked "" so they aren't retried daily.
    """
    todo = [r for r in store.records.values() if r.get("poster_url") is None][:limit]
    done = 0
    for i, rec in enumerate(todo, 1):
        try:
            rec["poster_url"] = fetch_poster(rec["const_id"]) or ""
            done += 1
        except Exception as e:
            log(f"IMDb posters: {rec['const_id']} failed ({e}); will retry next run")
        if i % 100 == 0:
            log(f"IMDb posters: {i}/{len(todo)}")
            store.save()
        time.sleep(delay)
    if todo:
        log(f"IMDb posters: filled {done} of {len(todo)} missing")
    return done


# ---------- runner ----------

def run(full: bool = False, headful: bool = False, url: str | None = None, max_pages: int = 500) -> int:
    from playwright.sync_api import sync_playwright

    store = Store(CFG["output_base"], "const_id", CFG["fields"], sort_by="date_rated")
    start = len(store)
    url = url or CFG["ratings_url"]
    gql: dict[str, dict] = {}

    def on_response(resp):
        if "graphql" in resp.url and "json" in (resp.headers.get("content-type") or ""):
            try:
                gql.update(merge(gql, parse_json_titles(resp.json())))
            except Exception:
                pass

    with sync_playwright() as p:
        browser, ctx = launch(p, headless=not headful)
        page = ctx.new_page()
        page.on("response", on_response)
        log(f"IMDb: opening {url}")
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        if is_challenge(page):
            wait_for_human(page, "IMDb/AWS WAF challenge", headless=not headful)
        page.wait_for_selector(".ipc-metadata-list-summary-item", timeout=30_000)
        if "private" in page.content().lower() and not page.query_selector(".ipc-metadata-list-summary-item"):
            raise RuntimeError("IMDb ratings list is private — make it public in IMDb account settings")

        next_data = parse_next_data(page.content())
        seen_count, pages = 0, 1
        while pages < max_pages:
            cards = parse_cards(page.content())
            batch = merge(cards, next_data, gql)
            new_here = sum(1 for k in batch if k not in store)
            for rec in batch.values():
                store.upsert(rec)
            log(f"IMDb: page {pages}: {len(cards)} cards on screen, {len(store) - start} new so far")
            if not full and start and new_here == 0:
                log("IMDb: reached already-backed-up titles; stopping (incremental)")
                break
            btn = page.query_selector(MORE_BTN)
            if not btn or not btn.is_visible():
                break
            btn.scroll_into_view_if_needed()
            btn.click()
            time.sleep(1.5)
            try:
                page.wait_for_function(
                    f"document.querySelectorAll('.ipc-metadata-list-summary-item').length > {len(cards)}",
                    timeout=20_000,
                )
            except Exception:
                if len(parse_cards(page.content())) <= len(cards):
                    break
            pages += 1
            if len(cards) == seen_count:
                break
            seen_count = len(cards)
        browser.close()

    backfill_posters(store)
    store.save()
    log(f"IMDb: done — {len(store)} titles total, {len(store) - start} new")
    return len(store) - start


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--headful", action="store_true")
    ap.add_argument("--url")
    a = ap.parse_args()
    run(full=a.full, headful=a.headful, url=a.url)
