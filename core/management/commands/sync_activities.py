"""
ETL pipeline: Extract activities from Strava → Transform → Load into PostgreSQL.

Modes
-----
Incremental (default): fetches only activities recorded after the last
  successful sync, keeping API usage low.
Full (--full flag):    ignores history and fetches everything from Strava.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models import Activity, SyncLog
from core import strava_client


def _transform(item: dict) -> dict:
    """Map a Strava REST API activity dict to Activity model field values."""
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
        "trainer":              item.get("trainer", False),
        "commute":              item.get("commute", False),
        "raw_data":             item,
    }


class Command(BaseCommand):
    help = "ETL: pull Strava activities and upsert into PostgreSQL."

    def add_arguments(self, parser):
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

    def handle(self, *args, **options):
        full     = options["full"]
        per_page = min(options["per_page"], 200)

        # ── Determine incremental cutoff ──────────────────────────────────────
        after_ts = None
        if not full:
            last_ok = (
                SyncLog.objects
                .filter(status=SyncLog.Status.SUCCESS)
                .order_by("-finished_at")
                .first()
            )
            if last_ok and last_ok.finished_at:
                after_ts = int(last_ok.finished_at.timestamp())
                self.stdout.write(
                    f"Incremental sync — fetching activities after "
                    f"{last_ok.finished_at:%Y-%m-%d %H:%M:%S UTC}"
                )
            else:
                self.stdout.write("No previous sync found — performing full sync.")
        else:
            self.stdout.write("Full sync requested.")

        log = SyncLog.objects.create(incremental=not full)

        # ── EXTRACT → TRANSFORM → LOAD ────────────────────────────────────────
        page = 1
        created = updated = 0

        try:
            while True:
                # Extract
                items = strava_client.fetch_activities(
                    after=after_ts,
                    per_page=per_page,
                    page=page,
                )

                if not items:
                    break

                # Transform + Load (upsert)
                for item in items:
                    _, is_new = Activity.objects.update_or_create(
                        strava_id=item["id"],
                        defaults=_transform(item),
                    )
                    if is_new:
                        created += 1
                    else:
                        updated += 1

                self.stdout.write(f"  Page {page}: {len(items)} activities processed")

                if len(items) < per_page:
                    break  # last page
                page += 1

            log.status = SyncLog.Status.SUCCESS

        except Exception as exc:
            log.status = SyncLog.Status.FAILED
            log.error_message = str(exc)
            self.stderr.write(self.style.ERROR(f"Sync failed: {exc}"))

        finally:
            log.finished_at        = timezone.now()
            log.activities_created = created
            log.activities_updated = updated
            log.pages_fetched      = page
            log.save()

        if log.status == SyncLog.Status.SUCCESS:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Done: {created} created, {updated} updated "
                    f"across {page} page(s)."
                )
            )
