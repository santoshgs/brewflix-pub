#!/usr/bin/env python3
"""One-time helper: get the Foursquare token the daily backup uses.

1. Create a project at https://foursquare.com/developers (free). In its OAuth
   settings add the redirect URL  https://localhost/  and note the Client ID
   and Client Secret.
2. Run:  python scripts/foursquare_token.py
3. Open the link it prints, approve, and paste back the address your browser
   ends up on (it starts with https://localhost/ and won't load; that's fine).
4. Save the printed token as the GitHub repository secret FOURSQUARE_TOKEN
   (Settings > Secrets and variables > Actions).

Nothing is written to disk.
"""
import getpass
import json
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
    print(f"\nYour token (save it as the FOURSQUARE_TOKEN secret, then clear your terminal):\n\n  {token}\n")


if __name__ == "__main__":
    main()
