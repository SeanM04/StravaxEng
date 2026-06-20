from django.contrib import admin
from .models import Activity, StravaToken, SyncLog


@admin.register(StravaToken)
class StravaTokenAdmin(admin.ModelAdmin):
    list_display = ("athlete_id", "expires_at", "is_expired", "updated_at")
    readonly_fields = ("athlete_id", "access_token", "refresh_token", "expires_at", "updated_at")

    def is_expired(self, obj):
        return obj.is_expired
    is_expired.boolean = True
    is_expired.short_description = "Expired?"


@admin.register(SyncLog)
class SyncLogAdmin(admin.ModelAdmin):
    list_display = ("started_at", "status", "incremental", "activities_created", "activities_updated", "pages_fetched", "finished_at")
    list_filter = ("status", "incremental")
    readonly_fields = ("started_at", "finished_at", "status", "incremental", "pages_fetched",
                       "activities_created", "activities_updated", "error_message")


@admin.register(Activity)
class ActivityAdmin(admin.ModelAdmin):
    list_display = ("name", "sport_type", "start_date", "distance_km", "moving_time_display", "kudos_count")
    list_filter = ("sport_type", "trainer", "commute")
    search_fields = ("name",)
    readonly_fields = ("synced_at", "raw_data")
    ordering = ("-start_date",)
