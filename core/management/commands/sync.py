"""
Run the whole Strava pipeline in order: activities, best efforts, achievements.

This is what the Pipeline page's "Sync now" button and the scheduled task run.
A lock (``core.sync_lock``) stops two syncs from overlapping.  If the activity
sync fails (e.g. the Strava token was revoked) the later stages are skipped,
since they would fail the same way.  If a later stage has failures, the run's
``SyncLog`` is marked ``partial`` and the command exits with an error; the
failed items are retried by the next sync.
"""
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.models import SyncLog
from core.sync_lock import SyncAlreadyRunning, sync_lock


class Command(BaseCommand):
    """Sync activities, compute missing best efforts, then fetch new achievements."""

    help = "Full pipeline: sync activities, compute missing best efforts, fetch achievements."

    def add_arguments(self, parser):
        """Register the ``--full`` flag.

        Args:
            parser: The ``argparse.ArgumentParser`` provided by Django.
        """
        parser.add_argument(
            "--full",
            action="store_true",
            help="Re-fetch every activity instead of only recent ones.",
        )

    def handle(self, *_args, **options):
        """Run each pipeline stage under the sync lock.

        Args:
            *_args: Unused positional arguments (required by Django's interface).
            **options: Parsed CLI options.  Key used: ``full`` (bool).

        Raises:
            CommandError: If another sync is running, the activity sync failed,
                or a later stage had failures.
        """
        try:
            with sync_lock():
                self.stdout.write(f"=== Sync started {timezone.localtime():%Y-%m-%d %H:%M %Z} ===")
                try:
                    call_command("sync_activities", full=options["full"], stdout=self.stdout,
                                 stderr=self.stderr)
                except CommandError as exc:
                    raise CommandError(
                        f"{exc} Skipped best efforts and achievements."
                    ) from exc
                problems = self._run_later_stages()
        except SyncAlreadyRunning as exc:
            raise CommandError(str(exc)) from exc

        if problems:
            log = SyncLog.objects.order_by("-started_at").first()  # this run's, under the lock
            log.status = SyncLog.Status.PARTIAL
            log.error_message = "\n".join(filter(None, [log.error_message, *problems]))
            log.save(update_fields=["status", "error_message"])
            raise CommandError("Sync finished with problems:\n" + "\n".join(problems))

    def _run_later_stages(self) -> list[str]:
        """Run the best-effort and achievement stages, each even if the other fails.

        Returns:
            list[str]: One ``"<stage>: <error>"`` message per failed stage.
        """
        problems = []
        for stage in ("backfill_best_efforts", "fetch_achievements"):
            try:
                call_command(stage, stdout=self.stdout, stderr=self.stderr)
            except CommandError as exc:
                problems.append(f"{stage}: {exc}")
        return problems
