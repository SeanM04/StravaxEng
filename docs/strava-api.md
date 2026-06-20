# Strava API

## Getting credentials

1. Go to [strava.com/settings/api](https://www.strava.com/settings/api) and create an application.
2. Note the **Client ID** and **Client Secret** — copy them into `.env`.
3. Run the OAuth flow below to get a valid token pair stored in the database.

## OAuth2 authorization flow

Strava uses OAuth2 with **rolling refresh tokens**: every call to `/oauth/token` returns a
brand-new refresh token and invalidates the previous one. The token pair must be persisted
to the database (`StravaToken` model) on every exchange — never rely solely on `.env`.

### Recommended: management command (automated)

```bash
python manage.py get_strava_token
```

This command handles the full flow interactively:

1. Opens your browser to the Strava authorization page.
2. You click **Authorize** — Strava redirects to `http://localhost/?code=XXXX` (page won't load — that's expected).
3. Copy the full URL from the address bar and paste it into the terminal.
4. The command exchanges the code for tokens and saves the pair directly to `StravaToken` in the DB.

No `.env` editing required. After this runs, `sync_activities` is ready to use.

### Manual alternative: `get_strava_token.py`

If you prefer to handle the flow yourself:

**Step 1 — Open the authorization URL in a browser:**

```text
https://www.strava.com/oauth/authorize
  ?client_id=<YOUR_CLIENT_ID>
  &redirect_uri=http://localhost
  &response_type=code
  &approval_prompt=force
  &scope=activity:read_all
```

**Step 2 — Exchange the code:**

```bash
curl -X POST https://www.strava.com/oauth/token \
  -d client_id=<YOUR_CLIENT_ID> \
  -d client_secret=<YOUR_CLIENT_SECRET> \
  -d code=<CODE_FROM_STEP_1> \
  -d grant_type=authorization_code
```

The response contains `access_token`, `refresh_token`, and `expires_at`.
Save `refresh_token` to `.env`, then run:

```bash
python manage.py bootstrap_token
```

This exchanges the `.env` refresh token and seeds `StravaToken` in the DB.

## Token refresh logic

File: [`core/strava_client.py`](../core/strava_client.py)

`get_access_token()` reads the `StravaToken` row. If the access token is within 5 minutes
of expiry it POSTs a refresh grant and writes the new token pair back to the same row:

```python
token = StravaToken.objects.get(athlete_id=ATHLETE_ID)
if token.is_expired:
    response = requests.post(STRAVA_TOKEN_URL, data={
        "client_id":     CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": token.refresh_token,
        "grant_type":    "refresh_token",
    })
    data = response.json()
    token.access_token  = data["access_token"]
    token.refresh_token = data["refresh_token"]   # rotated — must save
    token.expires_at    = data["expires_at"]
    token.save(update_fields=["access_token", "refresh_token", "expires_at", "updated_at"])
return token.access_token
```

> **Why not store the refresh token in `.env`?**
> Strava invalidates the old refresh token the moment a new one is issued. If the rotated
> token is discarded (e.g. only the access token is saved), the next refresh call returns
> `400 Bad Request: refresh_token invalid`. The DB row is the only reliable store.

## Endpoints used

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/oauth/token` | POST | Authorization-code exchange and refresh-token rotation |
| `/api/v3/athlete/activities` | GET | List authenticated athlete's activities |

### Activity list parameters

| Parameter | Type | Description |
| --- | --- | --- |
| `per_page` | int (max 200) | Activities per page |
| `page` | int | Page number (1-indexed) |
| `after` | Unix timestamp | Return only activities after this time (used for incremental sync) |
| `before` | Unix timestamp | Return only activities before this time |

## Rate limits

Strava enforces **100 requests / 15 minutes** and **1 000 requests / day**.
Each `sync_activities` page costs 1 API call (token refresh is free if the access token
is still valid). Syncing 10 pages costs 10 requests.
