"""
Strava OAuth2 token management and authenticated API access.

Token rotation contract
-----------------------
Strava access tokens expire after 6 hours.  When we exchange a refresh token,
Strava invalidates it and issues a *new* refresh token alongside the new access
token.  If we discard that new refresh token, the next call will fail with 400.

This module reads tokens from and writes them back to the StravaToken DB row,
so every rotation is persisted automatically.  The .env file is only used for
the initial bootstrap — after that, the DB is the source of truth.
"""
import logging
import time

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


def _load_token():
    """Fetch the single StravaToken row from the database.

    Returns:
        StravaToken: The persisted token object.

    Raises:
        RuntimeError: If no token row exists in the database.  The user must
            run ``python manage.py get_strava_token`` to perform the initial
            OAuth2 authorisation flow.
    """
    from core.models import StravaToken  # late import avoids circular dependency
    token = StravaToken.objects.first()
    if not token:
        raise RuntimeError(
            "No Strava token found in the database. "
            "Run: python manage.py get_strava_token"
        )
    return token


def _parse_response_body(response):
    """Parse a requests Response as JSON, falling back to raw text.

    Args:
        response: A ``requests.Response`` object whose body will be decoded.

    Returns:
        The parsed JSON value (dict or list) if the body is valid JSON,
        otherwise the raw response text as a string.
    """
    try:
        return response.json()
    except ValueError:
        return response.text


def _is_dead_token(status_code, body):
    """Return True if the refresh token is permanently invalid.

    A dead token cannot be recovered by retrying — the user must re-authorise
    via ``get_strava_token``.  Two conditions indicate this: a 401 Unauthorized
    response, or a Strava error body that explicitly marks the ``refresh_token``
    field as ``"invalid"``.

    Args:
        status_code (int): The HTTP status code from Strava's token endpoint.
        body: The parsed response body (dict) or raw text (str).

    Returns:
        bool: True when a retry would be pointless; False for transient errors.

    Notes:
        Strava returns 400 (not 401) for most invalid-token errors, so we
        inspect the error body rather than relying solely on the status code.
        The ``errors`` array in the response payload is the canonical indicator.
    """
    if status_code == 401:
        return True
    if isinstance(body, dict):
        errors = body.get("errors", [])
        return any(
            e.get("code") == "invalid" and e.get("field") == "refresh_token"
            for e in errors
        )
    return False


def get_access_token() -> str:
    """Return a valid Strava access token, refreshing if necessary.

    Returns the cached token if it still has more than 5 minutes of life.
    Otherwise calls ``POST /oauth/token``, persists the rotated token pair to
    the database, and returns the new access token.

    On a transient refresh failure (non-401, non-invalid_grant error), logs a
    warning and retries once after a 2-second delay.  A dead token (401 or
    ``invalid_grant``) raises immediately without retrying, since sending the
    same dead token a second time cannot succeed.

    Returns:
        str: A valid Bearer access token.

    Raises:
        RuntimeError: If no token exists in the database, if the refresh token
            is permanently invalid, or if the refresh request fails on both
            the initial attempt and the retry.

    Notes:
        Token rotation is atomic with respect to the DB: ``token.save()`` uses
        ``update_fields`` so only the three token columns are touched, reducing
        the chance of a race condition overwriting unrelated fields.
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
    """Make an authenticated GET request to the Strava v3 API.

    Args:
        endpoint: API path relative to the v3 base URL, e.g.
            ``"athlete/activities"``.  Leading slashes are stripped
            automatically.
        params: Optional query-string parameters to include in the request.

    Returns:
        The decoded JSON response body — a dict for single-resource endpoints
        or a list for collection endpoints.

    Raises:
        RuntimeError: If a valid access token cannot be obtained.
        requests.HTTPError: If Strava returns a non-2xx status for the
            resource request itself.
    """
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


def fetch_activities(
    after: int | None = None,
    per_page: int = 100,
    page: int = 1,
) -> list[dict]:
    """Return one page of the authenticated athlete's activities from Strava.

    Args:
        after: Unix timestamp — only return activities recorded after this
            time.  Pass ``None`` to fetch all activities (full sync).
        per_page: Number of results per page.  Strava's maximum is 200;
            default is 100.
        page: 1-indexed page number to retrieve.

    Returns:
        list[dict]: A list of Strava activity dicts.  An empty list indicates
        the last page has been reached.

    Raises:
        RuntimeError: If a valid access token cannot be obtained.
        requests.HTTPError: If the Strava API returns a non-2xx response.
    """
    params: dict = {"per_page": per_page, "page": page}
    if after is not None:
        params["after"] = after
    return strava_get("athlete/activities", params=params)
