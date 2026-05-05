"""Background price-refresh scheduler.

Design notes:

Phase B (resilience):
- Uses ``threading.Event.wait(timeout)`` instead of ``time.sleep`` so the loop
  is interruptible — ``trigger_refresh_now()`` can wake it on demand.
- The outer loop is wrapped in a try/except that NEVER lets the thread die.
  A silent daemon death used to mean the Pi would stop refreshing forever
  while everything else looked healthy. Any uncaught exception is logged
  + recorded to status_module + the loop continues to the next tick.
- Reentrancy guard: a ``threading.Lock`` around ``update_all_prices()`` so
  if a refresh somehow runs longer than the interval, the next tick skips
  rather than overlapping (which would race on PriceHistory inserts).

Phase E (single-instance + cadence):
- Single-instance flock on ``~/.cache/card-collection-scheduler.lock``. If
  two uvicorns are running (e.g. dev + Pi behind a tunnel), only the first
  acquires the lock; the second still serves API requests but doesn't run
  a duplicate scheduler. Prevents duplicate API calls + DB write races.
- Default interval lowered to 6h (4×/day) — matches the user-facing config
  in ``deploy/pi-run-nosudo.sh``.
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Optional

from price_service import update_all_prices

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_scheduler_thread: Optional[threading.Thread] = None
_lock_file_handle = None  # held for process lifetime when scheduler owns the slot
_refresh_lock = threading.Lock()
_wake = threading.Event()


def _acquire_single_instance_lock() -> bool:
    """Try to claim the single-instance lock. True = we own the scheduler slot.

    Uses fcntl.flock on POSIX. On Windows (dev), the call is a no-op and we
    proceed (single-instance assumed locally). The lock is held for the
    process lifetime — releasing it would let a second scheduler start.
    """
    global _lock_file_handle
    try:
        lock_dir = Path.home() / ".cache"
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock_path = lock_dir / "card-collection-scheduler.lock"
        _lock_file_handle = open(lock_path, "w")
        try:
            import fcntl
        except ImportError:
            logger.info("fcntl unavailable (likely Windows) — skipping flock")
            return True
        try:
            fcntl.flock(_lock_file_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            logger.warning(
                "Scheduler lock held by another process (pid in lock file). "
                "This instance will serve API requests only, no refresh."
            )
            _lock_file_handle.close()
            _lock_file_handle = None
            return False
        _lock_file_handle.write(f"{os.getpid()}\n")
        _lock_file_handle.flush()
        return True
    except Exception as exc:  # noqa: BLE001 — never fail boot on a lock issue
        logger.error("Lock acquisition error (%s) — proceeding without lock", exc)
        return True


def run_scheduler(interval_hours: int = 24) -> threading.Thread:
    """Start the background refresh thread. Returns the thread object."""
    from status import record_price_update, record_scheduler_interval

    interval_seconds = float(interval_hours) * 60.0 * 60.0
    record_scheduler_interval(interval_seconds)

    def _refresh_once() -> None:
        """One refresh tick. The reentrancy lock ensures two ticks never race.

        If the lock is held (previous tick still running), skip this tick
        rather than queueing — better to drop a refresh than pile up overlaps.
        """
        if not _refresh_lock.acquire(blocking=False):
            logger.warning(
                "Previous price refresh still running — skipping this tick to avoid overlap"
            )
            return
        try:
            logger.info("Starting scheduled price update")
            update_all_prices()
            logger.info("Scheduled price update completed")
            record_price_update(True)
        except Exception as exc:  # noqa: BLE001 — log + survive, never let thread die
            logger.exception("Price update failed: %s", exc)
            record_price_update(False, str(exc))
        finally:
            _refresh_lock.release()

    def _scheduler_loop() -> None:
        # Refresh once on boot so /status has fresh data quickly, then loop.
        while True:
            try:
                _refresh_once()
            except Exception as exc:  # noqa: BLE001 — defense in depth
                logger.exception("Scheduler outer loop caught (will retry): %s", exc)
            # Wait on an Event (interruptible) instead of time.sleep so a
            # ``trigger_refresh_now()`` call can break the wait early.
            _wake.wait(timeout=interval_seconds)
            _wake.clear()

    thread = threading.Thread(target=_scheduler_loop, daemon=True, name="price-scheduler")
    thread.start()
    logger.info("Price update scheduler started (interval: %s hours)", interval_hours)
    return thread


def start_scheduler() -> Optional[threading.Thread]:
    """Public entry point used by main.py's lifespan.

    Honors the ``PRICE_UPDATE_INTERVAL_HOURS`` env var; defaults to 6h (4×/day).
    Returns the thread on success, ``None`` if another process owns the
    single-instance lock — the API still serves, just no refresh here.
    """
    global _scheduler_thread

    raw_hours = os.getenv("PRICE_UPDATE_INTERVAL_HOURS")
    if raw_hours:
        try:
            interval = int(raw_hours)
        except ValueError:
            logger.warning(
                "Invalid PRICE_UPDATE_INTERVAL_HOURS=%r — falling back to 6h", raw_hours
            )
            interval = 6
    else:
        interval = 6

    if not _acquire_single_instance_lock():
        # Other instance owns the scheduler slot. Still record the configured
        # interval so /status can compute health relative to whatever the
        # other instance is running.
        from status import record_scheduler_interval
        record_scheduler_interval(float(interval) * 3600.0)
        return None

    _scheduler_thread = run_scheduler(interval_hours=interval)
    return _scheduler_thread


def trigger_refresh_now() -> None:
    """Wake the scheduler immediately (e.g. for a manual /prices/update kick)."""
    _wake.set()
