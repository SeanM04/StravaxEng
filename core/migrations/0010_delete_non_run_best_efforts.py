"""
Delete BestEffort rows that do not come from GPS runs.

Best efforts used to be computed for every sport, so walks (and any GPS ride)
competed for "fastest 5K".  They are now computed only for outdoor runs; this
removes the rows computed earlier for anything else.  The rows are derived
from Strava streams, so nothing original is lost.
"""
from django.db import migrations

# Frozen copy of core.best_effort.RUN_SPORT_TYPES at the time of this migration.
RUN_SPORT_TYPES = ("Run", "TrailRun")


def delete_non_run_best_efforts(apps, schema_editor):
    """Remove best efforts whose activity is not an outdoor run."""
    BestEffort = apps.get_model("core", "BestEffort")
    BestEffort.objects.exclude(
        activity__sport_type__in=RUN_SPORT_TYPES, activity__trainer=False
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0009_activity_checked_flags"),
    ]

    operations = [
        migrations.RunPython(delete_non_run_best_efforts, reverse_code=migrations.RunPython.noop),
    ]
