import json
from datetime import date, timedelta

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Avg, Count, Sum
from django.db.models.functions import TruncWeek
from django.http import JsonResponse
from django.shortcuts import render

from .models import Activity, SyncLog, StravaToken


# ── Private helpers ───────────────────────────────────────────────────────────

def _aggregate(qs):
    raw = qs.aggregate(
        total_distance=Sum("distance_meters"),
        total_time=Sum("moving_time_seconds"),
        total_elevation=Sum("total_elevation_gain"),
        avg_heartrate=Avg("average_heartrate"),
        avg_cadence=Avg("average_cadence"),
        total_count=Count("id"),
    )
    m = raw["total_distance"] or 0
    s = raw["total_time"] or 0
    return {**raw, "total_distance_km": round(m / 1000, 1), "total_time_hours": round(s / 3600, 1)}


def _daily_streaks(date_set, today):
    current = 0
    d = today
    while d in date_set:
        current += 1
        d -= timedelta(days=1)
    if current == 0:
        d = today - timedelta(days=1)
        while d in date_set:
            current += 1
            d -= timedelta(days=1)

    sorted_dates = sorted(date_set)
    longest = 1 if sorted_dates else 0
    run = 1
    for i in range(1, len(sorted_dates)):
        if (sorted_dates[i] - sorted_dates[i - 1]).days == 1:
            run += 1
            longest = max(longest, run)
        else:
            run = 1
    return current, longest


def _weekly_streaks(date_set, today):
    week_set = {d.isocalendar()[:2] for d in date_set}
    sorted_weeks = sorted(week_set, key=lambda yw: date.fromisocalendar(yw[0], yw[1], 1))

    longest = 1 if sorted_weeks else 0
    wrun = 1
    for i in range(1, len(sorted_weeks)):
        prev = date.fromisocalendar(sorted_weeks[i - 1][0], sorted_weeks[i - 1][1], 1)
        curr = date.fromisocalendar(sorted_weeks[i][0], sorted_weeks[i][1], 1)
        if (curr - prev).days == 7:
            wrun += 1
            longest = max(longest, wrun)
        else:
            wrun = 1

    this_week = today.isocalendar()[:2]
    check = date.fromisocalendar(this_week[0], this_week[1], 1)
    if this_week not in week_set:
        check -= timedelta(days=7)
    current = 0
    while check.isocalendar()[:2] in week_set:
        current += 1
        check -= timedelta(days=7)
    return current, longest


def _personal_records(runs, all_acts):
    longest_run       = runs.order_by("-distance_meters").first()
    highest_elevation = runs.order_by("-total_elevation_gain").first()
    most_kudos        = all_acts.order_by("-kudos_count").first()
    cand = list(runs.filter(distance_meters__gte=3000, moving_time_seconds__gt=0))
    fastest_run = (
        min(cand, key=lambda r: r.moving_time_seconds / (r.distance_meters / 1000))
        if cand else None
    )
    return longest_run, fastest_run, highest_elevation, most_kudos


def _avg_pace_min_per_km(batch):
    valid = [r for r in batch if r.distance_meters > 0]
    if not valid:
        return None
    return sum(r.moving_time_seconds / (r.distance_meters / 1000) for r in valid) / len(valid) / 60


# ── Views ─────────────────────────────────────────────────────────────────────

_SORT_MAP = {
    "date": "start_date", "-date": "-start_date",
    "dist": "distance_meters", "-dist": "-distance_meters",
    "elev": "total_elevation_gain", "-elev": "-total_elevation_gain",
    "kudos": "kudos_count", "-kudos": "-kudos_count",
    "cadence": "average_cadence", "-cadence": "-average_cadence",
}


@login_required
def dashboard(request):
    stats = _aggregate(Activity.objects.all())

    chart_qs = (
        Activity.objects.filter(sport_type="Run", distance_meters__gt=0)
        .order_by("start_date").values("start_date", "distance_meters")
    )
    chart_labels = json.dumps([a["start_date"].strftime("%b %d") for a in chart_qs])
    chart_data   = json.dumps([round(a["distance_meters"] / 1000, 2) for a in chart_qs])

    return render(request, "core/dashboard.html", {
        "active_page": "dashboard", "page_title": "Dashboard",
        "stats": stats, "chart_labels": chart_labels, "chart_data": chart_data,
        "recent_activities": Activity.objects.all()[:8],
    })


@login_required
def activities_list(request):
    qs     = Activity.objects.all()
    sport  = request.GET.get("sport", "")
    search = request.GET.get("q", "")
    if sport:
        qs = qs.filter(sport_type=sport)
    if search:
        qs = qs.filter(name__icontains=search)

    sort_key   = request.GET.get("sort", "-date")
    sort_field = _SORT_MAP.get(sort_key, "-start_date")
    qs = qs.order_by(sort_field)

    page_obj    = Paginator(qs, 25).get_page(request.GET.get("page"))
    sport_types = (
        Activity.objects.values_list("sport_type", flat=True).distinct().order_by("sport_type")
    )

    return render(request, "core/activities.html", {
        "active_page": "activities", "page_title": "Activities",
        "page_obj": page_obj, "sport_types": sport_types,
        "current_sport": sport, "current_sort": sort_key, "search_query": search,
    })


