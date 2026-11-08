"""Back up Untappd images to a private Cloudflare R2 bucket.

Your check-in photos are re-encoded as WebP with all metadata stripped, in two
sizes, and beer labels / brewery logos are kept once each (shared by every
check-in of that beer):

  photos/<checkin_id>.webp   longest side 640px  (full view)
  thumbs/<checkin_id>.webp   160px square crop   (list thumbnail)
  labels/<hash>.webp         longest side 200px  (beer label or brewery logo)

The Worker serves them at /photos/…, /thumbs/… and /labels/… only to visitors
who pass Cloudflare Access. Nothing is written to the git repo except the
"photo_backup" / "label_backup" markers on each record.

Needs these environment variables (GitHub secrets in the daily job); without
them the step is skipped, so clones of this repo never touch your bucket:
  R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET (optional)
"""
from __future__ import annotations

import hashlib
import io
import os
import time
import urllib.request

from common import DESKTOP_UA, TARGETS, Store, log

FULL_PX, THUMB_PX = 640, 160
DEFAULT_BUCKET = "brewflix-photos"


def r2_uploader():
    """Return put(key, data) for the configured bucket, or None if not configured."""
    acct = os.environ.get("R2_ACCOUNT_ID")
    key_id = os.environ.get("R2_ACCESS_KEY_ID")
    secret = os.environ.get("R2_SECRET_ACCESS_KEY")
    if not (acct and key_id and secret):
        return None
    import boto3

    bucket = os.environ.get("R2_BUCKET") or DEFAULT_BUCKET
    s3 = boto3.client(
        "s3",
        endpoint_url=f"https://{acct}.r2.cloudflarestorage.com",
        aws_access_key_id=key_id,
        aws_secret_access_key=secret,
        region_name="auto",
    )

    def put(key: str, data: bytes):
        s3.put_object(Bucket=bucket, Key=key, Body=data, ContentType="image/webp",
                      CacheControl="private, max-age=31536000, immutable")

    return put


def optimise(raw: bytes) -> tuple[bytes, bytes]:
    """Return (full, thumb) WebP bytes with no EXIF/GPS or other metadata."""
    from PIL import Image, ImageOps

    with Image.open(io.BytesIO(raw)) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")  # applies rotation, drops metadata
        full = im.copy()
        full.thumbnail((FULL_PX, FULL_PX), Image.LANCZOS)
        thumb = ImageOps.fit(im, (THUMB_PX, THUMB_PX), Image.LANCZOS)
    out_full, out_thumb = io.BytesIO(), io.BytesIO()
    full.save(out_full, "WEBP", quality=72, method=6)
    thumb.save(out_thumb, "WEBP", quality=70, method=6)
    return out_full.getvalue(), out_thumb.getvalue()


LABEL_PX = 200


def label_key(url: str) -> str:
    """Stable bucket key for a label URL (same image -> same key)."""
    return f"labels/{hashlib.sha1(url.encode()).hexdigest()[:16]}.webp"


def optimise_label(raw: bytes) -> bytes:
    from PIL import Image

    with Image.open(io.BytesIO(raw)) as im:
        im = im.convert("RGBA" if ("A" in im.getbands() or "transparency" in im.info) else "RGB")
        im.thumbnail((LABEL_PX, LABEL_PX), Image.LANCZOS)
        out = io.BytesIO()
        im.save(out, "WEBP", quality=80, method=6)
    return out.getvalue()


def backup_labels(put=None, limit: int = int(os.environ.get("LABELS_PER_RUN", "1000")), delay: float = 0.1) -> int:
    """Copy each beer label / brewery logo once; mark every check-in and beer using it."""
    from untappd import BEER_FIELDS, CHECKIN_FIELDS, PRIVATE_FIELDS, fix_beer, fix_checkin

    put = put or r2_uploader()
    if put is None:
        return 0
    stores = [
        Store(TARGETS["untappd"]["output_base"], "checkin_id", CHECKIN_FIELDS,
              sort_by="checkin_date", drop=PRIVATE_FIELDS, fix=fix_checkin),
        Store("backups/untappd_beers", "beer_id", BEER_FIELDS, sort_by="last_checkin", fix=fix_beer),
    ]
    by_url: dict[str, list[dict]] = {}
    for st in stores:
        for r in st.records.values():
            if r.get("label_url") and r.get("label_backup") != label_key(r["label_url"]):
                by_url.setdefault(r["label_url"], []).append(r)
    saved = 0
    for i, (url, recs) in enumerate(list(by_url.items())[:limit], 1):
        key = label_key(url)
        try:
            put(key, optimise_label(download(url)))
        except Exception as e:
            log(f"Labels: {url} failed ({e}); will retry next run")
            continue
        for r in recs:
            r["label_backup"] = key
        saved += 1
        if saved % 100 == 0:
            log(f"Labels: {saved}/{len(by_url)} saved")
        time.sleep(delay)
    for st in stores:
        st.save()
    if by_url:
        log(f"Labels: saved {saved} of {len(by_url)} new labels/logos to R2")
    return saved


def download(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": DESKTOP_UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def backup(put=None, limit: int = int(os.environ.get("PHOTOS_PER_RUN", "600")), delay: float = 0.2) -> int:
    """Copy photos not yet backed up. Returns how many were saved this run."""
    from untappd import CHECKIN_FIELDS, PRIVATE_FIELDS, fix_checkin

    put = put or r2_uploader()
    if put is None:
        log("Photos: R2 not configured (no R2_* secrets); skipping photo backup")
        return 0
    store = Store(TARGETS["untappd"]["output_base"], "checkin_id", CHECKIN_FIELDS,
                  sort_by="checkin_date", drop=PRIVATE_FIELDS, fix=fix_checkin)
    todo = [r for r in store.rows() if r.get("photo_url") and not r.get("photo_backup")][:limit]
    saved = 0
    for i, rec in enumerate(todo, 1):
        cid = rec["checkin_id"]
        try:
            full, thumb = optimise(download(rec["photo_url"]))
            put(f"photos/{cid}.webp", full)
            put(f"thumbs/{cid}.webp", thumb)
        except Exception as e:  # keep going; it's retried next run
            log(f"Photos: {cid} failed ({e}); will retry next run")
            continue
        rec["photo_backup"] = f"photos/{cid}.webp"
        saved += 1
        if saved % 50 == 0:
            store.save()
            log(f"Photos: {saved}/{len(todo)} saved")
        time.sleep(delay)
    store.save()
    if todo:
        log(f"Photos: saved {saved} of {len(todo)} new photos to R2")
    return saved


if __name__ == "__main__":
    backup()
    backup_labels()
