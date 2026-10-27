"""
Management command: compute sliding-window best efforts for activities that still need them.

For each eligible activity, fetches GPS streams from Strava's
``GET /activities/{id}/streams`` endpoint and runs the two-pointer algorithm
in ``core.best_effort.find_best_effort`` to find the fastest time covering
each standard target distance.

By default only activities whose streams have not been processed yet are
handled: new ones, and ones whose last fetch failed.  ``--force`` recomputes
every eligible activity.  Rate limiting and failure handling are shared with
``fetch_achievements`` (see ``core.management.activity_stage``).
"""
from core.best_effort import activities_for_best_efforts, compute_and_save
from core.management.activity_stage import ActivityStageCommand


class Command(ActivityStageCommand):
    """Compute BestEffort rows for activities using Strava GPS streams."""

    help = "Compute sliding-window best efforts for activities that still need them"
    noun = "best efforts"

    def select(self, include_checked: bool):
        """Return activities long enough for a best effort.

        Args:
            include_checked: Whether to include already-processed activities.

        Returns:
            QuerySet[Activity]: The activities to process.
        """
        return activities_for_best_efforts(include_checked)

    def process(self, activity) -> tuple[int, int]:
        """Fetch *activity*'s GPS streams and store its best efforts.

        Args:
            activity: The ``Activity`` to process.

        Returns:
            ``(created, updated)`` ``BestEffort`` row counts.
        """
        return compute_and_save(activity)
