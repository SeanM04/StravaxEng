import time
from django.db import models


class StravaToken(models.Model):
    """Stores the Strava OAuth2 token pair for a single athlete.

    Strava uses rolling refresh tokens: every call to ``/oauth/token`` returns
    a brand-new refresh token and invalidates the old one.  This model is the
    single source of truth — ``strava_client.py`` reads from and writes back to
    this row so every rotation is persisted automatically between runs.

    The access and refresh tokens are stored as plaintext ``TextField``s.
    For a single-user personal app this is acceptable; a multi-tenant
    deployment should encrypt them at rest (e.g. via
    ``django-encrypted-model-fields`` or a dedicated secrets manager).
    """

    athlete_id    = models.BigIntegerField(unique=True)
    access_token  = models.TextField()
    refresh_token = models.TextField()
    expires_at    = models.BigIntegerField(
        help_text="Unix timestamp when the access token expires"
    )
    updated_at    = models.DateTimeField(auto_now=True)

    def __str__(self):
        """Return a readable identifier including the athlete ID."""
        return f"StravaToken(athlete={self.athlete_id})"

    @property
    def is_expired(self) -> bool:
        """Return True if the access token has expired or expires within 5 minutes."""
        return time.time() >= (self.expires_at - 300)


class SyncLog(models.Model):
    """Audit trail entry for a single ETL pipeline run.

    Created at the start of each ``sync_activities`` invocation and updated
    with the final status, row counts, and any error message when the run
    finishes.  Used by the ``pipeline_health`` view to display sync history,
    and by the incremental-sync logic to determine the cutoff timestamp for
    the next run (``finished_at`` of the last successful log).
    """

    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        SUCCESS = "success", "Success"
        PARTIAL = "partial", "Partial"
        FAILED  = "failed",  "Failed"

    started_at         = models.DateTimeField(auto_now_add=True)
    finished_at        = models.DateTimeField(null=True, blank=True)
    status             = models.CharField(
        max_length=20, choices=Status.choices, default=Status.RUNNING
    )
    incremental        = models.BooleanField(
        default=True,
        help_text="False = full sync, True = since last run",
    )
    pages_fetched      = models.IntegerField(default=0)
    activities_created = models.IntegerField(default=0)
    activities_updated = models.IntegerField(default=0)
    error_message      = models.TextField(blank=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        """Return a summary line with start time, status, and wall-clock duration."""
        duration = ""
        if self.finished_at:
            secs = int((self.finished_at - self.started_at).total_seconds())
            duration = f" in {secs}s"
        return f"SyncLog {self.started_at:%Y-%m-%d %H:%M} — {self.status}{duration}"


class Activity(models.Model):
    """A single Strava activity stored locally for analytics.

    Each row corresponds to one Strava activity identified by ``strava_id``.
    The ``raw_data`` JSONField stores the full Strava API response so new
    fields can be backfilled without requiring a fresh API sync.  Computed
    properties (``distance_km``, ``pace_per_km``, ``moving_time_display``)
    convert raw SI units into human-readable values for templates.
    """

    class SportType(models.TextChoices):
        RUN   = "Run",   "Run"
        RIDE  = "Ride",  "Ride"
        SWIM  = "Swim",  "Swim"
        WALK  = "Walk",  "Walk"
        HIKE  = "Hike",  "Hike"
        OTHER = "Other", "Other"

    strava_id            = models.BigIntegerField(unique=True)
    name                 = models.CharField(max_length=255)
    sport_type           = models.CharField(
        max_length=50, choices=SportType.choices, default=SportType.OTHER, db_index=True
    )
    start_date           = models.DateTimeField(db_index=True)
    distance_meters      = models.FloatField(default=0)
    moving_time_seconds  = models.IntegerField(default=0)
    elapsed_time_seconds = models.IntegerField(default=0)
    total_elevation_gain = models.FloatField(default=0)
    average_speed        = models.FloatField(default=0)
    max_speed            = models.FloatField(default=0)
    average_heartrate    = models.FloatField(null=True, blank=True)
    max_heartrate        = models.FloatField(null=True, blank=True)
    average_cadence      = models.FloatField(null=True, blank=True)
    kudos_count          = models.IntegerField(default=0)
    trainer              = models.BooleanField(default=False)
    commute              = models.BooleanField(default=False)
    raw_data             = models.JSONField(default=dict)
    synced_at            = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-start_date"]
        verbose_name_plural = "activities"

    def __str__(self):
        """Return the activity name and its local date."""
        return f"{self.name} ({self.start_date.date()})"

    @property
    def distance_km(self) -> float:
        """Convert raw distance in metres to kilometres, rounded to 2 decimal places."""
        return round(self.distance_meters / 1000, 2)

    @property
    def pace_per_km(self) -> str:
        """Return moving pace as a ``M:SS /km`` string, or ``'—'`` when distance is zero."""
        if not self.distance_meters or not self.moving_time_seconds:
            return "—"
        secs_per_km = self.moving_time_seconds / (self.distance_meters / 1000)
        minutes, seconds = divmod(int(secs_per_km), 60)
        return f"{minutes}:{seconds:02d}"

    @property
    def moving_time_display(self) -> str:
        """Format moving time as a human-readable string.

        Returns:
            str: ``"1h 01m 05s"`` when the duration exceeds one hour,
            ``"10m 05s"`` otherwise.
        """
        hours, remainder = divmod(self.moving_time_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours:
            return f"{hours}h {minutes:02d}m {seconds:02d}s"
        return f"{minutes}m {seconds:02d}s"
