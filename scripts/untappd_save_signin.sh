#!/usr/bin/env bash
# Save your Untappd sign-in as the daily job's fallback, from the Brewflix Codespace.
#   bash scripts/untappd_save_signin.sh            # log in if needed, then save
#   bash scripts/untappd_save_signin.sh --relogin  # log in again first (after it expires)
#
# The daily job finds new check-ins through your RSS feed (UNTAPPD_RSS_URL),
# which lists your latest 25. If you check in more than that between runs,
# the job falls back to this saved sign-in, which can see your whole history.
# The sign-in is stored only as the encrypted repository secret
# UNTAPPD_STORAGE_STATE; it is never committed.
set -euo pipefail
cd "$(dirname "$0")/.."
state=.secrets/untappd_state.json

if [ "${1:-}" = "--relogin" ] || { [ ! -f "$state" ] && [ "${1:-}" != "--no-login" ]; }; then
  export DISPLAY="${DISPLAY:-:1}"
  echo "Log in to Untappd in the browser desktop:"
  echo "  Ports tab → 'Browser desktop' (port 6080) → globe icon → Connect (password: vscode)."
  python scraper/untappd.py --login
fi
if [ ! -f "$state" ]; then
  echo "No saved Untappd sign-in to store (run without --no-login to log in)."
  exit 1
fi
if command -v gh >/dev/null && base64 -w0 "$state" | gh secret set UNTAPPD_STORAGE_STATE 2>/dev/null; then
  echo "Saved your Untappd sign-in as the UNTAPPD_STORAGE_STATE secret."
else
  echo "This Codespace isn't allowed to set repository secrets."
  echo "Create the secret yourself: Settings → Secrets and variables → Actions → New repository secret,"
  echo "named UNTAPPD_STORAGE_STATE, with the text printed by:"
  echo "  base64 -w0 $state; echo"
  exit 1
fi
