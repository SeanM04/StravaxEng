"""
ETL pipeline: Extract activities from Strava → Transform → Load into PostgreSQL.

Modes
-----
Incremental (default): fetches activities that started up to
  ``INCREMENTAL_OVERLAP`` before the newest stored one, so late uploads are
  caught while keeping API usage low (usually one page).
Full (--full flag):    ignores history and fetches everything from Strava.

Outcome (recorded in ``SyncLog``)
---------------------------------
success: activities synced and best efforts computed for every new activity.
partial: activities synced, but some best efforts could not be computed; the
  ``backfill_best_efforts`` stage of ``manage.py sync`` retries them.
failed:  activities could not be fetched; the command exits with an error.
"""
import traceback
from datetime import timedelta

import requests
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from core import strava_client
from core.best_effort import compute_and_save, qualifies_for_best_efforts
from core.models import Activity, SyncLog
from core.sync_lock import STALE_AFTER_S

# How far before the newest stored activity an incremental sync looks, to catch
# activities uploaded late (e.g. a watch that syncs to Strava days afterwards).
INCREMENTAL_OVERLAP = timedelta(days=7)


def _close_interrupted_runs() -> None:
    """Mark runs stuck in ``running`` for longer than a sync can take as failed.

    A run stays ``running`` when its process is killed (PC shut down, window
    closed) before it can record an outcome.
    """
    SyncLog.objects.filter(
        status=SyncLog.Status.RUNNING,
        started_at__lt=timezone.now() - timedelta(seconds=STALE_AFTER_S),
    ).update(
        status=SyncLog.Status.FAILED,
        finished_at=timezone.now(),
        error_message="Interrupted: the sync process stopped before finishing.",
    )


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
        # Parsed here so freshly-created instances hold a datetime, not the raw
        # ISO string (compute_and_save calls start_date.date() before any reload).
        "start_date":           parse_datetime(item["start_date"]),
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

        Strava's ``?after=`` filters on when an activity *started*, so the
        cutoff is the start of the newest stored activity minus
        ``INCREMENTAL_OVERLAP``.  Using the last sync's finish time instead
        would skip any activity uploaded after a sync but started before it
        (a watch that syncs late).  The overlap re-fetches recent activities,
        which is harmless because rows are upserted.

        Args:
            full: If ``True``, return ``None`` (no cutoff).

        Returns:
            int | None: Unix timestamp to pass as ``?after=`` to the Strava
            API, or ``None`` to fetch all pages (full mode, or nothing stored yet).
        """
        if full:
            self.stdout.write("Full sync requested.")
            return None
        newest = (
            Activity.objects.order_by("-start_date").values_list("start_date", flat=True).first()
        )
        if newest is None:
            self.stdout.write("No activities stored yet — performing full sync.")
            return None
        cutoff = newest - INCREMENTAL_OVERLAP
        self.stdout.write(
            "Incremental sync — fetching activities started after "
            f"{timezone.localtime(cutoff):%Y-%m-%d %H:%M %Z} "
            f"({INCREMENTAL_OVERLAP.days} days before the newest stored activity)"
        )
        return int(cutoff.timestamp())

    def _run_etl(
        self, after_ts: int | None, per_page: int
    ) -> tuple[int, int, int, list[Activity]]:
        """Paginate through Strava activities and upsert each one.

        Args:
            after_ts: Unix timestamp cutoff passed as ``?after=`` to the API,
                or ``None`` for a full sync.
            per_page: Number of activities to request per API page (max 200).

        Returns:
            tuple: ``(created, updated, pages_fetched, new_activities)``.

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

    def _compute_best_efforts(self, activities: list) -> list[str]:
        """Fetch GPS streams and compute best efforts for newly-synced activities.

        Only GPS runs of at least 1 km are processed (rate limiting happens in
        ``core.strava_client``).  Activities whose streams cannot be fetched
        stay unchecked, so ``backfill_best_efforts`` retries them.

        Args:
            activities: List of ``Activity`` instances created during this sync.

        Returns:
            list[str]: One message per activity whose streams could not be fetched.
        """
        eligible = [a for a in activities if qualifies_for_best_efforts(a)]
        problems: list[str] = []
        if not eligible:
            return problems
        n = len(eligible)
        self.stdout.write(
            f"  Computing best efforts for {n} new activit{'y' if n == 1 else 'ies'}…"
        )
        for activity in eligible:
            try:
                c, u = compute_and_save(activity)
            except requests.RequestException as exc:
                problems.append(f"best efforts for activity {activity.strava_id}: {exc}")
                self.stderr.write(f"    Failed {activity.strava_id}: {exc}")
            else:
                if c + u > 0:
                    self.stdout.write(
                        f"    {activity.name[:40]}: {c} new, {u} updated best efforts"
                    )
        return problems

    def handle(self, *_args, **options):
        """Execute the ETL pipeline and write results to the SyncLog.

        Args:
            *_args: Unused positional arguments (required by Django's interface).
            **options: Parsed CLI options.  Keys used: ``full`` (bool),
                ``per_page`` (int).

        Raises:
            CommandError: If activities could not be fetched.  The failure is
                recorded in the ``SyncLog`` first.
        """
        _close_interrupted_runs()
        full     = options["full"]
        per_page = min(options["per_page"], 200)
        after_ts = self._get_cutoff_timestamp(full)
        log      = SyncLog.objects.create(incremental=not full)

        created = updated = pages = 0
        try:
            created, updated, pages, new_activities = self._run_etl(after_ts, per_page)
        # All errors must be recorded in SyncLog.
        except Exception as exc:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            log.status = SyncLog.Status.FAILED
            log.error_message = str(exc)
        else:
            # Activities are saved by now, so a best-effort problem only makes the run partial.
            try:
                problems = self._compute_best_efforts(new_activities)
            # Unexpected errors too: record them rather than lose them.
            except Exception as exc:  # noqa: BLE001  # pylint: disable=broad-exception-caught
                self.stderr.write(traceback.format_exc())
                problems = [f"best efforts stopped: {exc}"]
            log.status = SyncLog.Status.PARTIAL if problems else SyncLog.Status.SUCCESS
            if problems:
                log.error_message = (
                    f"{len(problems)} problem(s), retried next sync. First: {problems[0]}"
                )
        finally:
            log.finished_at        = timezone.now()
            log.activities_created = created
            log.activities_updated = updated
            log.pages_fetched      = pages
            log.save()

        summary = f"{created} created, {updated} updated across {pages} page(s)."
        if log.status == SyncLog.Status.FAILED:
            raise CommandError(f"Sync failed: {log.error_message}")
        if log.status == SyncLog.Status.PARTIAL:
            self.stdout.write(self.style.WARNING(f"Done with problems: {summary}"))
            self.stdout.write(self.style.WARNING(f"  {log.error_message}"))
        else:
            self.stdout.write(self.style.SUCCESS(f"Done: {summary}"))
