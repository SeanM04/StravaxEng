from datetime import date, timedelta

from django.contrib.auth.models import User
from django.test import TestCase

from .models import Achievement, Activity
from .views import _daily_streaks, _weekly_streaks, _avg_pace_min_per_km


# ── Model helpers ─────────────────────────────────────────────────────────────

def make_activity(**kwargs):
    defaults = {
        "strava_id": 1,
        "name": "Test Run",
        "sport_type": "Run",
        "start_date": "2024-01-10T08:00:00Z",
        "distance_meters": 10000,
        "moving_time_seconds": 3600,
    }
    defaults.update(kwargs)
    return Activity(**defaults)


# ── Activity model ────────────────────────────────────────────────────────────

class ActivityModelTests(TestCase):
    def test_distance_km(self):
        a = make_activity(distance_meters=10000)
        assert a.distance_km == 10.0

    def test_pace_per_km(self):
        a = make_activity(distance_meters=10000, moving_time_seconds=3600)
        assert a.pace_per_km == "6:00"

    def test_pace_per_km_zero_distance(self):
        a = make_activity(distance_meters=0, moving_time_seconds=0)
        assert a.pace_per_km == "—"

    def test_moving_time_display_with_hours(self):
        a = make_activity(moving_time_seconds=3661)
        assert a.moving_time_display == "1h 01m 01s"

    def test_moving_time_display_no_hours(self):
        a = make_activity(moving_time_seconds=605)
        assert a.moving_time_display == "10m 05s"


# ── Streak helpers ────────────────────────────────────────────────────────────

class DailyStreakTests(TestCase):
    def test_no_activities(self):
        current, longest = _daily_streaks(set(), date.today())
        assert current == 0
        assert longest == 0

    def test_consecutive_streak_today(self):
        today = date(2024, 6, 10)
        dates = {today - timedelta(days=i) for i in range(5)}
        current, longest = _daily_streaks(dates, today)
        assert current == 5
        assert longest == 5

    def test_streak_broken(self):
        today = date(2024, 6, 10)
        dates = {date(2024, 6, 8), date(2024, 6, 7), date(2024, 6, 1)}
        current, longest = _daily_streaks(dates, today)
        # last run was June 8 — two days ago — so the streak is broken
        assert current == 0
        assert longest == 2


class WeeklyStreakTests(TestCase):
    def test_no_activities(self):
        current, longest = _weekly_streaks(set(), date.today())
        assert current == 0
        assert longest == 0

    def test_consecutive_weeks(self):
        today = date(2024, 6, 10)
        dates = {
            date(2024, 6, 10),
            date(2024, 6, 3),
            date(2024, 5, 27),
        }
        current, longest = _weekly_streaks(dates, today)
        assert current == 3
        assert longest == 3

    def test_non_consecutive_weeks(self):
        today = date(2024, 6, 10)
        dates = {date(2024, 6, 10), date(2024, 5, 20)}
        current, longest = _weekly_streaks(dates, today)
        assert current == 1
        assert longest == 1


# ── Pace helper ───────────────────────────────────────────────────────────────

class AvgPaceTests(TestCase):
    def test_empty(self):
        assert _avg_pace_min_per_km([]) is None

    def test_zero_distance_excluded(self):
        a = make_activity(distance_meters=0, moving_time_seconds=600)
        assert _avg_pace_min_per_km([a]) is None

    def test_correct_pace(self):
        a = make_activity(distance_meters=10000, moving_time_seconds=3600)
        result = _avg_pace_min_per_km([a])
        assert abs(result - 6.0) < 0.01


# ── Health check ──────────────────────────────────────────────────────────────

class HealthCheckTests(TestCase):
    def test_health_returns_200_with_db(self):
        response = self.client.get("/health/")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["database"] == "connected"


# ── Backfill migration logic ──────────────────────────────────────────────────

