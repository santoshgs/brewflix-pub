#!/usr/bin/env bash
# Move an existing Codespace onto the current (cleaned) GitHub history while
# keeping flavour tags it already collected. Safe to run more than once:
#   git fetch origin main && git show FETCH_HEAD:scripts/codespace_resync.sh | bash
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

git rebase --abort 2>/dev/null || true
git merge --abort 2>/dev/null || true

mkdir -p .secrets   # git-ignored: survives the reset, never committed
saved=.secrets/checkins_before_resync.json
if [ -f backups/untappd_checkins.json ]; then
  cp backups/untappd_checkins.json "$saved"
  echo "Kept a private copy of this Codespace's check-ins (with flavours)."
fi

git fetch -q origin main
git reset -q --hard FETCH_HEAD
echo "Codespace now matches GitHub: $(git log --oneline -1)"

if [ -f "$saved" ]; then
  python - "$saved" <<'PY'
import json, sys
sys.path.insert(0, "scraper")
from common import Store, TARGETS
from untappd import CHECKIN_FIELDS, PRIVATE_FIELDS, fix_checkin

old = json.load(open(sys.argv[1])).get("items", [])
store = Store(TARGETS["untappd"]["output_base"], "checkin_id", CHECKIN_FIELDS,
              sort_by="checkin_date", drop=PRIVATE_FIELDS, fix=fix_checkin)
kept = 0
for r in old:
    rec = store.records.get(str(r.get("checkin_id")))
    if rec is not None and r.get("details_v") is not None:
        rec["flavors"] = r.get("flavors")
        rec["details_v"] = r["details_v"]
        kept += bool(r.get("flavors"))
store.save()  # cleaned: no venue, no comments, day-only dates
print(f"Carried over flavour tags for {kept} check-ins.")
PY
fi
echo "Done. Now run: bash scripts/untappd_backfill.sh"
