"""Single-run lock and background launcher for the full Strava sync.

``manage.py sync`` holds the lock for its whole run, so the scheduled task and
the "Sync now" button can never start overlapping syncs.  The lock is a file
created atomically; one older than ``STALE_AFTER_S`` is assumed to be left
behind by a crashed run and is replaced.
"""
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from django.conf import settings

LOCK_PATH = Path(settings.BASE_DIR) / ".sync.lock"
LOG_PATH = Path(settings.BASE_DIR) / "sync.log"
STALE_AFTER_S = 3 * 60 * 60


class SyncAlreadyRunning(Exception):
    """Raised when another sync already holds the lock."""


def sync_in_progress() -> bool:
    """Return True if a sync holds the lock and the lock is not stale.

    Returns:
        Whether a sync is currently running.
    """
    try:
        age_s = time.time() - LOCK_PATH.stat().st_mtime
    except FileNotFoundError:
        return False
    return age_s < STALE_AFTER_S


@contextmanager
def sync_lock():
    """Hold the sync lock for the duration of the ``with`` block.

    Yields:
        None.

    Raises:
        SyncAlreadyRunning: If another sync holds a lock that is not stale.
    """
    if sync_in_progress():
        raise SyncAlreadyRunning("Another sync is already running.")
    LOCK_PATH.unlink(missing_ok=True)  # absent, or stale from a crashed run
    try:
        with LOCK_PATH.open("x", encoding="utf-8") as lock:
            lock.write(str(os.getpid()))
    except FileExistsError as exc:
        raise SyncAlreadyRunning("Another sync is already running.") from exc
    try:
        yield
    finally:
        LOCK_PATH.unlink(missing_ok=True)


def start_background_sync() -> None:
    """Launch ``manage.py sync`` as a detached process, appending its output to ``sync.log``."""
    detach = (
        {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
        if sys.platform == "win32"
        else {"start_new_session": True}
    )
    with LOG_PATH.open("a", encoding="utf-8") as log:
        # Deliberately not waited on: the sync outlives this request.
        subprocess.Popen(  # pylint: disable=consider-using-with
            [sys.executable, str(Path(settings.BASE_DIR) / "manage.py"), "sync"],
            cwd=settings.BASE_DIR,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            env={**os.environ, "PYTHONUTF8": "1"},
            **detach,
        )
