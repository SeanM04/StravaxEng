"""
Full OAuth2 authorization-code flow for Strava.

Run:
    python manage.py get_strava_token

Steps:
  1. Your browser opens the Strava authorization page.
  2. Click "Authorize" — you are redirected to http://localhost/?code=XXXX
     (the page fails to load — that is expected).
  3. Copy the full URL from the address bar and paste it here.
  4. Tokens are saved directly to the StravaToken DB table.

This is how a Strava account is connected, and re-connected if Strava revokes
access.  No .env edits are needed: afterwards ``manage.py sync`` renews the
token on its own — the DB is the source of truth.
"""
import urllib.parse
import webbrowser

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.models import StravaToken


class Command(BaseCommand):
    """Management command that runs the Strava OAuth2 authorization-code flow.

    Opens the user's browser to Strava's authorization page, prompts for the
    resulting redirect URL, exchanges the embedded code for a token pair, and
    persists the tokens to the ``StravaToken`` table.
    """

    help = "Run the Strava OAuth2 authorization-code flow and save tokens to the DB."

    def handle(self, *args, **options):
        """Execute the interactive OAuth2 flow.

        Args:
            *args: Unused positional arguments passed by Django.
            **options: Unused parsed CLI options.

        Raises:
            CommandError: If the app credentials are missing from ``.env``,
                the user cancels, the pasted URL has no authorization code,
                or Strava rejects the code exchange.
        """
        client_id     = settings.STRAVA_CLIENT_ID
        client_secret = settings.STRAVA_CLIENT_SECRET

        if not client_id or not client_secret:
            raise CommandError("STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET must be set in .env")

        code    = self._ask_for_code(client_id)
        data    = self._exchange_code(client_id, client_secret, code)
        athlete = data.get("athlete") or {}

        if not athlete.get("id"):
            raise CommandError("Strava did not return an athlete ID. Response:\n" + str(data))

        # ── Step 4: Persist to DB ─────────────────────────────────────────────
        _, created = StravaToken.objects.update_or_create(
            athlete_id=athlete["id"],
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
            f"{action} token for athlete {athlete['id']} ({name}).\n\n"
            f"The DB is now the source of truth. Run:\n"
            f"  python manage.py sync\n"
            f"to pull your latest Strava activities."
        ))

    def _ask_for_code(self, client_id: str) -> str:
        """Send the user to Strava's authorization page and read back the code.

        Args:
            client_id: The Strava app's client ID.

        Returns:
            The authorization code from the redirect URL the user pastes.

        Raises:
            CommandError: If the user cancels, Strava reports an error, or the
                pasted URL has no ``code`` parameter.
        """
        # ── Step 1: Build authorization URL ──────────────────────────────────
        params = urllib.parse.urlencode({
            "client_id":       client_id,
            "redirect_uri":    "http://localhost",
            "response_type":   "code",
            "approval_prompt": "force",
            "scope":           "activity:read_all",
        })
        auth_url = f"https://www.strava.com/oauth/authorize?{params}"

        self.stdout.write("\n=== Strava OAuth2 Flow ===\n")
        self.stdout.write("Opening your browser to Strava's authorization page...")
        self.stdout.write(f"\nIf the browser doesn't open, visit:\n{auth_url}\n")
        webbrowser.open(auth_url)

        self.stdout.write(
            "\nAfter clicking 'Authorize' you'll be redirected to http://localhost/?code=...\n"
            "The page won't load — that's fine. Copy the FULL URL from the address bar.\n"
        )

        # ── Step 2: Get the authorization code from the user ─────────────────
        try:
            redirect_url = input("Paste the full redirect URL here: ").strip()
        except (EOFError, KeyboardInterrupt):
            raise CommandError("Aborted.") from None

        query = urllib.parse.parse_qs(urllib.parse.urlparse(redirect_url).query)

        if "error" in query:
            raise CommandError(
                f"Strava returned an error: {query['error'][0]}\n"
                "Make sure you clicked 'Authorize', not 'Cancel'."
            )
        if "code" not in query:
            raise CommandError(
                "Could not find 'code' in the URL.\n"
                "Make sure you copied the full redirect URL including the query string."
            )
        return query["code"][0]

    def _exchange_code(self, client_id: str, client_secret: str, code: str) -> dict:
        """Exchange an authorization code for a token pair.

        Args:
            client_id: The Strava app's client ID.
            client_secret: The Strava app's client secret.
            code: The authorization code from :meth:`_ask_for_code`.

        Returns:
            Strava's token response, including ``athlete``.

        Raises:
            CommandError: If Strava rejects the exchange.
        """
        # ── Step 3: Exchange code for tokens ──────────────────────────────────
        self.stdout.write("\nGot authorization code. Exchanging with Strava...")
        response = requests.post(
            settings.STRAVA_TOKEN_URL,
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
            except ValueError:  # requests' JSONDecodeError subclasses ValueError
                detail = response.text
            raise CommandError(f"Token exchange failed ({response.status_code}): {detail}")
        return response.json()
