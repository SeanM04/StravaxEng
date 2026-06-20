from datetime import date, timedelta

import pytest
from django.test import TestCase, RequestFactory
from django.contrib.auth.models import User

from .models import Activity, SyncLog, StravaToken
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
        assert current == 2
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
