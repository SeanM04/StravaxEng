"""
Strava OAuth2 token management and authenticated API access.

Token rotation contract
-----------------------
Strava access tokens expire after 6 hours.  When we exchange a refresh token,
Strava invalidates it and issues a *new* refresh token alongside the new access
token.  If we discard that new refresh token, the next call will fail with 400.

This module reads tokens from and writes them back to the StravaToken DB row,
so every rotation is persisted automatically.  The first token pair comes from
``manage.py get_strava_token``; .env holds only the app's client ID and secret.

Rate limits
-----------
Every API request goes through ``strava_get``, whose ``RateLimiter`` reads the
usage Strava reports on each response and pauses or stops before the next
request goes over a limit.  Commands therefore need no rate-limit logic.
"""
import logging
import math
import time

import requests
from django.conf import settings

from core.models import StravaToken

logger = logging.getLogger(__name__)

_WINDOW_S = 15 * 60       # Strava's short window; resets at :00, :15, :30, :45
_DAY_S = 24 * 60 * 60     # daily allowance; resets at midnight UTC
_MARGIN_S = 5             # wait this much past a reset, for clock differences
_HEADROOM = 3             # requests kept spare in each window
_MAX_ATTEMPTS = 3         # per request, when Strava answers 429 Too Many Requests
# Header families reporting "<15-min>,<daily>" usage and limits: the read limit
# (which applies to every GET this app makes) and the overall limit.
_LIMIT_HEADERS = ("X-ReadRateLimit", "X-RateLimit")


class StravaUnavailableError(RuntimeError):
    """Strava can't be called right now; every further request would fail the same way.

    Callers that loop over activities stop at this error rather than recording
    the same failure for every remaining activity.
    """


# Included in every StravaAuthError message that needs the user to re-connect;
# the Settings page looks for it in the last sync's error to say so.
REAUTH_COMMAND = "python manage.py get_strava_token"


class StravaAuthError(StravaUnavailableError):
    """No usable Strava access token; run ``manage.py get_strava_token``."""


class StravaRateLimitError(StravaUnavailableError):
    """Strava's daily request allowance is used up; requests resume after midnight UTC."""


def _next_boundary(now: float, period_s: int) -> float:
    """Return the Unix time just after the next multiple of *period_s*.

    Args:
        now: Current Unix time.
        period_s: Window length in seconds.

    Returns:
        The reset time plus a small safety margin.
    """
    return now - now % period_s + period_s + _MARGIN_S


def _usage_and_limits(response):
    """Yield ``(usage, limit)`` pairs from *response*'s rate-limit headers.

    Args:
        response: A ``requests.Response`` from the Strava API.

    Yields:
        tuple[tuple[int, int], tuple[int, int]]: ``((used_15min, used_day),
        (limit_15min, limit_day))`` for each header family present.
    """
    for family in _LIMIT_HEADERS:
        try:
            usage = tuple(int(v) for v in response.headers[f"{family}-Usage"].split(","))
            limit = tuple(int(v) for v in response.headers[f"{family}-Limit"].split(","))
        except (KeyError, ValueError):
            continue
        if len(usage) == len(limit) == 2:
            yield usage, limit


class RateLimiter:
    """Keeps requests under Strava's 15-minute and daily limits.

    After each response, ``record`` notes whether a limit is nearly used up.
    Before the next request, ``before_request`` waits for the next 15-minute
    window, or refuses if the daily allowance is gone.  Waiting before the
    *next* request, rather than after this one, means a run never ends with a
    pointless pause.
    """

    def __init__(self):
        """Start with no pause and no daily block."""
        self.paused_until = 0.0
        self.daily_resets_at = 0.0

    def before_request(self) -> None:
        """Wait out a used-up 15-minute window; refuse if the day's allowance is used.

        Raises:
            StravaRateLimitError: If the daily allowance is used up.
        """
        now = time.time()
        if now < self.daily_resets_at:
            raise StravaRateLimitError(
                "Strava's daily API limit is used up; the next sync after midnight UTC "
                "will pick up where this one stopped."
            )
        if now < self.paused_until:
            wait_s = self.paused_until - now
            logger.warning(
                "Strava's 15-minute API limit is nearly used up; pausing %d min.",
                math.ceil(wait_s / 60),
            )
            time.sleep(wait_s)

    def record(self, response) -> None:
        """Note from *response* whether the next request must wait or be refused.

        Args:
            response: A ``requests.Response`` from the Strava API.
        """
        now = time.time()
        for (used_window, used_day), (limit_window, limit_day) in _usage_and_limits(response):
            if used_day >= limit_day - _HEADROOM:
                self.daily_resets_at = _next_boundary(now, _DAY_S)
            if used_window >= limit_window - _HEADROOM:
                self.paused_until = _next_boundary(now, _WINDOW_S)
        if response.status_code == 429:
            self.paused_until = max(self.paused_until, _next_boundary(now, _WINDOW_S))


