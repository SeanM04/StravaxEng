"""
Management command: fetch and store segment achievement details.

Calls ``GET /activities/{id}`` for every Activity that has
``achievement_count > 0`` and has not been fetched yet, and parses
``segment_efforts[].achievements`` to populate the ``Achievement`` model
(see ``core.achievements``).  Rate limiting and failure handling are shared
with ``backfill_best_efforts`` (see ``core.management.activity_stage``).
"""
from core.achievements import activities_for_achievements, fetch_and_save
from core.management.activity_stage import ActivityStageCommand


class Command(ActivityStageCommand):
    """Fetch achievement details from Strava and store them in the Achievement table."""

    help = "Fetch segment achievement details for activities with achievement_count > 0"
    noun = "achievements"

    def select(self, include_checked: bool):
        """Return activities that earned achievements.

        Args:
            include_checked: Whether to include already-fetched activities.

        Returns:
            QuerySet[Activity]: The activities to process.
        """
        return activities_for_achievements(include_checked)

    def process(self, activity) -> tuple[int, int]:
        """Fetch *activity*'s detail and store its segment achievements.

        Args:
            activity: The ``Activity`` to process.

        Returns:
            ``(created, updated)`` ``Achievement`` row counts.
        """
        return fetch_and_save(activity)
