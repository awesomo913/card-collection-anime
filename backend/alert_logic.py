"""Pure price-alert evaluation — no DB, no I/O, fully unit-testable.

`crud.evaluate_watchlist` pulls the baseline + latest price from the DB and
feeds them here. Keeping the math pure means the threshold rules can be tested
in isolation without a database or price provider.
"""
from __future__ import annotations

from typing import Literal

Direction = Literal["drop", "rise", "either"]


def pct_change(baseline: float, current: float) -> float:
    """Signed percent change from baseline to current. 0 if baseline is 0/None."""
    if not baseline:
        return 0.0
    return (current - baseline) / baseline * 100.0


def is_triggered(
    direction: Direction,
    baseline: float | None,
    current: float | None,
    threshold_pct: float,
) -> bool:
    """True when the price move crosses the threshold in the watched direction.

    - ``drop``   triggers when the price fell by at least threshold_pct.
    - ``rise``   triggers when the price rose by at least threshold_pct.
    - ``either`` triggers on a move of at least threshold_pct in either direction.
    Missing baseline/current or a non-positive threshold never triggers.
    """
    if baseline is None or current is None or not baseline or threshold_pct <= 0:
        return False
    change = pct_change(baseline, current)
    if direction == "drop":
        return change <= -threshold_pct
    if direction == "rise":
        return change >= threshold_pct
    # 'either'
    return abs(change) >= threshold_pct


def build_alert(entry: dict, current_price: float | None) -> dict | None:
    """Return an alert dict if ``entry`` is triggered and not muted, else None.

    ``entry`` is a plain dict of the watchlist row's fields. A muted entry that
    is no longer triggered is reported as ``rearm=True`` so the caller can clear
    the mute (next genuine cross fires again).
    """
    baseline = entry.get("baseline_price")
    direction = entry.get("direction", "drop")
    threshold = entry.get("threshold_pct", 10.0)
    triggered = is_triggered(direction, baseline, current_price, threshold)

    if entry.get("muted"):
        # Still triggered → stay muted (no alert). No longer triggered → re-arm.
        return {"rearm": True, "id": entry["id"]} if not triggered else None

    if not triggered:
        return None

    change = pct_change(baseline, current_price)
    return {
        "id": entry["id"],
        "item_type": entry["item_type"],
        "item_id": entry["item_id"],
        "direction": direction,
        "threshold_pct": threshold,
        "baseline_price": baseline,
        "current_price": current_price,
        "pct_change": round(change, 2),
        "note": entry.get("note"),
    }
