"""
Seeds the Activity table from a JSON file exported via the Strava MCP tool.
The MCP data format differs slightly from the REST API format — this command
handles the mapping so the same Activity model is populated either way.

Usage:
    python manage.py seed_from_mcp --file mcp_activities.json
"""
import json
from django.core.management.base import BaseCommand
from django.utils.dateparse import parse_datetime
from django.utils import timezone
from core.models import Activity


def _parse_dt(value: str):
    """Parse a naive ISO datetime string and make it timezone-aware (UTC)."""
    dt = parse_datetime(value)
    if dt is None:
        return None
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.utc)
    return dt


def _map(item: dict) -> dict:
    """Map a single MCP activity dict to Activity model field values."""
    s = item.get("summary", {})
    return {
        "name":                  item.get("name") or "",
        "sport_type":            item.get("sport_type", "Other"),
        "start_date":            _parse_dt(item.get("start_local", "")),
        "distance_meters":       s.get("distance", 0),
        "moving_time_seconds":   s.get("moving_time", 0),
        "elapsed_time_seconds":  s.get("elapsed_time", 0),
        "total_elevation_gain":  s.get("elevation_gain", 0),
        "average_speed":         s.get("avg_speed", 0),
        "max_speed":             s.get("max_speed", 0),
        "average_heartrate":     s.get("avg_heartrate"),
        "max_heartrate":         s.get("max_heartrate"),
        "average_cadence":       s.get("avg_cadence"),
        "kudos_count":           s.get("kudos_count", 0),
        "trainer":               item.get("is_trainer", False),
        "commute":               item.get("is_commute", False),
        "raw_data":              item,
    }


class Command(BaseCommand):
    help = "Seed the Activity table from a Strava MCP JSON export."

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            default="mcp_activities.json",
            help="Path to the JSON file containing MCP activities (default: mcp_activities.json)",
        )

    def handle(self, *args, **options):
        path = options["file"]
        try:
            with open(path, encoding="utf-8") as f:
                activities = json.load(f)
        except FileNotFoundError:
            self.stderr.write(self.style.ERROR(f"File not found: {path}"))
            return
        except json.JSONDecodeError as exc:
            self.stderr.write(self.style.ERROR(f"Invalid JSON: {exc}"))
            return

        created = updated = skipped = 0
        for item in activities:
            strava_id = item.get("id")
            if not strava_id:
                skipped += 1
                continue
            try:
                strava_id = int(strava_id)
            except (ValueError, TypeError):
                skipped += 1
                continue

            defaults = _map(item)
            if defaults["start_date"] is None:
                skipped += 1
                continue

            _, is_new = Activity.objects.update_or_create(
                strava_id=strava_id,
                defaults=defaults,
            )
            if is_new:
                created += 1
            else:
                updated += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Done: {created} created, {updated} updated, {skipped} skipped."
            )
        )
