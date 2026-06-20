"""
Management command: fetch and store segment achievement details.

Calls ``GET /activities/{id}`` for every Activity that has
``achievement_count > 0`` and parses ``segment_efforts[].achievements``
to populate the ``Achievement`` model.

Rate-aware: sleeps 1 second between requests and pauses 15 minutes after
every 80 requests to stay within Strava's 100-request/15-minute limit.
"""
import time

from django.core.management.base import BaseCommand

from core import strava_client
from core.models import Achievement, Activity

_RATE_BATCH = 80    # requests before we pause
_RATE_SLEEP = 900   # 15 minutes in seconds


class Command(BaseCommand):
    """Fetch achievement details from Strava and store them in the Achievement table.

    For each eligible activity, calls the Strava detail endpoint and upserts
    achievements from ``segment_efforts[].achievements`` into the
    ``Achievement`` model.  Skips activities that already have Achievement
    rows unless ``--force`` is passed.
    """

    help = "Fetch segment achievement details for activities with achievement_count > 0"

    def add_arguments(self, parser):
        """Attach CLI arguments to the command parser.

        Args:
            parser: ``ArgumentParser`` instance provided by Django.
        """
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Max number of activities to process (default: all eligible).",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            default=False,
            help="Re-fetch activities that already have Achievement rows stored.",
        )

    def handle(self, *_args, **options):
        """Run the fetch-achievements ETL.

        Args:
            options (dict): Parsed CLI options — ``limit`` (int | None) and
                ``force`` (bool).
        """
        limit = options["limit"]
        force = options["force"]

        qs = Activity.objects.filter(achievement_count__gt=0).order_by("-start_date")

        if not force:
            done_ids = Achievement.objects.values_list(
                "activity_id", flat=True
            ).distinct()
            qs = qs.exclude(pk__in=done_ids)

        if limit:
            qs = qs[:limit]

        total = qs.count()
        if total == 0:
            self.stdout.write("No activities need achievement fetching.")
            return

        self.stdout.write(f"Fetching achievements for {total} activit{'y' if total == 1 else 'ies'}…")
        created = 0
        updated = 0
        failed  = 0

        for i, activity in enumerate(qs, 1):
            # Pause every 80 requests to respect Strava's 100/15-min rate limit.
            if i > 1 and (i - 1) % _RATE_BATCH == 0:
                self.stdout.write("  Rate-limit pause — waiting 15 min before continuing…")
                time.sleep(_RATE_SLEEP)

            try:
                detail = strava_client.strava_get(f"activities/{activity.strava_id}")
            except Exception as exc:  # noqa: BLE001
                self.stderr.write(f"  Failed {activity.strava_id}: {exc}")
                failed += 1
                time.sleep(1)
                continue

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

            if i % 10 == 0 or i == total:
                self.stdout.write(f"  [{i}/{total}] processed")

            time.sleep(1)

        self.stdout.write(
            self.style.SUCCESS(
                f"Done — {created} created, {updated} updated, {failed} failed."
            )
        )
