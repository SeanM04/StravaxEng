# Best-Effort Algorithm

File: [`core/best_effort.py`](../core/best_effort.py)

StravaXEng detects your personal best time for each standard distance bucket by running a
sliding-window scan over the raw GPS stream of every run — the same technique Strava
uses for its own "Best Efforts" feature.

## Which activities qualify

Only GPS runs: `sport_type` in `RUN_SPORT_TYPES` (`Run`, `TrailRun`), not on a treadmill
(`trainer = False`), and at least 1 km long. Walks and rides would otherwise win every
"fastest 5K"; treadmill and virtual runs take their distance from the treadmill or a foot pod
rather than GPS, so their bests are unreliable. The rule lives in one place —
`qualifies_for_best_efforts()` / `activities_for_best_efforts()` — and is used by
`sync_activities`, `backfill_best_efforts` and the Records page.

## Target distances

| Label | Metres |
| --- | --- |
| 1K | 1 000 |
| 3K | 3 000 |
| 5K | 5 000 |
| 10K | 10 000 |
| 12K | 12 000 |
| 15K | 15 000 |
| 18K | 18 000 |
| Half Marathon | 21 097 |
| 23K | 23 000 |

---

## Data source: GPS streams

Strava exposes cumulative arrays for every activity via:

```
GET /activities/{id}/streams?keys=distance,time&key_by_type=true
```

The response contains two parallel arrays:

```json
{
  "distance": { "data": [0.0, 10.5, 21.2, 31.8, ...] },
  "time":     { "data": [0, 1, 2, 3, ...] }
}
```

- **`distance`** — cumulative metres from the start of the activity. Monotonically
  non-decreasing (GPS drift can cause tiny dips in raw data, but Strava smooths
  these out before returning the stream).
- **`time`** — cumulative seconds from the start. Also monotonically non-decreasing.
- Both arrays are the same length; element `k` represents the state at GPS sample `k`.

`strava_client.fetch_activity_streams(strava_id)` wraps this call and returns a dict:

```python
{"distance": [...], "time": [...]}
```

---

## Algorithm — two-pointer sliding window

```python
find_best_effort(distances, times, target_m) → float | None
```

### Goal

Find the minimum value of `times[j] - times[i]` over all index pairs `(i, j)` where
`distances[j] - distances[i] >= target_m`. This is the shortest elapsed time in which
the athlete covered at least `target_m` metres continuously in a single activity.

### Why two pointers and not a double loop?

A naïve O(n²) approach would try every `(i, j)` pair. Two pointers achieve O(n) by
exploiting one key invariant of monotone arrays:

> **The minimum valid `j` for `i+1` is always ≥ the minimum valid `j` for `i`.**

Proof: if `distances[j] - distances[i] >= target_m` and `distances[i+1] >= distances[i]`,
then `distances[j] - distances[i+1] ≤ distances[j] - distances[i]`. So the window
shrinks as the left pointer advances, meaning `j` may need to move _further right_
but never left. `j` advances at most `n` times across the entire loop.

### Step-by-step walkthrough

```
Target: 500 m

distances = [0,  100, 200, 300, 400, 500, 600, 700]   (metres, cumulative)
times     = [0,   20,  40,  60,  70,  80,  85,  90]   (seconds, cumulative)

i=0  advance j until distances[j] − 0 ≥ 500
     → j=5  (500 m)    elapsed = 80 − 0  = 80 s    best = 80

i=1  distances[5] − 100 = 400 < 500, advance j
     → j=6  (600 m)    elapsed = 85 − 20 = 65 s    best = 65

i=2  distances[6] − 200 = 400 < 500, advance j
     → j=7  (700 m)    elapsed = 90 − 40 = 50 s ★  best = 50

i=3  distances[7] − 300 = 400 < 500, advance j
     → j=8  = n        no valid right endpoint → break

Result: 50 s  (the 500 m segment between index 2 and index 7)
```

