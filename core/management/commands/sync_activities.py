"""
ETL pipeline: Extract activities from Strava → Transform → Load into PostgreSQL.

Modes
-----
Incremental (default): fetches only activities recorded after the last
  successful sync, keeping API usage low.
Full (--full flag):    ignores history and fetches everything from Strava.
"""
import time

from django.core.management.base import BaseCommand
from django.utils import timezone

from core import strava_client
from core.best_effort import compute_and_save
from core.models import Activity, SyncLog


def _transform(item: dict) -> dict:
    """Map a Strava REST API activity dict to Activity model field values.

    Args:
        item: A single activity dict as returned by ``GET /athlete/activities``.

    Returns:
        dict: Keyword arguments suitable for ``Activity.objects.update_or_create``.
    """
    return {
        "name":                 item.get("name", ""),
        "sport_type":           item.get("sport_type", "Other"),
        "start_date":           item.get("start_date"),
        "distance_meters":      item.get("distance", 0),
        "moving_time_seconds":  item.get("moving_time", 0),
        "elapsed_time_seconds": item.get("elapsed_time", 0),
        "total_elevation_gain": item.get("total_elevation_gain", 0),
        "average_speed":        item.get("average_speed", 0),
        "max_speed":            item.get("max_speed", 0),
        "average_heartrate":    item.get("average_heartrate"),
        "max_heartrate":        item.get("max_heartrate"),
        "average_cadence":      item.get("average_cadence"),
        "kudos_count":          item.get("kudos_count", 0),
        "calories":             item.get("calories"),
        "achievement_count":    item.get("achievement_count", 0),
        "trainer":              item.get("trainer", False),
        "commute":              item.get("commute", False),
        "raw_data":             item,
    }


class Command(BaseCommand):
    """Management command that runs the Strava → PostgreSQL ETL pipeline.

    Creates a ``SyncLog`` entry at the start of each run and updates it with
    the final status and row counts on completion, regardless of success or
    failure.
    """

    help = "ETL: pull Strava activities and upsert into PostgreSQL."

    def add_arguments(self, parser):
        """Register ``--full`` and ``--per-page`` CLI arguments.

        Args:
            parser: The ``argparse.ArgumentParser`` provided by Django.
        """
        parser.add_argument(
            "--full",
            action="store_true",
            help="Ignore the last-sync timestamp and fetch all activities.",
        )
        parser.add_argument(
            "--per-page",
            type=int,
            default=100,
            help="Activities per API page (max 200, default 100).",
        )

    def _get_cutoff_timestamp(self, full: bool) -> int | None:
        """Determine the Unix timestamp cutoff for an incremental sync.

        In full mode, always returns ``None`` (no cutoff).  In incremental
        mode, returns the ``finished_at`` timestamp of the last successful
        sync, or ``None`` if no successful sync has been recorded yet (which
        causes the caller to fall back to a full sync automatically).

        Args:
            full: If ``True``, skip the history lookup and return ``None``.

        Returns:
            int | None: Unix timestamp to pass as ``?after=`` to the Strava
            API, or ``None`` to fetch all pages.
        """
        if full:
            self.stdout.write("Full sync requested.")
            return None
        last_ok = (
            SyncLog.objects
            .filter(status=SyncLog.Status.SUCCESS)
            .order_by("-finished_at")
            .first()
        )
        if last_ok and last_ok.finished_at:
            self.stdout.write(
                f"Incremental sync — fetching activities after "
                f"{last_ok.finished_at:%Y-%m-%d %H:%M:%S UTC}"
            )
            return int(last_ok.finished_at.timestamp())
        self.stdout.write("No previous sync found — performing full sync.")
        return None

    def _run_etl(self, after_ts: int | None, per_page: int) -> tuple[int, int, int]:
        """Paginate through Strava activities and upsert each one.

        Args:
            after_ts: Unix timestamp cutoff passed as ``?after=`` to the API,
                or ``None`` for a full sync.
            per_page: Number of activities to request per API page (max 200).

        Returns:
            tuple[int, int, int]: ``(created, updated, pages_fetched)`` counts.

        Raises:
            Exception: Any exception from ``strava_client`` propagates to the
                caller so it can be recorded in the ``SyncLog``.
        """
        page = 1
        created = updated = 0
        new_activities: list[Activity] = []
        while True:
            items = strava_client.fetch_activities(
                after=after_ts, per_page=per_page, page=page
            )
            if not items:
                break
            for item in items:
                activity, is_new = Activity.objects.update_or_create(
                    strava_id=item["id"],
                    defaults=_transform(item),
                )
                if is_new:
                    created += 1
                    new_activities.append(activity)
                else:
                    updated += 1
            self.stdout.write(f"  Page {page}: {len(items)} activities processed")
            if len(items) < per_page:
                break  # last page
            page += 1
        return created, updated, page, new_activities

    def _compute_best_efforts(self, activities: list) -> None:
        """Fetch GPS streams and compute best efforts for newly-synced activities.

        Rate-aware: sleeps 1 second between stream API calls and pauses 15
        minutes after every 80 calls to respect Strava's rate limit.
        Activities shorter than 1 km are silently skipped.

        Args:
            activities: List of ``Activity`` instances created during this sync.
        """
        from core.best_effort import TARGET_DISTANCES_M as _TARGETS
        eligible = [a for a in activities if a.distance_meters >= min(_TARGETS)]
        if not eligible:
            return
        n = len(eligible)
        self.stdout.write(
            f"  Computing best efforts for {n} new activit{'y' if n == 1 else 'ies'}…"
        )
        for i, activity in enumerate(eligible, 1):
            if i > 1 and (i - 1) % 80 == 0:
                self.stdout.write("    Rate-limit pause — waiting 15 min…")
                time.sleep(900)
            c, u = compute_and_save(activity)
            if c + u > 0:
                self.stdout.write(
                    f"    {activity.name[:40]}: {c} new, {u} updated best efforts"
                )
            time.sleep(1)

    def handle(self, *_args, **options):
        """Execute the ETL pipeline and write results to the SyncLog.

        Args:
            *_args: Unused positional arguments (required by Django's interface).
            **options: Parsed CLI options.  Keys used: ``full`` (bool),
                ``per_page`` (int).
        """
        full     = options["full"]
        per_page = min(options["per_page"], 200)
        after_ts = self._get_cutoff_timestamp(full)
        log      = SyncLog.objects.create(incremental=not full)

        new_activities: list = []
        created = updated = pages = 0
        try:
            created, updated, pages, new_activities = self._run_etl(after_ts, per_page)
            log.status = SyncLog.Status.SUCCESS
        except Exception as exc:  # noqa: BLE001 — all errors must be recorded in SyncLog
            log.status = SyncLog.Status.FAILED
            log.error_message = str(exc)
            self.stderr.write(self.style.ERROR(f"Sync failed: {exc}"))
        finally:
            log.finished_at        = timezone.now()
            log.activities_created = created
            log.activities_updated = updated
            log.pages_fetched      = pages
            log.save()

        if log.status == SyncLog.Status.SUCCESS:
            self._compute_best_efforts(new_activities)
            self.stdout.write(self.style.SUCCESS(
                f"Done: {created} created, {updated} updated across {pages} page(s)."
            ))
