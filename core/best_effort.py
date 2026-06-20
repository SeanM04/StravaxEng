"""
Sliding-window best-effort detection over GPS activity streams.

Algorithm overview
------------------
Given parallel arrays of **cumulative** distance (metres) and elapsed time
(seconds) — as returned by Strava's ``/activities/{id}/streams`` endpoint —
this module finds the shortest time in which the athlete covered at least
*target_m* metres continuously within a single activity.

Two-pointer O(n) approach
~~~~~~~~~~~~~~~~~~~~~~~~~
Maintain a left pointer ``i`` and a right pointer ``j``:

1. For each ``i``, advance ``j`` (never backwards) until
   ``distances[j] - distances[i] >= target_m``.
2. Record ``elapsed = times[j] - times[i]`` as a candidate best time.
3. Increment ``i`` by 1 and repeat.
4. Because the distance array is monotonically non-decreasing, the minimum
   valid ``j`` for ``i+1`` is always ≥ the minimum valid ``j`` for ``i``.
   Consequently ``j`` never has to regress, and the total work across all
   ``i`` iterations is O(n).

If ``j`` reaches the end of the array without finding a valid window, no
valid window can exist for any future ``i`` either (advancing ``i`` only
shrinks the available right-side coverage), so we break early.

Complexity: O(n) time, O(1) extra space.
"""

TARGET_DISTANCES_M: list[int] = [
    1_000,
    3_000,
    5_000,
    10_000,
    12_000,
    15_000,
    18_000,
    21_097,   # half marathon
    23_000,
]

DISTANCE_LABELS: dict[int, str] = {
    1_000:  "1K",
    3_000:  "3K",
    5_000:  "5K",
    10_000: "10K",
    12_000: "12K",
    15_000: "15K",
    18_000: "18K",
    21_097: "Half Marathon",
    23_000: "23K",
}


def find_best_effort(
    distances: list[float],
    times: list[float],
    target_m: float,
) -> float | None:
    """Return the minimum elapsed time (seconds) to cover *target_m* metres.

    Uses a two-pointer sliding window over the cumulative distance/time arrays
    produced by Strava's stream endpoint.  Both arrays must be parallel (same
    length) and monotonically non-decreasing.

    Args:
        distances: Cumulative distance array in metres, e.g. ``[0.0, 10.5, …]``.
            Must be monotonically non-decreasing.
        times: Cumulative elapsed-time array in seconds (same length as
            *distances*).  Must be monotonically non-decreasing.
        target_m: Target distance in metres (must be > 0).

    Returns:
        Minimum elapsed time in seconds for a contiguous window that covers
        at least *target_m* metres, or ``None`` if no such window exists in
        the data (e.g. the activity is shorter than the target).

    Complexity:
        O(n) time where ``n = len(distances)``, O(1) extra space.
    """
    n = len(distances)
    if n < 2 or len(times) != n or target_m <= 0:
        return None

    best: float | None = None
    j = 0

    for i in range(n):
        # j must be strictly ahead of i to form a non-trivial window.
        if j <= i:
            j = i + 1

        # Advance j until the window [i..j] spans at least target_m.
        while j < n and distances[j] - distances[i] < target_m:
            j += 1

        if j >= n:
            # distances[n-1] - distances[i] < target_m; since distances is
            # non-decreasing, this will be true for all future i too.
            break

        elapsed = times[j] - times[i]
        if best is None or elapsed < best:
            best = elapsed

    return best


def compute_and_save(activity) -> tuple[int, int]:
    """Fetch GPS streams for *activity* and upsert BestEffort rows.

    Calls the Strava stream endpoint, runs ``find_best_effort`` for each
    target distance the activity is long enough to cover, and persists
    results via ``BestEffort.objects.update_or_create``.

    Uses late imports to avoid circular-import issues at module load time.

    Args:
        activity: A saved ``Activity`` model instance.

    Returns:
        ``(created, updated)`` counts.  Returns ``(0, 0)`` when the activity
        is shorter than all targets or the stream fetch fails.
    """
    from core import strava_client
    from core.models import BestEffort

    if activity.distance_meters < min(TARGET_DISTANCES_M):
        return 0, 0

    try:
        streams = strava_client.fetch_activity_streams(activity.strava_id)
    except Exception:  # noqa: BLE001 — caller decides whether to log / retry
        return 0, 0

    dist = streams["distance"]
    ts   = streams["time"]
    created = updated = 0

    for target_m in TARGET_DISTANCES_M:
        # Skip targets the activity clearly cannot cover (5 % margin for GPS drift).
        if activity.distance_meters < target_m * 0.95:
            continue

        best_s = find_best_effort(dist, ts, target_m)
        if best_s is None:
            continue

        pace = (best_s / 60.0) / (target_m / 1000.0)
        _, is_new = BestEffort.objects.update_or_create(
            activity=activity,
            target_distance_m=target_m,
            defaults={
                "elapsed_time_s": best_s,
                "achieved_at":    activity.start_date.date(),
                "pace_per_km":    pace,
            },
        )
        if is_new:
            created += 1
        else:
            updated += 1

    return created, updated
