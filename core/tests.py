"""Tests for the core app: models, view helpers, views, best efforts and the sync pipeline."""
import io
import os
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

import requests
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError
from django.test import TestCase, override_settings
from django.utils import timezone

from .achievements import activities_for_achievements
from .best_effort import activities_for_best_efforts, compute_and_save, find_best_effort
from .models import Achievement, Activity, BestEffort, StravaToken, SyncLog
from . import strava_client
from .strava_client import REAUTH_COMMAND, StravaAuthError, StravaRateLimitError
from .sync_lock import STALE_AFTER_S, SyncAlreadyRunning, sync_in_progress, sync_lock
from .views import (
    _avg_pace_min_per_km,
    _daily_streaks,
    _distance_bests,
    _format_pace,
    _pace_trend,
    _strava_connection,
    _weekly_streaks,
)

User = get_user_model()


# ── Model helpers ─────────────────────────────────────────────────────────────

def make_activity(**kwargs):
    """Build an unsaved ``Activity`` with sensible run defaults.

    Args:
        **kwargs: Field values overriding the defaults.

    Returns:
        Activity: The unsaved instance.
    """
    defaults = {
        "strava_id": 1,
        "name": "Test Run",
        "sport_type": "Run",
        # A real datetime, as the sync produces; a string here hides date bugs.
        "start_date": datetime.fromisoformat("2024-01-10T08:00:00+00:00"),
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


# ── Sliding-window best-effort algorithm ─────────────────────────────────────

class FindBestEffortTests(TestCase):
    def test_exact_match(self):
        """Single window that exactly covers the target returns its time."""
        distances = [0.0, 100.0, 200.0, 300.0, 400.0, 500.0]
        times     = [0,   10,    20,    30,    40,    50]
        assert find_best_effort(distances, times, 500.0) == 50.0

    def test_no_segment_meets_target(self):
        """Activity shorter than the target returns None."""
        distances = [0.0, 100.0, 200.0]
        times     = [0,   10,    20]
        assert find_best_effort(distances, times, 500.0) is None

    def test_fastest_window_not_first(self):
        """The optimal window is later in the activity, not at the start."""
        # Slow first half, fast second half:
        #   i=0 → j=5: 500m in 80s
        #   i=1 → j=6: 500m in 65s
        #   i=2 → j=7: 500m in 50s  ← fastest
        distances = [0,   100, 200, 300, 400, 500, 600, 700]
        times     = [0,   20,  40,  60,  70,  80,  85,  90]
        assert find_best_effort(distances, times, 500.0) == 50.0

    def test_multiple_1k_windows_returns_minimum(self):
        """Multiple 1-K windows; the fastest one (not the first) is returned."""
        # Segment times: 300s, 180s, 180s, 120s, 120s → best = 120s
        distances = [0,    1000, 2000, 3000, 4000, 5000]
        times     = [0,    300,  480,  660,  780,  900]
        assert find_best_effort(distances, times, 1000.0) == 120.0

    def test_empty_arrays_returns_none(self):
        assert find_best_effort([], [], 1000.0) is None

    def test_single_element_returns_none(self):
        assert find_best_effort([0.0], [0], 1000.0) is None

    def test_zero_target_returns_none(self):
        distances = [0.0, 500.0, 1000.0]
        times     = [0,   60,    120]
        assert find_best_effort(distances, times, 0.0) is None

    def test_mismatched_lengths_returns_none(self):
        assert find_best_effort([0.0, 500.0], [0], 500.0) is None


# ── Sync pipeline: lock, sync command, Sync now button ───────────────────────

def use_temp_sync_lock(test):
    """Point the sync lock file at a temp directory for the duration of *test*.

    Args:
        test: The running ``TestCase``.

    Returns:
        Path: The temporary lock file path.
    """
    # enterContext cleans the directory up when the test ends.
    tmp_dir = test.enterContext(tempfile.TemporaryDirectory())  # pylint: disable=consider-using-with
    lock_path = Path(tmp_dir) / ".sync.lock"
    test.enterContext(mock.patch("core.sync_lock.LOCK_PATH", lock_path))
    return lock_path


class SyncLockTests(TestCase):
    def setUp(self):
        self.lock_path = use_temp_sync_lock(self)

    def test_lock_is_held_inside_block_and_released_after(self):
        with sync_lock():
            assert sync_in_progress()
        assert not sync_in_progress()
        assert not self.lock_path.exists()

    def test_second_sync_is_refused_while_first_runs(self):
        with sync_lock(), self.assertRaises(SyncAlreadyRunning):
            with sync_lock():
                pass

    def test_lock_is_released_when_sync_raises(self):
        with self.assertRaises(RuntimeError), sync_lock():
            raise RuntimeError("boom")
        assert not self.lock_path.exists()

    def test_stale_lock_from_crashed_run_is_replaced(self):
        self.lock_path.write_text("12345")
        long_ago = time.time() - STALE_AFTER_S - 60
        os.utime(self.lock_path, (long_ago, long_ago))
        assert not sync_in_progress()
        with sync_lock():
            assert sync_in_progress()


class SyncCommandTests(TestCase):
    def setUp(self):
        use_temp_sync_lock(self)
        self.stages = []

    def _run_sync(self, activity_sync_status, failing_stage=None):
        """Run ``manage.py sync`` with every stage stubbed out.

        Args:
            activity_sync_status: The ``SyncLog`` status the stubbed
                ``sync_activities`` stage records (it raises if ``failed``,
                like the real command).
            failing_stage: Name of a later stage that raises ``CommandError``.

        Raises:
            CommandError: From the stubbed stage, mimicking the real command.
        """
        def fake_stage(name, **_kwargs):
            self.stages.append(name)
            if name == "sync_activities":
                SyncLog.objects.create(status=activity_sync_status)
                if activity_sync_status == SyncLog.Status.FAILED:
                    raise CommandError("Sync failed: boom")
            if name == failing_stage:
                raise CommandError("1 of 1 fetches failed")

        with mock.patch("core.management.commands.sync.call_command", side_effect=fake_stage):
            call_command("sync", stdout=io.StringIO())

    def test_runs_all_stages_in_order(self):
        self._run_sync(SyncLog.Status.SUCCESS)
        assert self.stages == ["sync_activities", "backfill_best_efforts", "fetch_achievements"]

    def test_skips_later_stages_when_activity_sync_fails(self):
        with self.assertRaises(CommandError):
            self._run_sync(SyncLog.Status.FAILED)
        assert self.stages == ["sync_activities"]

    def test_refuses_to_start_while_another_sync_runs(self):
        with sync_lock(), self.assertRaises(CommandError):
            self._run_sync(SyncLog.Status.SUCCESS)
        assert not self.stages

    def test_later_stage_failure_marks_run_partial_but_runs_remaining_stages(self):
        with self.assertRaises(CommandError):
            self._run_sync(SyncLog.Status.SUCCESS, failing_stage="backfill_best_efforts")
        assert self.stages == ["sync_activities", "backfill_best_efforts", "fetch_achievements"]
        log = SyncLog.objects.get()
        assert log.status == SyncLog.Status.PARTIAL
        assert "backfill_best_efforts" in log.error_message


class SyncNowViewTests(TestCase):
    def setUp(self):
        use_temp_sync_lock(self)
        self.start = self.enterContext(mock.patch("core.views.start_background_sync"))
        self.client.force_login(User.objects.create_user("syncer", password="testpass123"))

    def test_post_starts_background_sync(self):
        response = self.client.post("/pipeline/sync/")
        assert response.status_code == 302
        self.start.assert_called_once()

    def test_does_not_start_a_second_sync_while_one_runs(self):
        with sync_lock():
            self.client.post("/pipeline/sync/")
        self.start.assert_not_called()

    def test_get_is_not_allowed(self):
        assert self.client.get("/pipeline/sync/").status_code == 405

    def test_anonymous_user_cannot_start_sync(self):
        self.client.logout()
        assert self.client.post("/pipeline/sync/").status_code == 302
        self.start.assert_not_called()

    def test_pipeline_page_shows_sync_button(self):
        response = self.client.get("/pipeline/")
        assert b"Sync now" in response.content


# ── Pipeline stages against a mocked Strava API ──────────────────────────────

FIVE_K_STREAMS = {"distance": [0.0, 2500.0, 5000.0], "time": [0, 750, 1500]}


def strava_activity(strava_id, **overrides):
    """Return an activity dict shaped like ``GET /athlete/activities`` output.

    Args:
        strava_id: The Strava activity ID.
        **overrides: Fields to override.

    Returns:
        dict: The activity payload.
    """
    item = {
        "id": strava_id,
        "name": "Morning Run",
        "sport_type": "Run",
        "start_date": "2026-10-27T06:28:56Z",
        "distance": 5000.0,
        "moving_time": 1500,
        "elapsed_time": 1550,
    }
    item.update(overrides)
    return item


class ComputeAndSaveTests(TestCase):
    def setUp(self):
        self.activity = make_activity(strava_id=7001, distance_meters=5000)
        self.activity.save()
        self.streams = self.enterContext(mock.patch("core.strava_client.fetch_activity_streams"))

    def test_saves_best_efforts_and_marks_activity_checked(self):
        self.streams.return_value = FIVE_K_STREAMS
        created, _ = compute_and_save(self.activity)
        assert created > 0
        five_k = BestEffort.objects.get(activity=self.activity, target_distance_m=5000)
        assert five_k.elapsed_time_s == 1500
        assert self.activity not in activities_for_best_efforts()

    def test_activity_without_gps_data_is_still_marked_checked(self):
        self.streams.return_value = {"distance": [], "time": []}
        assert compute_and_save(self.activity) == (0, 0)
        assert self.activity not in activities_for_best_efforts()

    def test_failed_fetch_raises_and_leaves_activity_for_retry(self):
        self.streams.side_effect = requests.ConnectionError("network down")
        with self.assertRaises(requests.ConnectionError):
            compute_and_save(self.activity)
        assert self.activity in activities_for_best_efforts()

    def test_walks_and_treadmill_runs_are_never_fetched(self):
        walk = make_activity(strava_id=7002, sport_type="Walk", distance_meters=6000)
        treadmill = make_activity(strava_id=7003, trainer=True, distance_meters=6000)
        for activity in (walk, treadmill):
            activity.save()
            assert compute_and_save(activity) == (0, 0)
            assert activity not in activities_for_best_efforts()
        self.streams.assert_not_called()


class RecordsPersonalBestsTests(TestCase):
    def test_only_runs_compete_for_personal_bests(self):
        run = make_activity(strava_id=7101)
        walk = make_activity(strava_id=7102, sport_type="Walk")
        for activity, seconds in ((run, 1500), (walk, 900)):
            activity.save()
            BestEffort.objects.create(
                activity=activity, target_distance_m=5000, elapsed_time_s=seconds,
                achieved_at=date(2024, 1, 10), pace_per_km=seconds / 300,
            )
        five_k = next(pb for pb in _distance_bests() if pb["target_m"] == 5000)
        assert five_k["best"].activity == run


class SyncActivitiesCommandTests(TestCase):
    def setUp(self):
        self.fetch =self.enterContext(mock.patch("core.strava_client.fetch_activities"))
        self.streams = self.enterContext(mock.patch("core.strava_client.fetch_activity_streams"))
        self.streams.return_value = FIVE_K_STREAMS

    def _sync(self):
        call_command("sync_activities", stdout=io.StringIO(), stderr=io.StringIO())

    def test_new_activity_gets_best_efforts_and_run_succeeds(self):
        # Regression: a freshly-created activity used to hold start_date as a
        # string, crashing best-effort computation.
        self.fetch.return_value = [strava_activity(9001)]
        self._sync()
        activity = Activity.objects.get(strava_id=9001)
        assert activity.best_efforts.filter(target_distance_m=5000).exists()
        assert SyncLog.objects.get().status == SyncLog.Status.SUCCESS

    def test_incremental_sync_looks_a_week_before_the_newest_activity(self):
        # A run uploaded after the last sync, but started before it, must still
        # be fetched, so the cutoff ignores when syncs ran.
        newest_start = datetime.fromisoformat("2026-10-20T07:00:00+00:00")
        make_activity(strava_id=9100, start_date=newest_start).save()
        SyncLog.objects.create(status=SyncLog.Status.SUCCESS, finished_at=timezone.now())
        self.fetch.return_value = []
        self._sync()
        expected = newest_start - timedelta(days=7)
        assert self.fetch.call_args.kwargs["after"] == int(expected.timestamp())

    def test_first_sync_fetches_everything(self):
        self.fetch.return_value = []
        self._sync()
        assert self.fetch.call_args.kwargs["after"] is None

    def test_refetched_activity_in_overlap_is_updated_not_reprocessed(self):
        make_activity(strava_id=9101, best_efforts_checked_at=timezone.now()).save()
        self.fetch.return_value = [strava_activity(9101, kudos_count=5)]
        self._sync()
        log = SyncLog.objects.get()
        assert (log.activities_created, log.activities_updated) == (0, 1)
        assert Activity.objects.get(strava_id=9101).kudos_count == 5
        self.streams.assert_not_called()

    def test_new_walk_gets_no_stream_request(self):
        self.fetch.return_value = [strava_activity(9003, sport_type="Walk")]
        self._sync()
        self.streams.assert_not_called()
        assert SyncLog.objects.get().status == SyncLog.Status.SUCCESS

    def test_stream_failure_makes_run_partial_and_leaves_activity_for_retry(self):
        self.fetch.return_value = [strava_activity(9002)]
        self.streams.side_effect = requests.HTTPError("503 Service Unavailable")
        self._sync()
        log = SyncLog.objects.get()
        assert log.status == SyncLog.Status.PARTIAL
        assert "9002" in log.error_message
        assert Activity.objects.get(strava_id=9002) in activities_for_best_efforts()

    def test_fetch_failure_marks_run_failed_and_raises(self):
        self.fetch.side_effect = requests.HTTPError("500 Server Error")
        with self.assertRaises(CommandError):
            self._sync()
        log = SyncLog.objects.get()
        assert log.status == SyncLog.Status.FAILED
        assert "500" in log.error_message

    def test_run_left_running_by_a_killed_process_is_marked_failed(self):
        stuck = SyncLog.objects.create()
        SyncLog.objects.filter(pk=stuck.pk).update(started_at=timezone.now() - timedelta(hours=5))
        self.fetch.return_value = []
        self._sync()
        stuck.refresh_from_db()
        assert stuck.status == SyncLog.Status.FAILED
        assert "Interrupted" in stuck.error_message


class FetchAchievementsCommandTests(TestCase):
    def setUp(self):
        self.strava_get =self.enterContext(mock.patch("core.strava_client.strava_get"))
        self.activity = make_activity(strava_id=8001, achievement_count=2)
        self.activity.save()

    def _fetch(self):
        call_command("fetch_achievements", stdout=io.StringIO(), stderr=io.StringIO())

    def test_activity_without_segment_achievements_is_not_fetched_again(self):
        # achievement_count also counts best-effort PRs, which have no segment rows.
        self.strava_get.return_value = {"segment_efforts": []}
        self._fetch()
        self._fetch()
        assert self.strava_get.call_count == 1
        assert self.activity not in activities_for_achievements()

    def test_failed_fetch_raises_and_leaves_activity_for_retry(self):
        self.strava_get.side_effect = requests.HTTPError("502 Bad Gateway")
        with self.assertRaises(CommandError):
            self._fetch()
        assert self.activity in activities_for_achievements()

    def test_dead_token_stops_after_first_activity(self):
        make_activity(strava_id=8002, achievement_count=1).save()
        self.strava_get.side_effect = StravaAuthError("token revoked")
        with self.assertRaises(CommandError):
            self._fetch()
        assert self.strava_get.call_count == 1

    def test_used_up_daily_limit_stops_after_first_activity(self):
        make_activity(strava_id=8003, achievement_count=1).save()
        self.strava_get.side_effect = StravaRateLimitError("daily limit used up")
        with self.assertRaises(CommandError):
            self._fetch()
        assert self.strava_get.call_count == 1


# ── UX: pace trend, Strava connection status, local time ─────────────────────

class PaceTrendTests(TestCase):
    def test_small_change_is_steady(self):
        assert _pace_trend(6.0, 6.1) == "steady"     # 1.6 % faster: noise

    def test_clear_changes_have_a_direction(self):
        assert _pace_trend(5.8, 6.1) == "improving"  # 4.9 % faster
        assert _pace_trend(6.4, 6.1) == "declining"  # 4.9 % slower

    def test_missing_pace_gives_no_trend(self):
        assert _pace_trend(None, 6.1) is None

    def test_pace_is_shown_as_minutes_and_seconds(self):
        assert _format_pace(5 + 50 / 60) == "5:50"
        assert _format_pace(5.999) == "6:00"
        assert _format_pace(None) is None


class StravaConnectionTests(TestCase):
    def setUp(self):
        self.token = StravaToken.objects.create(
            athlete_id=1, access_token="secret-access-token-value",
            refresh_token="secret-refresh-token-value",
            expires_at=int(time.time()) - 60,  # access token expired: normal between syncs
        )

    def _fail_sync_with_auth_error(self):
        SyncLog.objects.create(
            status=SyncLog.Status.FAILED,
            error_message=f"Strava refresh token is invalid or revoked (400). "
                          f"Re-run: {REAUTH_COMMAND}",
        )

    def test_expired_access_token_alone_is_still_connected(self):
        assert _strava_connection(self.token) == "connected"

    def test_no_token_is_missing(self):
        assert _strava_connection(None) == "missing"

    def test_failed_sync_with_revoked_token_needs_reconnect(self):
        self._fail_sync_with_auth_error()
        assert _strava_connection(self.token) == "reconnect"

    def test_reconnecting_after_the_failure_clears_the_warning(self):
        self._fail_sync_with_auth_error()
        self.token.save()  # get_strava_token saves a new token pair
        assert _strava_connection(self.token) == "connected"

    def test_other_sync_failures_do_not_ask_for_reconnect(self):
        SyncLog.objects.create(status=SyncLog.Status.FAILED, error_message="500 Server Error")
        assert _strava_connection(self.token) == "connected"

    def test_settings_page_never_shows_token_values(self):
        self.client.force_login(User.objects.create_user("viewer", password="testpass123"))
        content = self.client.get("/settings/").content.decode()
        assert "Connected" in content
        assert "secret-" not in content


class LocalTimeTests(TestCase):
    @override_settings(TIME_ZONE="Africa/Johannesburg")
    def test_best_effort_date_is_the_local_date(self):
        # 23:30 UTC on the 26th is 01:30 on the 27th in Johannesburg.
        late_run = make_activity(
            strava_id=7201, start_date=datetime.fromisoformat("2026-10-26T23:30:00+00:00"),
        )
        late_run.save()
        with mock.patch("core.strava_client.fetch_activity_streams", return_value=FIVE_K_STREAMS):
            compute_and_save(late_run)
        best = BestEffort.objects.filter(activity=late_run).first()
        assert best.achieved_at == date(2026, 10, 27)


# ── Strava client rate limiting ──────────────────────────────────────────────

def strava_response(status=200, read_usage=None, body=None):
    """Build a fake Strava ``requests.Response``.

    Args:
        status: HTTP status code.
        read_usage: ``X-ReadRateLimit-Usage`` value (``"<15-min>,<daily>"``)
            against a limit of 100 per 15 minutes and 1000 per day, or
            ``None`` to send no rate-limit headers.
        body: Decoded JSON body.

    Returns:
        mock.Mock: An object with the parts of ``requests.Response`` the client uses.
    """
    response = mock.Mock(status_code=status)
    response.headers = (
        {"X-ReadRateLimit-Usage": read_usage, "X-ReadRateLimit-Limit": "100,1000"}
        if read_usage else {}
    )
    response.json.return_value = body if body is not None else {}
    response.raise_for_status.side_effect = (
        requests.HTTPError(f"{status} Error") if status >= 400 else None
    )
    return response


class StravaRateLimitTests(TestCase):
    def setUp(self):
        self.enterContext(mock.patch("core.strava_client.get_access_token", return_value="tok"))
        fresh_limiter = strava_client.RateLimiter()
        self.enterContext(mock.patch("core.strava_client.rate_limiter", fresh_limiter))
        self.get = self.enterContext(mock.patch("core.strava_client.requests.get"))
        self.sleep = self.enterContext(mock.patch("core.strava_client.time.sleep"))

    def test_no_pause_while_well_under_the_limit(self):
        self.get.return_value = strava_response(read_usage="10,100")
        strava_client.strava_get("athlete")
        strava_client.strava_get("athlete")
        self.sleep.assert_not_called()

    def test_waits_for_next_window_only_when_another_request_follows(self):
        self.get.return_value = strava_response(read_usage="98,300")
        strava_client.strava_get("athlete")
        self.sleep.assert_not_called()  # a run never ends with a pointless pause
        strava_client.strava_get("athlete")
        self.sleep.assert_called_once()
        assert 0 < self.sleep.call_args.args[0] <= 15 * 60 + 5

    def test_429_waits_for_next_window_then_retries(self):
        self.get.side_effect = [
            strava_response(429, read_usage="100,300"),
            strava_response(body={"id": 1}),
        ]
        assert strava_client.strava_get("activities/1") == {"id": 1}
        assert self.get.call_count == 2
        self.sleep.assert_called_once()

    def test_gives_up_after_repeated_429s(self):
        self.get.return_value = strava_response(429)
        with self.assertRaises(requests.HTTPError):
            strava_client.strava_get("athlete")
        assert self.get.call_count == 3

    def test_used_up_daily_limit_refuses_further_requests_without_calling_strava(self):
        self.get.return_value = strava_response(read_usage="40,999", body={"id": 1})
        assert strava_client.strava_get("activities/1") == {"id": 1}  # this answer still counts
        with self.assertRaises(StravaRateLimitError):
            strava_client.strava_get("activities/2")
        assert self.get.call_count == 1
        self.sleep.assert_not_called()
