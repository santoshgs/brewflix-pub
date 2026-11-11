#!/usr/bin/env python3
"""One-time helper: get the Foursquare token the daily backup uses.

Meant to be run in a GitHub Codespace on this repo (Code > Codespaces), so
nothing has to be installed or run on your own computer. A phone browser works.

1. Create a project at https://foursquare.com/developers (free). In its OAuth
   settings add the redirect URL  https://localhost/  and note the Client ID
   and Client Secret.
2. In the Codespace terminal:  python scripts/foursquare_token.py
3. Open the link it prints, approve, and paste back the address your browser
   ends up on (it starts with https://localhost/ and won't load; that's fine).
4. The token is saved straight into this repo's FOURSQUARE_TOKEN secret. If
   the Codespace isn't allowed to do that, the token is shown once so you can
   paste it at Settings > Secrets and variables > Actions > New repository
   secret.

Nothing is written to disk.
"""
import getpass
import json
import shutil
import subprocess
import urllib.parse
import urllib.request

REDIRECT = "https://localhost/"


def main():
    client_id = input("Client ID: ").strip()
    client_secret = getpass.getpass("Client Secret (hidden): ").strip()
    auth = "https://foursquare.com/oauth2/authenticate?" + urllib.parse.urlencode(
        {"client_id": client_id, "response_type": "code", "redirect_uri": REDIRECT})
    print(f"\nOpen this link, approve, then copy the address you land on:\n\n  {auth}\n")
    landed = input("Paste that address (or just the code): ").strip()
    code = urllib.parse.parse_qs(urllib.parse.urlparse(landed).query).get("code", [landed])[0]
    url = "https://foursquare.com/oauth2/access_token?" + urllib.parse.urlencode(
        {"client_id": client_id, "client_secret": client_secret, "grant_type": "authorization_code",
         "redirect_uri": REDIRECT, "code": code})
    with urllib.request.urlopen(url, timeout=30) as r:
        token = json.loads(r.read().decode()).get("access_token")
    if not token:
        raise SystemExit("Foursquare didn't return a token. Check the Client ID, Secret and redirect URL.")
    if save_secret(token):
        print("\nSaved as this repo's FOURSQUARE_TOKEN secret. Next: Actions > Daily backup > Run workflow, with 'full' ticked.")
    else:
        print("\nCouldn't save the secret from here. Copy this token into Settings > Secrets and variables > Actions >"
              f"\nNew repository secret, named FOURSQUARE_TOKEN, then clear this terminal:\n\n  {token}\n")


def save_secret(token: str) -> bool:
    """Store the token as a repository secret with the GitHub CLI; the token goes in on stdin, never on a command line."""
    if not shutil.which("gh"):
        return False
    try:
        r = subprocess.run(["gh", "secret", "set", "FOURSQUARE_TOKEN"], input=token, text=True, capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0

if __name__ == "__main__":
    main()
