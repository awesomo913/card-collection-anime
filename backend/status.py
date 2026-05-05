"""Server status helpers: in-memory log ring buffer + system metrics.

Designed for resource-constrained hosts (Raspberry Pi). Zero filesystem writes
in the hot path; log retention is bounded.
"""
from __future__ import annotations

import logging
import os
import platform
import time
from collections import deque
from datetime import datetime
from typing import Any, Deque, Dict, List, Optional

try:
    import psutil  # optional — gracefully degrades if missing
except Exception:  # pragma: no cover
    psutil = None  # type: ignore[assignment]

# Wall-clock when the process started — used for uptime.
_START_TIME = time.time()

# Last successful run of the price scheduler. Updated by scheduler.py / price_service.
_last_price_update_at: Optional[float] = None
_last_price_update_error: Optional[str] = None

# Surfaced from main._self_heal_schema() — non-fatal schema mismatches the
# auto-heal couldn't fix on its own (NOT NULL columns without defaults, etc).
# The frontend StatusPage renders these as warnings so the user sees them.
_schema_warnings: List[str] = []

# Configured scheduler interval, set by scheduler.start_scheduler() at boot.
_scheduler_interval_seconds: Optional[float] = None


def record_price_update(success: bool, error: Optional[str] = None) -> None:
    """Called by the scheduler so /status can surface its health."""
    global _last_price_update_at, _last_price_update_error
    _last_price_update_at = time.time()
    _last_price_update_error = None if success else (error or "unknown error")


def record_schema_warning(message: str) -> None:
    """Called by main._self_heal_schema() for any non-fatal schema mismatch."""
    _schema_warnings.append(message)


def record_scheduler_interval(seconds: float) -> None:
    """Called by scheduler.start_scheduler() so /status can compute health."""
    global _scheduler_interval_seconds
    _scheduler_interval_seconds = float(seconds)


# ---------- Ring-buffer log handler ----------------------------------------

class RingLogHandler(logging.Handler):
    """Keeps the most recent N log records in memory for /status to expose."""

    def __init__(self, capacity: int = 200) -> None:
        super().__init__()
        self.records: Deque[Dict[str, Any]] = deque(maxlen=capacity)

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D401
        try:
            self.records.append({
                "ts": datetime.utcfromtimestamp(record.created).isoformat() + "Z",
                "level": record.levelname,
                "name": record.name,
                "msg": record.getMessage(),
            })
        except Exception:
            pass  # never let logging crash the request path


_RING = RingLogHandler(capacity=int(os.environ.get("LOG_RING_SIZE", "200")))


def install_ring_handler() -> None:
    """Idempotently attach the ring handler to the root logger."""
    root = logging.getLogger()
    if any(isinstance(h, RingLogHandler) for h in root.handlers):
        return
    _RING.setLevel(logging.INFO)
    root.addHandler(_RING)
    if root.level > logging.INFO or root.level == 0:
        root.setLevel(logging.INFO)


def recent_logs(limit: int = 100, level: Optional[str] = None) -> List[Dict[str, Any]]:
    rows = list(_RING.records)
    if level:
        wanted = level.upper()
        rows = [r for r in rows if r["level"] == wanted]
    return rows[-limit:]


# ---------- System metrics --------------------------------------------------

def system_snapshot() -> Dict[str, Any]:
    """Return a CPU / memory / disk / load snapshot. None-safe when psutil missing."""
    if psutil is None:
        return {
            "cpu_percent": None,
            "memory": None,
            "disk": None,
            "load_avg": None,
            "psutil_available": False,
        }
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    try:
        load = os.getloadavg()
    except (AttributeError, OSError):  # Windows has no loadavg
        load = (0.0, 0.0, 0.0)
    return {
        "cpu_percent": psutil.cpu_percent(interval=None),
        "cpu_count": psutil.cpu_count(logical=True),
        "memory": {
            "total_mb": round(mem.total / 1_048_576, 1),
            "used_mb": round(mem.used / 1_048_576, 1),
            "percent": mem.percent,
        },
        "disk": {
            "total_gb": round(disk.total / 1_073_741_824, 2),
            "used_gb": round(disk.used / 1_073_741_824, 2),
            "percent": disk.percent,
        },
        "load_avg": {"1m": load[0], "5m": load[1], "15m": load[2]},
        "psutil_available": True,
    }


# ---------- Headline status -------------------------------------------------

def overview() -> Dict[str, Any]:
    now = time.time()
    uptime_seconds = max(0, now - _START_TIME)
    last_update_iso = (
        datetime.utcfromtimestamp(_last_price_update_at).isoformat() + "Z"
        if _last_price_update_at else None
    )
    # Scheduler health: stale once the gap since last refresh exceeds 1.5×
    # the configured interval. Surfaces silent daemon death in the UI.
    age = (now - _last_price_update_at) if _last_price_update_at else None
    health = "unknown"
    if _scheduler_interval_seconds and age is not None:
        if age <= _scheduler_interval_seconds:
            health = "healthy"
        elif age <= _scheduler_interval_seconds * 1.5:
            health = "warning"
        else:
            health = "stale"
    return {
        "service": "card-collection-anime",
        "started_at": datetime.utcfromtimestamp(_START_TIME).isoformat() + "Z",
        "uptime_seconds": round(uptime_seconds, 1),
        "hostname": platform.node(),
        "python": platform.python_version(),
        "platform": platform.platform(terse=True),
        "last_price_update_at": last_update_iso,
        "last_price_update_error": _last_price_update_error,
        "last_price_update_age_seconds": round(age, 1) if age is not None else None,
        "scheduler_interval_seconds": _scheduler_interval_seconds,
        "scheduler_health": health,
        "schema_warnings": list(_schema_warnings),
    }
