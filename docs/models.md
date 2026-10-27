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
| `error_message` | TextField | What went wrong if status is `failed` or `partial` |

Statuses: `success` — everything worked; `partial` — activities synced but some best efforts or
achievements could not be fetched (the next sync retries them); `failed` — activities could not
be fetched; `running` — in progress. A run still `running` after 3 hours (its process was
killed) is marked `failed` with an "Interrupted" message by the next sync.

Default ordering: `-started_at` (newest first).

The incremental sync's cutoff comes from the newest stored `Activity`, not from this table — see
[`management-commands.md`](management-commands.md#sync_activities).

---

## Activity

Stores a single Strava activity. Upserted on every sync using `strava_id` as the unique key.

| Field | Type | Description |
| --- | --- | --- |
| `strava_id` | BigIntegerField (unique) | Strava's own activity ID |
| `name` | CharField(255) | Activity name set by the athlete |
| `sport_type` | CharField(50) | Strava's sport type as sent, e.g. Run, TrailRun, Walk, Ride, Workout, WeightTraining, Yoga |
| `start_date` | DateTimeField | Start time (stored in UTC; shown in `TIME_ZONE`) |
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
| `best_efforts_checked_at` | DateTimeField (nullable) | When GPS streams were processed for best efforts; empty = still to do (new, or last fetch failed) |
| `achievements_checked_at` | DateTimeField (nullable) | When segment achievements were fetched; empty = still to do |

### Computed properties

```python
activity.distance_km          # distance_meters / 1000, rounded to 2 dp
activity.pace_per_km          # "M:SS" string, e.g. "5:32" — returns "—" if no distance
activity.moving_time_display  # "1h 23m 45s" or "23m 45s"
```

### Ordering

Default ordering is `-start_date` (newest first).

---

## Achievement

Stores a segment achievement earned during an activity. Populated by `fetch_achievements`.

| Field | Type | Description |
| --- | --- | --- |
| `activity` | FK → Activity | Activity in which the achievement was earned |
| `segment_name` | CharField(255) | Name of the Strava segment |
| `achievement_type` | CharField(20) | One of: `pr`, `kom`, `cr`, `overall` |
| `rank` | IntegerField | Finishing rank for this achievement |

The triple `(activity, segment_name, achievement_type)` is unique — safe to re-run `fetch_achievements`.

Default ordering: `achievement_type`, then `segment_name`.

### Achievement — type labels

| Value | Display |
| --- | --- |
| `pr` | Personal Record |
| `kom` | King of the Mountain |
| `cr` | Course Record |
| `overall` | Overall Leader |

---

## BestEffort

Stores the fastest time an athlete covered a standard distance bucket in a given activity.
Computed by the sliding-window algorithm in `core/best_effort.py`.
See [`best-effort.md`](best-effort.md) for the full algorithm documentation.

| Field | Type | Description |
| --- | --- | --- |
| `activity` | FK → Activity | Activity in which the best was achieved |
| `target_distance_m` | IntegerField | Target in metres (e.g. `5000`) |
| `elapsed_time_s` | FloatField | Fastest elapsed time in seconds |
| `achieved_at` | DateField | Calendar date of the activity |
| `pace_per_km` | FloatField | Decimal minutes per kilometre |

The pair `(activity, target_distance_m)` is unique — running the computation again is safe.

Default ordering: `target_distance_m` ascending.

### BestEffort — computed properties

```python
effort.time_display   # → "21:34" or "1:05:12"
effort.pace_display   # → "4:19"  (M:SS per km)
```

---

## Migrations

| Migration | Description |
| --- | --- |
| `0001_initial` | Creates the `Activity` table |
| `0002_stravatoken_synclog` | Adds `StravaToken` and `SyncLog` tables |
| `0003_activity_average_cadence` | Adds `average_cadence` field to `Activity` |
| `0004_alter_activity_sport_type_alter_activity_start_date` | Alters field options on `sport_type` and `start_date` |
| `0005_activity_achievement_count_activity_calories` | Adds `achievement_count` and `calories` fields to `Activity` |
| `0006_backfill_calories_achievement_count` | Data migration: backfills `calories` and `achievement_count` from `raw_data` |
| `0007_achievement` | Creates the `Achievement` table |
| `0008_besteffort` | Creates the `BestEffort` table |
| `0009_activity_checked_flags` | Adds `best_efforts_checked_at` / `achievements_checked_at`; marks activities that already have rows as checked |
| `0010_delete_non_run_best_efforts` | Data migration: deletes best efforts computed for walks, rides and treadmill runs |