@login_required
def analytics(request):
    weekly_qs = (
        Activity.objects.filter(distance_meters__gt=0)
        .annotate(week=TruncWeek("start_date")).values("week")
        .annotate(total_m=Sum("distance_meters"), count=Count("id")).order_by("week")
    )
    run_qs = (
        Activity.objects.filter(
            sport_type="Run", distance_meters__gte=1000, moving_time_seconds__gt=0
        ).order_by("start_date").values("start_date", "distance_meters", "moving_time_seconds")
    )
    run_stats = Activity.objects.filter(sport_type="Run").aggregate(
        count=Count("id"), total_m=Sum("distance_meters")
    )
    total_run_km = round((run_stats["total_m"] or 0) / 1000, 1)

    return render(request, "core/analytics.html", {
        "active_page": "analytics", "page_title": "Analytics",
        "week_labels":  json.dumps([w["week"].strftime("%b %d") for w in weekly_qs]),
        "week_km":      json.dumps([round(w["total_m"] / 1000, 1) for w in weekly_qs]),
        "week_counts":  json.dumps([w["count"] for w in weekly_qs]),
        "pace_labels":  json.dumps([r["start_date"].strftime("%b %d") for r in run_qs]),
        "pace_data":    json.dumps([
            round(r["moving_time_seconds"] / (r["distance_meters"] / 1000) / 60, 2)
            for r in run_qs
        ]),
        "run_count": run_stats["count"] or 0, "total_run_km": total_run_km,
    })


@login_required
def records(request):
    runs     = Activity.objects.filter(sport_type="Run")
    all_acts = Activity.objects.all()
    today    = date.today()

    longest_run, fastest_run, highest_elevation, most_kudos = _personal_records(runs, all_acts)

    date_set = set(Activity.objects.values_list("start_date__date", flat=True).distinct())
    current_streak,        longest_streak        = _daily_streaks(date_set, today)
    current_weekly_streak, longest_weekly_streak = _weekly_streaks(date_set, today)

    sport_breakdown = (
        all_acts.values("sport_type")
        .annotate(count=Count("id"), total_km=Sum("distance_meters")).order_by("-count")
    )

    return render(request, "core/records.html", {
        "active_page": "records", "page_title": "Records & Streaks",
        "longest_run": longest_run, "highest_elevation": highest_elevation,
        "most_kudos": most_kudos, "fastest_run": fastest_run,
        "current_streak": current_streak, "longest_streak": longest_streak,
        "current_weekly_streak": current_weekly_streak,
        "longest_weekly_streak": longest_weekly_streak,
        "sport_breakdown": sport_breakdown,
    })


@login_required
def coach(request):
    runs = Activity.objects.filter(sport_type="Run", distance_meters__gt=0)

    weeks = list(
        runs.annotate(week=TruncWeek("start_date")).values("week")
        .annotate(total_m=Sum("distance_meters")).order_by("week")
    )
    avg_weekly_km = round(sum(w["total_m"] for w in weeks) / len(weeks) / 1000, 1) if weeks else 0

    recent_runs    = list(runs.filter(moving_time_seconds__gt=0).order_by("-start_date")[:20])
    latest_10_pace = _avg_pace_min_per_km(recent_runs[:10])
    prev_10_pace   = _avg_pace_min_per_km(recent_runs[10:20])
    pace_trend     = None
    if latest_10_pace and prev_10_pace:
        pace_trend = "improving" if latest_10_pace < prev_10_pace else "declining"

    return render(request, "core/coach.html", {
        "active_page": "coach", "page_title": "AI Coach Notes",
        "avg_weekly_km":  avg_weekly_km,
        "pace_trend":     pace_trend,
        "latest_pace":    round(latest_10_pace, 2) if latest_10_pace else None,
        "long_run_count": runs.filter(distance_meters__gte=15000).count(),
        "total_runs":     runs.count(),
        "recent_runs":    recent_runs[:5],
    })


@login_required
def pipeline_health(request):
    total_syncs   = SyncLog.objects.count()
    success_syncs = SyncLog.objects.filter(status=SyncLog.Status.SUCCESS).count()

    return render(request, "core/pipeline.html", {
        "active_page": "pipeline", "page_title": "Pipeline Health",
        "logs":             SyncLog.objects.all()[:20],
        "last_success": (
            SyncLog.objects.filter(status=SyncLog.Status.SUCCESS).order_by("-finished_at").first()
        ),
        "total_activities": Activity.objects.count(),
        "total_runs":       Activity.objects.filter(sport_type="Run").count(),
        "total_syncs":      total_syncs,
        "success_rate":     round(success_syncs / total_syncs * 100) if total_syncs else None,
        "token":            StravaToken.objects.first(),
    })


@login_required
def settings_view(request):
    token = StravaToken.objects.first()
    return render(request, "core/settings_page.html", {
        "active_page": "settings", "page_title": "Settings",
        "token": token, "has_token": token is not None,
        "token_expired": token.is_expired if token else True,
    })


def health(request):
    try:
        Activity.objects.exists()
        return JsonResponse({"status": "ok", "database": "connected"})
    except Exception:  # noqa: BLE001 — any DB error should return 503
        return JsonResponse({"status": "error", "database": "unreachable"}, status=503)
