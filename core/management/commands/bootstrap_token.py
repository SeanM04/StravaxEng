"""
Seeds the StravaToken table using the refresh token in .env.

Run this once after completing the OAuth flow with get_strava_token.py.
After the first successful run, all future token rotations are handled
automatically by strava_client.get_access_token().
"""
import requests
from django.conf import settings
from django.core.management.base import BaseCommand
from core.models import StravaToken


class Command(BaseCommand):
    help = "Bootstrap the StravaToken table from the STRAVA_REFRESH_TOKEN in .env."

    def handle(self, *args, **options):
        if not settings.STRAVA_REFRESH_TOKEN:
            self.stderr.write(self.style.ERROR(
                "STRAVA_REFRESH_TOKEN is not set in .env"
            ))
            return

        self.stdout.write("Exchanging refresh token with Strava...")

        response = requests.post(
            settings.STRAVA_TOKEN_URL,
            data={
                "client_id":     settings.STRAVA_CLIENT_ID,
                "client_secret": settings.STRAVA_CLIENT_SECRET,
                "refresh_token": settings.STRAVA_REFRESH_TOKEN,
                "grant_type":    "refresh_token",
            },
            timeout=10,
        )

        if not response.ok:
            try:
                detail = response.json()
            except Exception:
                detail = response.text
            self.stderr.write(self.style.ERROR(
                f"Strava rejected the token ({response.status_code}): {detail}\n"
                "Run python get_strava_token.py to obtain a fresh refresh token, "
                "then update STRAVA_REFRESH_TOKEN in .env and retry."
            ))
            return

        data = response.json()
        athlete   = data.get("athlete") or {}
        athlete_id = athlete.get("id") or 0

        token, created = StravaToken.objects.update_or_create(
            athlete_id=athlete_id,
            defaults={
                "access_token":  data["access_token"],
                "refresh_token": data["refresh_token"],
                "expires_at":    data["expires_at"],
            },
        )

        action = "Created" if created else "Updated"
        self.stdout.write(self.style.SUCCESS(
            f"{action} token for athlete {athlete_id}. "
            "The DB is now the source of truth — "
            "strava_client will rotate tokens automatically from here on."
        ))
