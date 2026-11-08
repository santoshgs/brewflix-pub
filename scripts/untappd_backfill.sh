#!/usr/bin/env bash
# One-time Untappd history backfill, run inside the Brewflix Codespace.
#   bash scripts/untappd_backfill.sh
# 1. Opens Untappd's login page in the browser desktop (port 6080).
# 2. Walks your whole check-in feed and unique-beers list while logged in,
#    then opens each check-in page once to read its flavour profile tags.
# 3. Commits and pushes backups/ so the viewer updates.
set -euo pipefail
cd "$(dirname "$0")/.."
export DISPLAY="${DISPLAY:-:1}"

# The Codespace installs these in the background after it opens; if that
# hasn't finished (or failed), do it here so the script always works.
if ! python -c "import bs4, lxml, dateutil, playwright" 2>/dev/null; then
  echo "Installing Python packages (first run only, ~1 minute)…"
  python -m pip install -q -r requirements.txt
fi
if ! python -c "from playwright.sync_api import sync_playwright as s; p=s().start(); b=p.chromium.launch(); b.close(); p.stop()" 2>/dev/null; then
  echo "Installing the Chromium browser (first run only, ~2 minutes)…"
  python -m playwright install --with-deps chromium
fi

# The desktop (display :1) belongs to the Codespace's normal user. If this
# terminal is root, borrow that user's X credentials and let root draw on it.
if [ "$(id -u)" = 0 ]; then
  for f in /home/*/.Xauthority; do
    [ -f "$f" ] && export XAUTHORITY="$f" && break
  done
  owner=$(stat -c %U "/tmp/.X11-unix/X${DISPLAY#:}" 2>/dev/null || true)
  if [ -n "$owner" ] && [ "$owner" != root ] && command -v xhost >/dev/null 2>&1; then
    su "$owner" -c "DISPLAY=$DISPLAY xhost +SI:localuser:root" >/dev/null 2>&1 || true
  fi
fi

# Make sure a visible browser window can actually open on the desktop.
if ! python -c "from playwright.sync_api import sync_playwright as s; p=s().start(); b=p.chromium.launch(headless=False, args=['--disable-gpu','--no-sandbox']); b.close(); p.stop()" 2>/tmp/headed.log; then
  echo "Can't open a window on the browser desktop (display $DISPLAY)."
  echo "Is the desktop running? Check: ls /tmp/.X11-unix  (should list X1)"
  tail -5 /tmp/headed.log
  exit 1
fi

echo
echo "Step 1 of 2 — log in to Untappd"
echo "  Ports tab → 'Browser desktop' (port 6080) → globe icon."
echo "  Tap Connect; the password is: vscode"
echo "  The desktop is plain black until a window opens. A Chromium window"
echo "  with Untappd's login page appears within ~10 seconds of this message."
echo "  Log in there, then come back here and press Enter."
echo
if [ -f .secrets/untappd_state.json ] && [ "${1:-}" != "--relogin" ]; then
  echo "  Using your saved Untappd login (run with --relogin to log in again)."
else
  python scraper/untappd.py --login
fi

echo
echo "Step 2 of 2 — downloading your full history (keep this tab open)…"
# also open every check-in page once, logged in, to read your flavour tags
UNTAPPD_DETAILS_PER_RUN=5000 python scraper/run_all.py --only untappd --full --headful

# Safety checks before anything leaves this Codespace
python - <<'PY'
import json, sys
bad = []
for f in ("backups/untappd_checkins.json", "backups/untappd_beers.json"):
    try:
        items = json.load(open(f)).get("items", [])
    except FileNotFoundError:
        continue
    for r in items:
        if any(k in r for k in ("comment", "venue", "venue_name", "purchase_venue")):
            bad.append(f"{f}: private field in {r.get('checkin_id') or r.get('beer_id')}")
        if len(str(r.get("checkin_date") or "")) > 10:
            bad.append(f"{f}: clock time in {r.get('checkin_id')}")
if bad:
    print("Not pushing: backups contain private data:", *bad[:5], sep="\n  ")
    sys.exit(1)
PY
git fetch -q origin main
if ! git merge-base --is-ancestor HEAD FETCH_HEAD && ! git merge-base --is-ancestor FETCH_HEAD HEAD; then
  echo "Not pushing: this Codespace's history differs from GitHub's."
  echo "Run: git fetch origin main && git show FETCH_HEAD:scripts/codespace_resync.sh | bash"
  exit 1
fi

git add backups/
if git diff --cached --quiet; then
  echo "Nothing new to save."
else
  git -c user.name="backup-bot" -c user.email="41898282+github-actions[bot]@users.noreply.github.com" \
    commit -m "Untappd full history backfill"
  git rebase -q FETCH_HEAD
  git push -q origin HEAD:main
  echo "Saved and pushed. The viewer redeploys in a minute or two."
fi
echo
echo "Done. You can stop this Codespace now (it only uses free hours while running)."