rate_limiter = RateLimiter()


def _load_token():
    """Fetch the single StravaToken row from the database.

    Returns:
        StravaToken: The persisted token object.

    Raises:
        StravaAuthError: If no token row exists in the database.  The user must
            run ``python manage.py get_strava_token`` to perform the initial
            OAuth2 authorisation flow.
    """
    token = StravaToken.objects.first()
    if not token:
        raise StravaAuthError(
            f"No Strava token found in the database. Run: {REAUTH_COMMAND}"
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
        StravaAuthError: If no token exists in the database, if the refresh token
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
            raise StravaAuthError(
                f"Strava refresh token is invalid or revoked ({response.status_code}). "
                f"Re-run: {REAUTH_COMMAND}"
            )

        logger.warning(
            "Strava token refresh failed (%s) — retrying in 2 s: %s",
            response.status_code, body,
        )
        time.sleep(2)
        response = requests.post(settings.STRAVA_TOKEN_URL, data=payload, timeout=10)

        if not response.ok:
            detail = _parse_response_body(response)
            raise StravaAuthError(
                f"Strava token refresh failed after retry ({response.status_code}): {detail}"
            )

    data = response.json()
    token.access_token  = data["access_token"]
    token.refresh_token = data["refresh_token"]
    token.expires_at    = data["expires_at"]
    token.save(update_fields=["access_token", "refresh_token", "expires_at", "updated_at"])
    return token.access_token


def strava_get(endpoint: str, params: dict | None = None) -> dict | list:
    """Make an authenticated, rate-limited GET request to the Strava v3 API.

    Waits for the next 15-minute window when Strava reports the current one
    nearly used up, and retries after a ``429 Too Many Requests``.

    Args:
        endpoint: API path relative to the v3 base URL, e.g.
            ``"athlete/activities"``.  Leading slashes are stripped
            automatically.
        params: Optional query-string parameters to include in the request.

    Returns:
        The decoded JSON response body — a dict for single-resource endpoints
        or a list for collection endpoints.

    Raises:
        StravaAuthError: If a valid access token cannot be obtained.
        StravaRateLimitError: If Strava's daily request allowance is used up.
        requests.HTTPError: If Strava returns a non-2xx status for the
            resource request itself (including a 429 that persists after
            retries).
    """
    access_token = get_access_token()
    url = f"{settings.STRAVA_API_BASE}/{endpoint.lstrip('/')}"
    for _ in range(_MAX_ATTEMPTS):
        rate_limiter.before_request()
        response = requests.get(
            url,
            headers={"Authorization": f"Bearer {access_token}"},
            params=params or {},
            timeout=15,
        )
        rate_limiter.record(response)
        if response.status_code != 429:
            break
    response.raise_for_status()
    return response.json()


def fetch_activity_streams(strava_id: int) -> dict[str, list]:
    """Return distance and time GPS streams for a single activity.

    Calls ``GET /activities/{id}/streams?keys=distance,time&key_by_type=true``
    and extracts the data arrays for each stream type.

    Args:
        strava_id: The Strava activity ID.

    Returns:
        dict with keys ``"distance"`` and ``"time"``, each a list of
        floats/ints.  Either list may be empty for activities without GPS data
        (e.g. indoor trainer sessions).

    Raises:
        StravaAuthError: If a valid access token cannot be obtained.
        requests.HTTPError: If the Strava API returns a non-2xx status.
    """
    data = strava_get(
        f"activities/{strava_id}/streams",
        params={"keys": "distance,time", "key_by_type": "true"},
    )
    return {
        "distance": data.get("distance", {}).get("data", []),
        "time":     data.get("time", {}).get("data", []),
    }


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
        StravaAuthError: If a valid access token cannot be obtained.
        requests.HTTPError: If the Strava API returns a non-2xx response.
    """
    params: dict = {"per_page": per_page, "page": page}
    if after is not None:
        params["after"] = after
    return strava_get("athlete/activities", params=params)
