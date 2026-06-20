"""
Seeds the StravaToken table using the refresh token stored in .env.

This command is the legacy bootstrap path.  Prefer ``get_strava_token`` for
first-time setup, which performs the full OAuth2 authorization-code flow and
does not require manually copying a refresh token into .env.

After the first successful run, all future token rotations are handled
automatically by ``strava_client.get_access_token()``.
"""
import requests
from django.conf import settings
from django.core.management.base import BaseCommand
from core.models import StravaToken


class Command(BaseCommand):
    """Management command that bootstraps the StravaToken table from .env.

    Exchanges the ``STRAVA_REFRESH_TOKEN`` value in ``.env`` for a live
    access/refresh token pair via ``POST /oauth/token`` and upserts the
    result into the ``StravaToken`` table.
    """

    help = "Bootstrap the StravaToken table from the STRAVA_REFRESH_TOKEN in .env."

    def handle(self, *_args, **_options):
        """Execute the token bootstrap exchange.

        Args:
            *args: Unused positional arguments passed by Django.
            **options: Unused parsed CLI options.

        Raises:
            SystemExit: The command writes to stderr and returns early if
                ``STRAVA_REFRESH_TOKEN`` is not set or if Strava rejects the
                exchange request.
        """
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
            except ValueError:
                detail = response.text
            self.stderr.write(self.style.ERROR(
                f"Strava rejected the token ({response.status_code}): {detail}\n"
                "Run python manage.py get_strava_token to obtain a fresh token."
            ))
            return

        data = response.json()
        athlete    = data.get("athlete") or {}
        athlete_id = athlete.get("id") or 0

        _, created = StravaToken.objects.update_or_create(
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