The early `break` is correct: once `j` reaches the end of the array without covering
`target_m`, advancing `i` can only _reduce_ `distances[n-1] - distances[i]`, making it
even harder to satisfy the constraint. No future `i` can produce a valid window.

### Complexity

| Property | Value |
| --- | --- |
| Time | O(n) — `i` advances `n` times; `j` advances at most `n` times total across all `i` |
| Space | O(1) extra — two pointers and one running minimum |

---

## Python implementation

```python
def find_best_effort(
    distances: list[float],
    times: list[float],
    target_m: float,
) -> float | None:
    n = len(distances)
    if n < 2 or len(times) != n or target_m <= 0:
        return None

    best: float | None = None
    j = 0

    for i in range(n):
        if j <= i:
            j = i + 1                             # j must always lead i

        while j < n and distances[j] - distances[i] < target_m:
            j += 1                                # expand window right

        if j >= n:
            break                                 # no valid window from here onward

        elapsed = times[j] - times[i]
        if best is None or elapsed < best:
            best = elapsed                        # new candidate best

    return best
```

Source: [`core/best_effort.py`](../core/best_effort.py)

---

## BestEffort model

Each computed result is stored as a `BestEffort` row linked to the `Activity` it came from.

| Field | Type | Description |
| --- | --- | --- |
| `activity` | FK → Activity | The activity in which the best was achieved |
| `target_distance_m` | IntegerField | Target in metres (e.g. `5000`) |
| `elapsed_time_s` | FloatField | Fastest elapsed time in seconds |
| `achieved_at` | DateField | Calendar date of the activity |
| `pace_per_km` | FloatField | Decimal minutes per kilometre |

The pair `(activity, target_distance_m)` is unique — running the computation again is safe
and will update existing rows rather than create duplicates.

### Computed properties

```python
effort.time_display   # → "21:34" or "1:05:12" (H:MM:SS for efforts over an hour)
effort.pace_display   # → "4:19"  (M:SS per km)
```

---

## How the Records page uses this

The Records & Streaks page queries the single best `BestEffort` per target distance
across all runs, links back to the source activity, and displays time and pace:

```python
best = (
    BestEffort.objects
    .filter(target_distance_m=5000, activity__sport_type__in=RUN_SPORT_TYPES)
    .select_related("activity")
    .order_by("elapsed_time_s")
    .first()
)
```

If no data exists for a target, the table row is omitted and an empty-state prompt
tells the user to run `backfill_best_efforts`.

---

## Running the backfill

```bash
# Process runs not processed yet (one stream API call per run)
python manage.py backfill_best_efforts

# Test with a small batch first
python manage.py backfill_best_efforts --limit 10

# Recompute every qualifying run
python manage.py backfill_best_efforts --force
```

Activities that do not qualify (see above) are skipped without an API call. Rate limiting is handled by the Strava client
(see [Rate limits](strava-api.md#rate-limits)).

Going forward, `sync_activities` automatically computes best efforts for every
newly-synced run, and `manage.py sync` retries any whose stream fetch failed.

---

## Test coverage

Unit tests for `find_best_effort` live in `core/tests.py` and require no API calls
or database access — they pass purely synthetic `distances` and `times` arrays:

| Test | What it covers |
| --- | --- |
| `test_exact_match` | A single window that exactly spans the target |
| `test_no_segment_meets_target` | Activity shorter than the target → `None` |
| `test_fastest_window_not_first` | Optimal window is mid-activity, not at index 0 |
| `test_multiple_1k_windows_returns_minimum` | Several qualifying windows; best is last |
| `test_empty_arrays_returns_none` | Edge: empty input |
| `test_single_element_returns_none` | Edge: only one GPS point |
| `test_zero_target_returns_none` | Edge: `target_m = 0` |
| `test_mismatched_lengths_returns_none` | Edge: unequal array lengths |
