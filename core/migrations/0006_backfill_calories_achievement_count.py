"""
Data migration: backfill Activity.calories and Activity.achievement_count
from the raw_data JSONField, which already contains the full Strava API
response for every synced activity.

This avoids a full re-sync from the Strava API — the data is already local.
"""
from django.db import migrations


def backfill_calories_achievements(apps, schema_editor):
    """Copy calories and achievement_count out of raw_data into their own columns."""
    Activity = apps.get_model("core", "Activity")
    to_update = []
    for activity in Activity.objects.only("id", "raw_data").iterator(chunk_size=500):
        raw = activity.raw_data or {}
        activity.calories          = raw.get("calories")
        activity.achievement_count = raw.get("achievement_count", 0) or 0
        to_update.append(activity)
        if len(to_update) >= 500:
            Activity.objects.bulk_update(to_update, ["calories", "achievement_count"])
            to_update.clear()
    if to_update:
        Activity.objects.bulk_update(to_update, ["calories", "achievement_count"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0005_activity_achievement_count_activity_calories"),
    ]

    operations = [
        migrations.RunPython(
            backfill_calories_achievements,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
