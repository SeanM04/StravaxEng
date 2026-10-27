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
from django.utils import timezone

from core import strava_client
from core.models import Activity, BestEffort

# Best efforts are computed only for GPS runs.  Walks and rides would otherwise
# win every "fastest 5K"; treadmill/virtual runs take their distance from the
# treadmill or a foot pod rather than GPS, so their bests are unreliable.
RUN_SPORT_TYPES: tuple[str, ...] = ("Run", "TrailRun")

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


def qualifies_for_best_efforts(activity) -> bool:
    """Return True if *activity* is a GPS run long enough for the shortest target.

    Mirrors the filter in ``activities_for_best_efforts``.

    Args:
        activity: An ``Activity`` instance.

    Returns:
        Whether best efforts should be computed for it.
    """
    return (
        activity.sport_type in RUN_SPORT_TYPES
        and not activity.trainer
        and activity.distance_meters >= min(TARGET_DISTANCES_M)
    )


def activities_for_best_efforts(include_checked: bool = False):
    """Return GPS runs long enough to hold a best effort, newest first.

    Args:
        include_checked: If ``False`` (the default), only activities whose
            streams have not been processed yet, i.e. new ones and ones whose
            last fetch failed.

    Returns:
        QuerySet[Activity]: The matching activities.
    """
    qs = Activity.objects.filter(
        sport_type__in=RUN_SPORT_TYPES,
        trainer=False,
        distance_meters__gte=min(TARGET_DISTANCES_M),
    )
    if not include_checked:
        qs = qs.filter(best_efforts_checked_at__isnull=True)
    return qs.order_by("-start_date")


def compute_and_save(activity) -> tuple[int, int]:
    """Fetch GPS streams for *activity* and upsert BestEffort rows.

    Calls the Strava stream endpoint, runs ``find_best_effort`` for each
    target distance the activity is long enough to cover, persists results
    via ``BestEffort.objects.update_or_create``, and stamps
    ``activity.best_efforts_checked_at`` so it is not fetched again.  An
    activity with no GPS data is still stamped: there is nothing to retry.

    Args:
        activity: A saved ``Activity`` model instance.

    Returns:
        ``(created, updated)`` counts.  ``(0, 0)`` when the activity is not a
        GPS run or is shorter than every target (no API call is made, nothing
        is stamped).

    Raises:
        requests.RequestException: If the stream request fails.  The activity
            is left unstamped so the next sync retries it.
        StravaAuthError: If no valid Strava token is available.
    """
    if not qualifies_for_best_efforts(activity):
        return 0, 0

    streams = strava_client.fetch_activity_streams(activity.strava_id)

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
                "achieved_at":    timezone.localdate(activity.start_date),
                "pace_per_km":    pace,
            },
        )
        if is_new:
            created += 1
        else:
            updated += 1

    activity.best_efforts_checked_at = timezone.now()
    activity.save(update_fields=["best_efforts_checked_at"])
    return created, updated
