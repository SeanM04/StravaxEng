# Management Commands

All commands are run from the project root with the virtual environment active:

```bash
.venv\Scripts\activate       # Windows
source .venv/bin/activate    # macOS / Linux
```

---

## get_strava_token

File: [`core/management/commands/get_strava_token.py`](../core/management/commands/get_strava_token.py)

Runs the full Strava OAuth2 authorization-code flow and saves the token pair directly to
the `StravaToken` table. This is the primary way to authenticate for the first time or
after a token has been invalidated.

### `get_strava_token` — usage

```bash
python manage.py get_strava_token
```

### `get_strava_token` — steps

1. Builds a Strava authorization URL and opens it in your browser.
2. You click **Authorize** on the Strava page.
3. Strava redirects to `http://localhost/?code=XXXX` — the page won't load, that's expected.
4. Copy the full URL from the address bar and paste it into the terminal.
5. The command exchanges the code for an access token + refresh token.
6. Both tokens are saved to the `StravaToken` DB row. No `.env` editing needed.

### `get_strava_token` — notes

- After this command succeeds, run `sync_activities` immediately.
- Uses the `authorization_code` grant, which always works regardless of the state of any
  refresh token currently in `.env`.

---

## bootstrap_token

File: [`core/management/commands/bootstrap_token.py`](../core/management/commands/bootstrap_token.py)

Seeds `StravaToken` by exchanging the `STRAVA_REFRESH_TOKEN` value from `.env`.
Use this only when you already have a known-valid refresh token to seed.

### `bootstrap_token` — usage

```bash
python manage.py bootstrap_token
```

### `bootstrap_token` — notes

- Will fail with `400 Bad Request` if the refresh token in `.env` has already been rotated
  or invalidated. Run `get_strava_token` instead.
- Once `StravaToken` is in the DB, `strava_client.py` handles all future rotations
  automatically — you do not need to run this again.

---

## sync_activities

File: [`core/management/commands/sync_activities.py`](../core/management/commands/sync_activities.py)

ETL pipeline: fetches activities from the Strava API and upserts them into the `Activity`
table. Writes a `SyncLog` row for every run.

### `sync_activities` — usage

```bash
# Incremental sync (default) — fetches only since the last successful sync
python manage.py sync_activities

# Full re-sync — ignores last-sync timestamp, fetches everything
python manage.py sync_activities --full

# Control page size (default 100, max 200)
python manage.py sync_activities --per-page 200
```

### `sync_activities` — arguments

| Argument | Type | Default | Description |
| --- | --- | --- | --- |
| `--full` | flag | off | Ignore last-sync timestamp and fetch all activities |
| `--per-page` | int | 100 | Activities per API page (capped at 200) |

### `sync_activities` — steps

1. Determines the `after` timestamp — `finished_at` of the last `success` SyncLog, or
   none if running in full mode or no previous success exists.
2. Creates a `SyncLog` row with `status = running`.
3. Pages through `GET /api/v3/athlete/activities?after=<ts>&per_page=<n>&page=<p>`.
4. For each activity, calls `Activity.objects.update_or_create(strava_id=...)`.
5. Updates the SyncLog to `success` (or `failed` on exception) with counts and duration.

### `sync_activities` — example output

```text
Incremental sync — fetching activities after 2026-06-15 08:00:00 UTC
  Page 1: 12 activities processed
Done: 10 created, 2 updated across 1 page(s).
```

---

## seed_from_mcp

File: [`core/management/commands/seed_from_mcp.py`](../core/management/commands/seed_from_mcp.py)

Seeds the `Activity` table from a JSON file exported via the Strava MCP tool. Useful when
the Strava OAuth token is unavailable and activities were retrieved through Claude's MCP
connection instead.

### `seed_from_mcp` — usage

```bash
python manage.py seed_from_mcp --file mcp_activities.json
```

### `seed_from_mcp` — arguments

| Argument | Type | Default | Description |
| --- | --- | --- | --- |
| `--file` | path | `mcp_activities.json` | Path to the MCP-format JSON export |

### `seed_from_mcp` — field mapping

The MCP format nests summary fields differently from the REST API response.

| MCP field | Model field |
| --- | --- |
| `summary.distance` | `distance_meters` |
| `summary.moving_time` | `moving_time_seconds` |
| `summary.elapsed_time` | `elapsed_time_seconds` |
| `summary.elevation_gain` | `total_elevation_gain` |
| `summary.avg_speed` | `average_speed` |
| `summary.avg_cadence` | `average_cadence` |
| `start_local` | `start_date` |
| `is_trainer` | `trainer` |
| `is_commute` | `commute` |

### `seed_from_mcp` — notes

- Safe to re-run — uses `update_or_create` on `strava_id`.
- Activities without a valid `id` or `start_local` are skipped.