class BackfillCaloriesTests(TestCase):
    def test_calories_extracted_from_raw_data(self):
        a = Activity.objects.create(
            strava_id=77777,
            name="Backfill Test",
            sport_type="Run",
            start_date="2024-03-15T07:00:00Z",
            raw_data={"calories": 512.0, "achievement_count": 3},
        )
        raw = a.raw_data or {}
        a.calories = raw.get("calories")
        a.achievement_count = raw.get("achievement_count", 0) or 0
        a.save(update_fields=["calories", "achievement_count"])
        a.refresh_from_db()
        assert a.calories == 512.0
        assert a.achievement_count == 3

    def test_null_calories_when_absent_from_raw_data(self):
        a = Activity.objects.create(
            strava_id=77778,
            name="No Calories",
            sport_type="Run",
            start_date="2024-03-16T07:00:00Z",
            raw_data={},
        )
        raw = a.raw_data or {}
        a.calories = raw.get("calories")
        a.achievement_count = raw.get("achievement_count", 0) or 0
        a.save(update_fields=["calories", "achievement_count"])
        a.refresh_from_db()
        assert a.calories is None
        assert a.achievement_count == 0


# ── Achievement model ─────────────────────────────────────────────────────────

class AchievementModelTests(TestCase):
    def setUp(self):
        self.activity = Activity.objects.create(
            strava_id=88801,
            name="Segment Run",
            sport_type="Run",
            start_date="2024-05-01T07:00:00Z",
            achievement_count=2,
        )

    def test_str(self):
        ach = Achievement(
            activity=self.activity,
            segment_name="Riverside Sprint",
            achievement_type=Achievement.Type.PR,
            rank=1,
        )
        assert str(ach) == "Personal Record — Riverside Sprint (#1)"

    def test_str_kom(self):
        ach = Achievement(
            activity=self.activity,
            segment_name="Big Hill",
            achievement_type=Achievement.Type.KOM,
            rank=1,
        )
        assert str(ach) == "King of the Mountain — Big Hill (#1)"

    def test_unique_together_enforced(self):
        Achievement.objects.create(
            activity=self.activity,
            segment_name="Sprint Segment",
            achievement_type=Achievement.Type.PR,
            rank=2,
        )
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            Achievement.objects.create(
                activity=self.activity,
                segment_name="Sprint Segment",
                achievement_type=Achievement.Type.PR,
                rank=1,
            )


# ── Activity detail view ──────────────────────────────────────────────────────

class ActivityDetailViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("testrunner", password="testpass123")
        self.activity = Activity.objects.create(
            strava_id=88802,
            name="Detail Test Run",
            sport_type="Run",
            start_date="2024-05-02T07:00:00Z",
            distance_meters=10000,
            moving_time_seconds=3600,
            achievement_count=2,
        )
        Achievement.objects.create(
            activity=self.activity,
            segment_name="Big Hill KOM",
            achievement_type=Achievement.Type.KOM,
            rank=1,
        )
        Achievement.objects.create(
            activity=self.activity,
            segment_name="Flat Sprint",
            achievement_type=Achievement.Type.PR,
            rank=3,
        )

    def test_detail_returns_200_and_shows_achievements(self):
        self.client.force_login(self.user)
        response = self.client.get(f"/activities/{self.activity.strava_id}/")
        assert response.status_code == 200
        assert b"Big Hill KOM" in response.content
        assert b"Flat Sprint" in response.content

    def test_detail_returns_404_for_unknown_strava_id(self):
        self.client.force_login(self.user)
        response = self.client.get("/activities/9999999/")
        assert response.status_code == 404

    def test_detail_shows_fetch_prompt_when_count_positive_but_no_rows(self):
        self.client.force_login(self.user)
        unfetched = Activity.objects.create(
            strava_id=88803,
            name="Unfetched Run",
            sport_type="Run",
            start_date="2024-05-03T07:00:00Z",
            achievement_count=3,
        )
        response = self.client.get(f"/activities/{unfetched.strava_id}/")
        assert response.status_code == 200
        assert b"fetch_achievements" in response.content

    def test_detail_redirects_unauthenticated(self):
        response = self.client.get(f"/activities/{self.activity.strava_id}/")
        assert response.status_code == 302
