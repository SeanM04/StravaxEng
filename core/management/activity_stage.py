"""Shared loop for pipeline stages that make one Strava request per activity."""
import requests
from django.core.management.base import BaseCommand, CommandError

from core.strava_client import StravaUnavailableError


class ActivityStageCommand(BaseCommand):
    """Base for commands that fetch something from Strava for each activity.

    Subclasses set ``noun`` and implement ``select`` and ``process``.  Network
    failures are reported and left for the next run to retry; if any occurred
    the command exits with an error so ``manage.py sync`` can record a partial
    run.  An unusable token or a used-up daily API allowance stops the loop at
    once, since every remaining request would fail the same way.  Pacing under
    Strava's 15-minute limit happens in ``core.strava_client``.
    """

    noun = "items"  # what is fetched, for messages, e.g. "best efforts"

    def select(self, include_checked: bool):
        """Return the activities to process.

        Args:
            include_checked: Whether to include activities already processed
                (``--force``).

        Raises:
            NotImplementedError: Always; subclasses must override.
        """
        raise NotImplementedError

    def process(self, activity) -> tuple[int, int]:
        """Fetch and store data for one activity.

        Args:
            activity: The ``Activity`` to process.

        Raises:
            NotImplementedError: Always; subclasses must override.
        """
        raise NotImplementedError

    def add_arguments(self, parser):
        """Attach ``--limit`` and ``--force``.

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
            help="Re-process activities that have already been processed.",
        )

    def handle(self, *_args, **options):
        """Process every selected activity.

        Args:
            *_args: Unused positional arguments (required by Django's interface).
            **options: Parsed CLI options — ``limit`` (int | None) and
                ``force`` (bool).

        Raises:
            CommandError: If Strava is unavailable (token unusable or daily
                limit used up), or if any activity could not be fetched.
        """
        qs = self.select(include_checked=options["force"])
        if options["limit"]:
            qs = qs[: options["limit"]]
        activities = list(qs)

        total = len(activities)
        if total == 0:
            self.stdout.write(f"No activities need {self.noun}.")
            return

        self.stdout.write(
            f"Fetching {self.noun} for {total} activit{'y' if total == 1 else 'ies'}…"
        )
        created = updated = 0
        errors: list[str] = []

        for i, activity in enumerate(activities, 1):
            try:
                c, u = self.process(activity)
            except StravaUnavailableError as exc:
                raise CommandError(str(exc)) from exc
            except requests.RequestException as exc:
                errors.append(f"activity {activity.strava_id}: {exc}")
                self.stderr.write(f"  Failed {activity.strava_id}: {exc}")
            else:
                created += c
                updated += u

            if i % 10 == 0 or i == total:
                self.stdout.write(f"  [{i}/{total}] processed")

        self.stdout.write(f"Done — {created} created, {updated} updated, {len(errors)} failed.")
        if errors:
            raise CommandError(
                f"{len(errors)} of {total} {self.noun} fetches failed (retried next sync). "
                f"First: {errors[0]}"
            )
