"""Shared helpers: config, merge-by-key storage, JSON+CSV output, browser setup."""
from __future__ import annotations

import csv
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# where backups/ and imports/ live (overridable for tests)
DATA_ROOT = Path(os.environ.get("BACKUP_ROOT", ROOT))
TARGETS = json.loads((ROOT / "targets.json").read_text())

DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
IN_CI = os.environ.get("CI") == "true"
# dates are stored as local calendar days (no clock times) in this zone
LOCAL_TZ = os.environ.get("BACKUP_TZ") or "UTC"  # set BACKUP_TZ to your zone, e.g. Europe/London


def log(*a):
    print(f"[{datetime.now():%H:%M:%S}]", *a, file=sys.stderr, flush=True)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Store:
    """Keyed record store persisted as <base>.json + <base>.csv.

    Records are merged by `key`, so re-running a scrape only adds or
    refreshes rows; nothing already backed up is ever dropped.
    """

    def __init__(self, base: str, key: str, fields: list[str], sort_by: str | None = None,
                 drop: tuple[str, ...] = (), fix=None):
        self.base = DATA_ROOT / base
        self.key = key
        self.fields = fields
        self.sort_by = sort_by
        self.drop = set(drop)  # fields never kept (and removed from old backups)
        self.fix = fix  # optional per-record clean-up, applied on load and on every upsert
        self.records: dict[str, dict] = {}
        self.added = 0
        self._since_save = 0
        jp = self.base.with_suffix(".json")
        if jp.exists():
            data = json.loads(jp.read_text() or "{}")
            for r in data.get("items", data if isinstance(data, list) else []):
                for f in self.drop:
                    r.pop(f, None)
                if fix:
                    fix(r)
                self.records[str(r[key])] = r

    def __contains__(self, k) -> bool:
        return str(k) in self.records

    def __len__(self) -> int:
        return len(self.records)

    def upsert(self, rec: dict, save_every: int = 50) -> bool:
        """Insert or update; returns True if the record was new."""
        k = str(rec[self.key])
        rec = {f: v for f, v in rec.items() if f not in self.drop}
        if self.fix:
            self.fix(rec)
        new = k not in self.records
        old = self.records.get(k, {})
        # keep old non-empty values when the new scrape didn't see a field
        merged = {**{f: None for f in rec}, **old, **{f: v for f, v in rec.items() if v not in (None, "")}}
        self.records[k] = merged
        if new:
            self.added += 1
            self._since_save += 1
            if save_every and self._since_save >= save_every:
                self.save()
        return new

    def rows(self) -> list[dict]:
        rows = list(self.records.values())
        if self.sort_by:
            # newest first; same-day ties broken by id (Untappd/IMDb ids grow over time)
            rows.sort(key=lambda r: (str(r.get(self.sort_by) or ""), str(r.get(self.key) or "").zfill(20)),
                      reverse=True)
        return rows

    def save(self):
        self._since_save = 0
        self.base.parent.mkdir(parents=True, exist_ok=True)
        rows = self.rows()
        payload = {"updated_at": now_iso(), "count": len(rows), "items": rows}
        tmp = self.base.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        tmp.replace(self.base.with_suffix(".json"))
        extra = sorted({k for r in rows for k in r} - set(self.fields))
        with open(self.base.with_suffix(".csv"), "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=self.fields + extra, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        log(f"saved {len(rows)} rows -> {self.base.relative_to(DATA_ROOT)}.json/.csv")


def local_date(v) -> str | None:
    """Any timestamp -> 'YYYY-MM-DD' in LOCAL_TZ. Times are deliberately dropped.

    Naive timestamps are treated as UTC (how Untappd stores them); a bare
    date is returned unchanged.
    """
    if v in (None, ""):
        return None
    s = str(v).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s
    from zoneinfo import ZoneInfo

    from dateutil import parser as dp

    try:
        dt = dp.parse(s)
    except (ValueError, OverflowError):
        return s[:10] if re.match(r"\d{4}-\d{2}-\d{2}", s) else None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ZoneInfo(LOCAL_TZ)).date().isoformat()


def to_float(v):
    if v in (None, ""):
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", str(v).replace(",", ""))
    return float(m.group()) if m else None


def launch(p, headless: bool, storage_state: str | None = None):
    args = ["--disable-blink-features=AutomationControlled", "--disable-dev-shm-usage"]
    if not headless and sys.platform.startswith("linux"):
        # remote desktops (Codespaces/VNC) have no GPU; software rendering avoids a blank window
        args += ["--disable-gpu", "--use-gl=swiftshader", "--window-size=1200,760"]
    browser = p.chromium.launch(headless=headless, args=args)
    ctx = browser.new_context(
        user_agent=DESKTOP_UA,
        viewport={"width": 1366, "height": 900},
        locale="en-US",
        extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        storage_state=storage_state if storage_state and Path(storage_state).exists() else None,
    )
    ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
    return browser, ctx


def is_challenge(page) -> bool:
    try:
        title = page.title().lower()
    except Exception:
        return False
    return any(s in title for s in ("just a moment", "attention required", "access denied"))


def wait_for_human(page, reason: str, headless: bool):
    """Pause for the user to solve a challenge / log in in the visible browser."""
    if headless or IN_CI or not sys.stdin.isatty():
        raise RuntimeError(f"{reason} — rerun locally with --headful to resolve it")
    log(f"ACTION NEEDED: {reason}")
    input("    Solve it in the browser window, then press Enter here to continue... ")
    page.wait_for_load_state("domcontentloaded")
