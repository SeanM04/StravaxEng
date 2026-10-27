"""
Segment achievements: fetch them from Strava and store them as ``Achievement`` rows.

Strava's ``achievement_count`` also counts best-effort PRs (e.g. a 5K PR), which
are not segment achievements, so a fetched activity can legitimately end up
with no ``Achievement`` rows.  ``Activity.achievements_checked_at`` records that
an activity was fetched either way, so it is not fetched again on every sync.
"""
from django.utils import timezone

from core import strava_client
from core.models import Achievement, Activity


def activities_for_achievements(include_checked: bool = False):
    """Return activities that earned achievements, newest first.

    Args:
        include_checked: If ``False`` (the default), only activities not
            fetched yet, i.e. new ones and ones whose last fetch failed.

    Returns:
        QuerySet[Activity]: The matching activities.
    """
    qs = Activity.objects.filter(achievement_count__gt=0)
    if not include_checked:
        qs = qs.filter(achievements_checked_at__isnull=True)
    return qs.order_by("-start_date")


def fetch_and_save(activity) -> tuple[int, int]:
    """Fetch *activity*'s detail from Strava and upsert its segment achievements.

    Stamps ``activity.achievements_checked_at`` once Strava has answered.

    Args:
        activity: A saved ``Activity`` model instance.

    Returns:
        ``(created, updated)`` ``Achievement`` row counts.

    Raises:
        requests.RequestException: If the request fails.  The activity is left
            unstamped so the next sync retries it.
        StravaAuthError: If no valid Strava token is available.
    """
    detail = strava_client.strava_get(f"activities/{activity.strava_id}")

    created = updated = 0
    for effort in detail.get("segment_efforts", []):
        seg_name = effort.get("name") or "Unknown Segment"
        for ach in effort.get("achievements", []):
            _, is_new = Achievement.objects.update_or_create(
                activity=activity,
                segment_name=seg_name,
                achievement_type=ach.get("type", Achievement.Type.PR),
                defaults={"rank": ach.get("rank", 1)},
            )
            if is_new:
                created += 1
            else:
                updated += 1

    activity.achievements_checked_at = timezone.now()
    activity.save(update_fields=["achievements_checked_at"])
    return created, updated
