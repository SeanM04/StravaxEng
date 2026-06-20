"""
Full OAuth2 authorization-code flow for Strava.

Run:
    python manage.py get_strava_token

Steps:
  1. Your browser opens the Strava authorization page.
  2. Click "Authorize" — you're redirected to http://localhost/?code=XXXX
     (the page fails to load — that's expected).
  3. Copy the full URL from the address bar and paste it here.
  4. Tokens are saved directly to the StravaToken DB table.

No .env edits needed. After this runs successfully, sync_activities
and bootstrap_token are not required — the DB is the source of truth.
"""
import urllib.parse
import webbrowser

import requests
from django.conf import settings
from django.core.management.base import BaseCommand

from core.models import StravaToken


class Command(BaseCommand):
    help = "Run the Strava OAuth2 authorization-code flow and save tokens to the DB."

    def handle(self, *args, **options):
        client_id     = settings.STRAVA_CLIENT_ID
        client_secret = settings.STRAVA_CLIENT_SECRET

        if not client_id or not client_secret:
            self.stderr.write(self.style.ERROR(
                "STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET must be set in .env"
            ))
            return

        # ── Step 1: Build authorization URL ──────────────────────────────────
        params = urllib.parse.urlencode({
            "client_id":      client_id,
            "redirect_uri":   "http://localhost",
            "response_type":  "code",
            "approval_prompt": "force",
            "scope":          "activity:read_all",
        })
        auth_url = f"https://www.strava.com/oauth/authorize?{params}"

        self.stdout.write("\n=== Strava OAuth2 Flow ===\n")
        self.stdout.write("Opening your browser to Strava's authorization page...")
        self.stdout.write(f"\nIf the browser doesn't open, visit this URL:\n{auth_url}\n")
        webbrowser.open(auth_url)

        self.stdout.write(
            "\nAfter clicking 'Authorize' you'll be redirected to http://localhost/?code=...\n"
            "The page won't load — that's fine. Copy the FULL URL from the address bar.\n"
        )

        # ── Step 2: Get the authorization code from the user ─────────────────
        try:
            redirect_url = input("Paste the full redirect URL here: ").strip()
        except (EOFError, KeyboardInterrupt):
            self.stderr.write(self.style.ERROR("\nAborted."))
            return

        parsed = urllib.parse.urlparse(redirect_url)
        query  = urllib.parse.parse_qs(parsed.query)

        if "error" in query:
            self.stderr.write(self.style.ERROR(
                f"Strava returned an error: {query['error'][0]}\n"
                "Make sure you clicked 'Authorize', not 'Cancel'."
            ))
            return

        if "code" not in query:
            self.stderr.write(self.style.ERROR(
                "Could not find 'code' in the URL.\n"
                "Make sure you copied the full redirect URL including the query string."
            ))
            return

        code = query["code"][0]
        self.stdout.write(f"\nGot authorization code. Exchanging with Strava...")

        # ── Step 3: Exchange code for tokens ──────────────────────────────────
        response = requests.post(
            "https://www.strava.com/oauth/token",
            data={
                "client_id":     client_id,
                "client_secret": client_secret,
                "code":          code,
                "grant_type":    "authorization_code",
            },
            timeout=15,
        )

        if not response.ok:
            try:
                detail = response.json()
            except Exception:
                detail = response.text
            self.stderr.write(self.style.ERROR(
                f"Token exchange failed ({response.status_code}): {detail}"
            ))
            return

        data = response.json()
        athlete    = data.get("athlete") or {}
        athlete_id = athlete.get("id")

        if not athlete_id:
            self.stderr.write(self.style.ERROR(
                "Strava did not return an athlete ID. Response:\n" + str(data)
            ))
            return

        # ── Step 4: Persist to DB ─────────────────────────────────────────────
        token, created = StravaToken.objects.update_or_create(
            athlete_id=athlete_id,
            defaults={
                "access_token":  data["access_token"],
                "refresh_token": data["refresh_token"],
                "expires_at":    data["expires_at"],
            },
        )

        action = "Created" if created else "Updated"
        name   = f"{athlete.get('firstname', '')} {athlete.get('lastname', '')}".strip()

        self.stdout.write(self.style.SUCCESS(
            f"\n=== SUCCESS ===\n"
            f"{action} token for athlete {athlete_id} ({name}).\n"
            f"Access token:  {data['access_token'][:16]}…\n"
            f"Refresh token: {data['refresh_token'][:16]}…\n\n"
            f"The DB is now the source of truth. Run:\n"
            f"  python manage.py sync_activities\n"
            f"to pull your latest Strava activities."
        ))
