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

- After this command succeeds, run `sync` immediately.
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

## sync

File: [`core/management/commands/sync.py`](../core/management/commands/sync.py)

The everyday command. Runs the whole pipeline in order:

1. `sync_activities` — new activities from Strava.
2. `backfill_best_efforts` — best efforts for any activity still missing them.
3. `fetch_achievements` — segment achievements for activities not yet fetched.

If step 1 fails (e.g. the Strava token was revoked), steps 2–3 are skipped. If step 2 or 3
has failures, the other still runs, the run's `SyncLog` is marked `partial` with the details,
and the command exits with an error; the failed items are retried by the next sync. A lock file
(`.sync.lock`) prevents two syncs from running at once; a lock older than 3 hours is treated
as left over from a crashed run.

The **Sync now** button on the Pipeline page runs this same command in the background and
appends its output to `sync.log` in the project root.

### `sync` — usage

```bash
python manage.py sync          # incremental
python manage.py sync --full   # re-fetch every activity
```

### `sync` — daily schedule (Windows Task Scheduler)

[`scripts/sync_daily.cmd`](../scripts/sync_daily.cmd) runs `manage.py sync` with the project's
virtualenv and appends the output to `sync.log`. Register it once from PowerShell (adjust the
path and time as needed):

```powershell
schtasks /Create /TN "StravaXEng daily sync" /TR "C:\Users\Mukar\OneDrive\Desktop\StravaXeng\scripts\sync_daily.cmd" /SC DAILY /ST 21:00 /F
```

- Runs while you are logged in; PostgreSQL must be running. A console window shows while it runs.
- Run it on demand: `schtasks /Run /TN "StravaXEng daily sync"`
- Remove it: `schtasks /Delete /TN "StravaXEng daily sync" /F`

---

## sync_activities

File: [`core/management/commands/sync_activities.py`](../core/management/commands/sync_activities.py)

ETL pipeline: fetches activities from the Strava API and upserts them into the `Activity`
table. Writes a `SyncLog` row for every run.

### `sync_activities` — usage

```bash
# Incremental sync (default) — activities started up to 7 days before the newest stored one
python manage.py sync_activities

# Full re-sync — fetches everything
python manage.py sync_activities --full

# Control page size (default 100, max 200)
python manage.py sync_activities --per-page 200
```

### `sync_activities` — arguments

| Argument | Type | Default | Description |
| --- | --- | --- | --- |
| `--full` | flag | off | Fetch all activities instead of the last 7 days' worth |
| `--per-page` | int | 100 | Activities per API page (capped at 200) |

### `sync_activities` — steps

1. Marks any run left `running` for over 3 hours as `failed` (interrupted).
2. Determines the `after` timestamp — the start time of the newest stored activity minus 7 days (`INCREMENTAL_OVERLAP`). Strava's `?after=` filters on when an activity *started*, so this catches activities uploaded late (e.g. a watch that syncs days afterwards); the re-fetched activities are simply upserted. None in full mode or when no
   activities are stored yet.
3. Creates a `SyncLog` row with `status = running`.
4. Pages through `GET /api/v3/athlete/activities?after=<ts>&per_page=<n>&page=<p>`.
5. For each activity, calls `Activity.objects.update_or_create(strava_id=...)`.
6. For each newly-created activity, fetches its GPS stream and runs the sliding-window
   best-effort algorithm via `best_effort.compute_and_save`.
7. Updates the SyncLog with counts and an outcome: `success`; `partial` if some GPS streams
   could not be fetched (those activities are retried by `backfill_best_efforts`); or `failed`
   if activities could not be fetched, in which case the command exits with an error.

### `sync_activities` — example output

```text
Incremental sync — fetching activities started after 2026-10-20 08:28 SAST (7 days before the newest stored activity)
  Page 1: 8 activities processed
Done: 1 created, 7 updated across 1 page(s).
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

---

## fetch_achievements

File: [`core/management/commands/fetch_achievements.py`](../core/management/commands/fetch_achievements.py)

Fetches segment achievements for every activity that has `achievement_count > 0` and has not
been fetched yet, and stores them as `Achievement` rows. Each call hits `GET /activities/{id}` (the detailed endpoint) to
read `segment_efforts[].achievements[]`.

### `fetch_achievements` — usage

```bash
# Process activities not fetched yet
python manage.py fetch_achievements

# Test with a small batch first
python manage.py fetch_achievements --limit 20

# Re-fetch every activity with achievement_count > 0
python manage.py fetch_achievements --force
```

### `fetch_achievements` — arguments

| Argument | Type | Default | Description |
| --- | --- | --- | --- |
| `--limit` | int | none | Stop after processing this many activities |
| `--force` | flag | off | Re-fetch activities that were already fetched |

### `fetch_achievements` — rate limiting

Handled by the Strava client from Strava's usage headers — see
[Rate limits](strava-api.md#rate-limits).

### `fetch_achievements` — notes

- Safe to re-run — uses `update_or_create` on `(activity, segment_name, achievement_type)`.
- Activities with `achievement_count = 0` are skipped without an API call.
- Each fetched activity is stamped `achievements_checked_at`, even if it has no segment
  achievements (Strava's count also includes best-effort PRs), so it is not fetched again.
- Failed requests leave the activity unstamped for the next run and make the command exit
  with an error. An invalid Strava token stops it immediately.
- Achievements appear on the activity detail page at `/activities/<strava_id>/`.

---

## backfill_best_efforts

File: [`core/management/commands/backfill_best_efforts.py`](../core/management/commands/backfill_best_efforts.py)

Computes sliding-window personal best times for activities that have not been processed yet
(new, or whose last fetch failed) and stores them as `BestEffort` rows. Fetches `GET /activities/{id}/streams?keys=distance,time` for each
activity, then runs the two-pointer algorithm in `core/best_effort.py`.

See [`best-effort.md`](best-effort.md) for the full algorithm documentation.

### `backfill_best_efforts` — usage

```bash
# Process activities not processed yet
python manage.py backfill_best_efforts

# Test with a small batch first
python manage.py backfill_best_efforts --limit 10

# Recompute every eligible activity
python manage.py backfill_best_efforts --force
```

### `backfill_best_efforts` — arguments

| Argument | Type | Default | Description |
| --- | --- | --- | --- |
| `--limit` | int | none | Stop after processing this many activities |
| `--force` | flag | off | Recompute activities that were already processed |

### `backfill_best_efforts` — rate limiting

Handled by the Strava client from Strava's usage headers — see
[Rate limits](strava-api.md#rate-limits).

### `backfill_best_efforts` — notes

- Only GPS runs (`Run` / `TrailRun`, not on a treadmill) of at least 1 K are processed;
  everything else is skipped without an API call. See [`best-effort.md`](best-effort.md).
- Safe to re-run — uses `update_or_create` on `(activity, target_distance_m)`.
- Each processed activity is stamped `best_efforts_checked_at`, even if it had no GPS data,
  so it is not fetched again.
- Failed requests leave the activity unstamped for the next run and make the command exit
  with an error. An invalid Strava token stops it immediately.
- `manage.py sync` runs this after `sync_activities`, so stream fetches that failed during
  a sync are retried automatically.
