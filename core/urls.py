from django.urls import path
from . import views

urlpatterns = [
    path("",                views.dashboard,        name="dashboard"),
    path("activities/",              views.activities_list,   name="activities"),
    path("activities/<int:strava_id>/", views.activity_detail, name="activity_detail"),
    path("analytics/",      views.analytics,        name="analytics"),
    path("records/",        views.records,           name="records"),
    path("coach/",          views.coach,             name="coach"),
    path("pipeline/",       views.pipeline_health,   name="pipeline"),
    path("settings/",       views.settings_view,     name="settings"),
    path("health/",         views.health,            name="health"),
]
