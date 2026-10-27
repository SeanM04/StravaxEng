"""
Add Activity.best_efforts_checked_at / achievements_checked_at.

Activities that already have BestEffort or Achievement rows are marked as
checked, so the first sync after this migration only fetches the ones that
were never processed (or whose last fetch failed).
"""
from django.db import migrations, models
from django.utils import timezone


def mark_already_processed(apps, schema_editor):
    """Stamp activities whose best efforts / achievements are already stored."""
    Activity = apps.get_model("core", "Activity")
    now = timezone.now()
    Activity.objects.filter(best_efforts__isnull=False).update(best_efforts_checked_at=now)
    Activity.objects.filter(segment_achievements__isnull=False).update(achievements_checked_at=now)


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0008_besteffort'),
    ]

    operations = [
        migrations.AddField(
            model_name='activity',
            name='achievements_checked_at',
            field=models.DateTimeField(blank=True, help_text='When segment achievements were fetched (empty = still to do)', null=True),
        ),
        migrations.AddField(
            model_name='activity',
            name='best_efforts_checked_at',
            field=models.DateTimeField(blank=True, help_text='When GPS streams were processed for best efforts (empty = still to do)', null=True),
        ),
        migrations.RunPython(mark_already_processed, reverse_code=migrations.RunPython.noop),
    ]
