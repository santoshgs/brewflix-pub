#!/usr/bin/env bash
# Cloudflare Pages build command: copy the latest backups into the static viewer.
set -euo pipefail
mkdir -p site/data
cp backups/*.json backups/*.csv site/data/ 2>/dev/null || true
echo "site/data: $(ls site/data | wc -l) files"
