#!/usr/bin/env python3
"""Check which Foursquare v2 endpoints still answer for your token.

  FOURSQUARE_TOKEN=... python scripts/foursquare_probe.py

Prints a status line and the names of the fields each endpoint returns. It
prints no check-in content and never prints the token. Useful once after
setup, and whenever a counter (mayorships, coins, stickers) is missing from
the site.
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("FOURSQUARE_API_BASE", "https://api.foursquare.com/v2").rstrip("/")
TOKEN = os.environ.get("FOURSQUARE_TOKEN", "").strip()


def get(path, **params):
    q = urllib.parse.urlencode({"v": "20260101", **params})
    req = urllib.request.Request(f"{BASE}/{path}?{q}", headers={"Authorization": f"Bearer {TOKEN}", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            return r.status, json.loads(r.read().decode()), dict(r.headers)
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode())
        except ValueError:
            body = {}
        return e.code, body, dict(e.headers)
    except Exception as e:  # network trouble
        return 0, {"meta": {"errorType": str(e)}}, {}


def shape(v, depth=0):
    """Field names only, two levels deep."""
    if isinstance(v, dict):
        return "{" + ", ".join(f"{k}{': ' + shape(x, depth + 1) if depth < 1 and isinstance(x, (dict, list)) else ''}" for k, x in v.items()) + "}"
    if isinstance(v, list):
        return f"[{len(v)} × {shape(v[0], depth + 1) if v else ''}]"
    return type(v).__name__


def main():
    if not TOKEN:
        sys.exit("Set FOURSQUARE_TOKEN first.")
    newest = None
    checks = [
        ("users/self", {}),
        ("users/self/checkins", {"limit": 1}),
        ("users/self/checkins", {"limit": 250}),
        ("users/self/checkins", {"limit": 1, "sort": "oldestfirst"}),
        ("users/self/checkins", {"limit": 1, "afterTimestamp": 1700000000}),
        ("users/self/mayorships", {}),
        ("users/self/venuehistory", {"limit": 1}),
        ("users/self/tips", {"limit": 1}),
        ("users/self/photos", {"limit": 1}),
        ("users/self/badges", {}),
        ("users/self/stickers", {}),
        ("users/self/coins", {}),
    ]
    for path, params in checks:
        code, body, headers = get(path, **params)
        res = body.get("response") or {}
        extra = ""
        if path == "users/self/checkins" and code == 200:
            items = (res.get("checkins") or {}).get("items") or []
            extra = f" · count={res.get('checkins', {}).get('count')} · returned={len(items)}"
            if items and not newest:
                newest = items[0]
        limit = headers.get("X-RateLimit-Remaining") or headers.get("x-ratelimit-remaining")
        print(f"{code:>3}  {path}  {params or ''}{extra}"
              f"{' · rate-limit left ' + limit if limit else ''}"
              f"{' · ' + str(body.get('meta', {}).get('errorType')) if code != 200 else ''}")
        if code == 200 and params in ({}, {"limit": 1}):
            print(f"       fields: {shape(res)}")
    if newest:
        print("\nFields on a check-in:", ", ".join(sorted(newest)))
        v = newest.get("venue") or {}
        print("Fields on its venue:", ", ".join(sorted(v)))
        print("Fields on its location:", ", ".join(sorted(v.get("location") or {})))
        if v.get("id"):
            code, body, _ = get(f"venues/{v['id']}/photos", limit=1)
            print(f"\n{code:>3}  venues/<id>/photos{'' if code == 200 else ' · ' + str(body.get('meta', {}).get('errorType'))}")


if __name__ == "__main__":
    main()
