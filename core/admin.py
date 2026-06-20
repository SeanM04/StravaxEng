"""Django admin registrations for StravaToken, SyncLog, and Activity models."""
from django.contrib import admin
from .models import Activity, StravaToken, SyncLog


@admin.register(StravaToken)
class StravaTokenAdmin(admin.ModelAdmin):
    """Admin view for the StravaToken model.

    All token fields are read-only to prevent accidental manual edits
    that could desync the stored token pair from what Strava expects.
    """

    list_display  = ("athlete_id", "expires_at", "is_expired", "updated_at")
    readonly_fields = (
        "athlete_id", "access_token", "refresh_token", "expires_at", "updated_at"
    )

    def is_expired(self, obj):
        """Expose the ``is_expired`` model property as a boolean column."""
        return obj.is_expired

    is_expired.boolean = True
    is_expired.short_description = "Expired?"


@admin.register(SyncLog)
class SyncLogAdmin(admin.ModelAdmin):
    """Admin view for the SyncLog model.

    All fields are read-only because sync logs are written exclusively by
    the ``sync_activities`` management command and should not be edited manually.
    """

    list_display  = (
        "started_at", "status", "incremental",
        "activities_created", "activities_updated", "pages_fetched", "finished_at",
    )
    list_filter   = ("status", "incremental")
    readonly_fields = (
        "started_at", "finished_at", "status", "incremental", "pages_fetched",
        "activities_created", "activities_updated", "error_message",
    )


@admin.register(Activity)
class ActivityAdmin(admin.ModelAdmin):
    """Admin view for the Activity model.

    ``synced_at`` and ``raw_data`` are read-only; ``raw_data`` stores the
    full Strava API payload and should never be edited manually.
    """

    list_display  = (
        "name", "sport_type", "start_date",
        "distance_km", "moving_time_display", "kudos_count",
    )
    list_filter   = ("sport_type", "trainer", "commute")
    search_fields = ("name",)
    readonly_fields = ("synced_at", "raw_data")
    ordering      = ("-start_date",)
