"""
Management command: backfill sliding-window best efforts for existing activities.

For each eligible activity, fetches GPS streams from Strava's
``GET /activities/{id}/streams`` endpoint and runs the two-pointer algorithm
in ``core.best_effort.find_best_effort`` to find the fastest time covering
each standard target distance.

Rate-aware: sleeps 1 second between API calls; pauses 15 minutes after every
80 requests to stay within Strava's 100-request/15-minute limit.  Activities
too short for the smallest target are skipped without an API call.
"""
import time

from django.core.management.base import BaseCommand

from core.best_effort import TARGET_DISTANCES_M, compute_and_save
from core.models import Activity, BestEffort

_RATE_BATCH = 80
_RATE_SLEEP = 900   # 15 minutes
_CALL_SLEEP = 1     # between individual requests


class Command(BaseCommand):
    """Backfill BestEffort rows for existing activities using Strava GPS streams.

    Activities shorter than the smallest target distance are skipped without
    any API call.  Activities that already have BestEffort rows are skipped
    unless ``--force`` is passed.
    """

    help = "Compute sliding-window best efforts for all activities using GPS streams"

    def add_arguments(self, parser):
        """Attach CLI arguments to the command parser.

        Args:
            parser: ``ArgumentParser`` instance provided by Django.
        """
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Maximum number of activities to process (default: all eligible).",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            default=False,
            help="Recompute activities that already have BestEffort rows.",
        )

    def handle(self, *_args, **options):
        """Execute the best-effort backfill ETL.

        Args:
            options (dict): Parsed CLI options — ``limit`` (int | None) and
                ``force`` (bool).
        """
        min_target = min(TARGET_DISTANCES_M)

        qs = Activity.objects.order_by("-start_date")
        if not options["force"]:
            done_ids = BestEffort.objects.values_list(
                "activity_id", flat=True
            ).distinct()
            qs = qs.exclude(pk__in=done_ids)
        if options["limit"]:
            qs = qs[: options["limit"]]

        total = qs.count()
        if total == 0:
            self.stdout.write("No activities need best-effort computation.")
            return

        self.stdout.write(
            f"Processing {total} activit{'y' if total == 1 else 'ies'}…"
        )
        created = updated = skipped = failed = api_calls = 0

        for idx, activity in enumerate(qs, 1):
            if activity.distance_meters < min_target:
                skipped += 1
                continue

            # Pause every 80 API calls to respect Strava's rate limit.
            if api_calls > 0 and api_calls % _RATE_BATCH == 0:
                self.stdout.write(
                    "  Rate-limit pause — waiting 15 min before continuing…"
                )
                time.sleep(_RATE_SLEEP)

            c, u = compute_and_save(activity)
            api_calls += 1

            if c == 0 and u == 0:
                failed += 1
            else:
                created += c
                updated += u

            if idx % 10 == 0 or idx == total:
                self.stdout.write(f"  [{idx}/{total}] processed")

            time.sleep(_CALL_SLEEP)

        self.stdout.write(
            self.style.SUCCESS(
                f"Done — {created} created, {updated} updated, "
                f"{skipped} skipped (too short), {failed} failed."
            )
        )
