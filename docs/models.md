# Models

File: [`core/models.py`](../core/models.py)

## StravaToken

Persists the Strava OAuth2 token pair for a single athlete. Strava uses rolling refresh
tokens — every `/oauth/token` call returns a new refresh token and invalidates the old one.
This model is the single source of truth; `strava_client.py` reads from and writes back to
this row so the rotation is never lost.

| Field | Type | Description |
| --- | --- | --- |
| `athlete_id` | BigIntegerField (unique) | Strava athlete ID |
| `access_token` | TextField | Current OAuth access token (valid ~6 hours) |
| `refresh_token` | TextField | Current rolling refresh token |
| `expires_at` | BigIntegerField | Unix timestamp when the access token expires |
| `updated_at` | DateTimeField (auto) | Timestamp of last token rotation |

### Computed property

```python
token.is_expired  # True if access token expires within the next 5 minutes
```

---

## SyncLog

Audit trail for every run of `sync_activities`. One row per ETL execution.

| Field | Type | Description |
| --- | --- | --- |
| `started_at` | DateTimeField (auto) | When the sync started |
| `finished_at` | DateTimeField (nullable) | When the sync completed (null if still running) |
| `status` | CharField | `running` / `success` / `partial` / `failed` |
| `incremental` | BooleanField | `True` = incremental sync, `False` = full re-sync |
| `pages_fetched` | IntegerField | Number of API pages retrieved |
| `activities_created` | IntegerField | New rows inserted |
| `activities_updated` | IntegerField | Existing rows updated |
| `error_message` | TextField | Error detail if status is `failed` |

Default ordering: `-started_at` (newest first).

The `finished_at` of the last `success` row is used as the `?after=` cutoff for the next
incremental sync. See [`management-commands.md`](management-commands.md).

---

## Activity

Stores a single Strava activity. Upserted on every sync using `strava_id` as the unique key.

| Field | Type | Description |
| --- | --- | --- |
| `strava_id` | BigIntegerField (unique) | Strava's own activity ID |
| `name` | CharField(255) | Activity name set by the athlete |
| `sport_type` | CharField(50) | One of: Run, Ride, Swim, Walk, Hike, Other |
| `start_date` | DateTimeField | UTC start time |
| `distance_meters` | FloatField | Total distance in metres |
| `moving_time_seconds` | IntegerField | Time in motion (seconds) |
| `elapsed_time_seconds` | IntegerField | Wall-clock duration (seconds) |
| `total_elevation_gain` | FloatField | Cumulative elevation gain (metres) |
| `average_speed` | FloatField | Average speed (m/s) |
| `max_speed` | FloatField | Max speed (m/s) |
| `average_heartrate` | FloatField (nullable) | Average HR in bpm — null if watch has no HR sync |
| `max_heartrate` | FloatField (nullable) | Max HR in bpm |
| `average_cadence` | FloatField (nullable) | Average cadence in steps/min |
| `kudos_count` | IntegerField | Number of kudos received |
| `trainer` | BooleanField | `True` if recorded on an indoor trainer |
| `commute` | BooleanField | `True` if marked as a commute |
| `raw_data` | JSONField | Full Strava API response — safe to add fields without re-migration |
| `synced_at` | DateTimeField (auto) | Timestamp of last upsert |

### Computed properties

```python
activity.distance_km          # distance_meters / 1000, rounded to 2 dp
activity.pace_per_km          # "M:SS" string, e.g. "5:32" — returns "—" if no distance
activity.moving_time_display  # "1h 23m 45s" or "23m 45s"
```

### Ordering

Default ordering is `-start_date` (newest first).

---

## Migrations

| Migration | Description |
| --- | --- |
| `0001_initial` | Creates the `Activity` table |
| `0002_stravatoken_synclog` | Adds `StravaToken` and `SyncLog` tables |
| `0003_activity_average_cadence` | Adds `average_cadence` field to `Activity` |
