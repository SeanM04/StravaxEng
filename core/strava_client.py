"""
Strava OAuth2 token management and authenticated API access.

Token rotation contract
-----------------------
Strava access tokens expire after 6 hours. When we exchange a refresh token,
Strava invalidates it and issues a *new* refresh token alongside the new access
token. If we discard that new refresh token, the next call will fail with 400.

This module reads tokens from and writes them back to the StravaToken DB row,
so every rotation is persisted automatically. The .env file is only used for
the initial bootstrap — after that, the DB is the source of truth.
"""
import logging
import time

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


def _load_token():
    from core.models import StravaToken  # late import avoids circular dependency
    token = StravaToken.objects.first()
    if not token:
        raise RuntimeError(
            "No Strava token found in the database. "
            "Run: python manage.py get_strava_token"
        )
    return token


def _parse_response_body(response):
    try:
        return response.json()
    except ValueError:
        return response.text


def _is_dead_token(status_code, body):
    """True when the refresh token is revoked or invalid — retrying won't help."""
    if status_code == 401:
        return True
    if isinstance(body, dict):
        errors = body.get("errors", [])
        return any(e.get("code") == "invalid" and e.get("field") == "refresh_token" for e in errors)
    return False


def get_access_token() -> str:
    """
    Return a valid access token.

    Returns the cached token if it still has more than 5 minutes of life.
    Otherwise calls /oauth/token, persists the rotated token pair, and returns
    the new access token. Retries once after 2 s on transient failures; raises
    immediately on 401 or invalid_grant without retrying.
    """
    token = _load_token()
    if not token.is_expired:
        return token.access_token

    payload = {
        "client_id":     settings.STRAVA_CLIENT_ID,
        "client_secret": settings.STRAVA_CLIENT_SECRET,
        "refresh_token": token.refresh_token,
        "grant_type":    "refresh_token",
    }

    response = requests.post(settings.STRAVA_TOKEN_URL, data=payload, timeout=10)

    if not response.ok:
        body = _parse_response_body(response)

        if _is_dead_token(response.status_code, body):
            raise RuntimeError(
                f"Strava refresh token is invalid or revoked ({response.status_code}). "
                "Re-run: python manage.py get_strava_token"
            )

        logger.warning(
            "Strava token refresh failed (%s) — retrying in 2 s: %s",
            response.status_code, body,
        )
        time.sleep(2)
        response = requests.post(settings.STRAVA_TOKEN_URL, data=payload, timeout=10)

        if not response.ok:
            detail = _parse_response_body(response)
            raise RuntimeError(
                f"Strava token refresh failed after retry ({response.status_code}): {detail}"
            )

    data = response.json()
    token.access_token  = data["access_token"]
    token.refresh_token = data["refresh_token"]
    token.expires_at    = data["expires_at"]
    token.save(update_fields=["access_token", "refresh_token", "expires_at", "updated_at"])
    return token.access_token


def strava_get(endpoint: str, params: dict | None = None) -> dict | list:
    """Authenticated GET against the Strava v3 API."""
    access_token = get_access_token()
    url = f"{settings.STRAVA_API_BASE}/{endpoint.lstrip('/')}"
    response = requests.get(
        url,
        headers={"Authorization": f"Bearer {access_token}"},
        params=params or {},
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


def fetch_activities(after: int | None = None, per_page: int = 100, page: int = 1) -> list[dict]:
    """
    Return a page of the authenticated athlete's activities.

    Parameters
    ----------
    after    : Unix timestamp — only return activities recorded after this time.
               Pass None for a full sync.
    per_page : Results per page (Strava max: 200).
    page     : Page number (1-indexed).
    """
    params: dict = {"per_page": per_page, "page": page}
    if after is not None:
        params["after"] = after
    return strava_get("athlete/activities", params=params)
