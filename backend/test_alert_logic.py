"""Pure unit tests for the price-alert threshold logic (no DB)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import alert_logic


# ── pct_change ──────────────────────────────────────────────────────────────

def test_pct_change_basic():
    assert alert_logic.pct_change(100, 80) == -20.0
    assert alert_logic.pct_change(100, 130) == 30.0


def test_pct_change_zero_baseline_is_safe():
    assert alert_logic.pct_change(0, 50) == 0.0
    assert alert_logic.pct_change(None, 50) == 0.0


# ── is_triggered ────────────────────────────────────────────────────────────

def test_drop_triggers_only_on_fall():
    assert alert_logic.is_triggered("drop", 100, 89, 10) is True   # -11%
    assert alert_logic.is_triggered("drop", 100, 95, 10) is False  # -5%
    assert alert_logic.is_triggered("drop", 100, 120, 10) is False  # rose


def test_rise_triggers_only_on_gain():
    assert alert_logic.is_triggered("rise", 100, 111, 10) is True
    assert alert_logic.is_triggered("rise", 100, 80, 10) is False


def test_either_triggers_both_ways():
    assert alert_logic.is_triggered("either", 100, 88, 10) is True
    assert alert_logic.is_triggered("either", 100, 112, 10) is True
    assert alert_logic.is_triggered("either", 100, 105, 10) is False


def test_exact_threshold_is_inclusive():
    assert alert_logic.is_triggered("drop", 100, 90, 10) is True   # exactly -10%


def test_missing_or_bad_inputs_never_trigger():
    assert alert_logic.is_triggered("drop", None, 90, 10) is False
    assert alert_logic.is_triggered("drop", 100, None, 10) is False
    assert alert_logic.is_triggered("drop", 100, 50, 0) is False   # 0 threshold
    assert alert_logic.is_triggered("drop", 0, 50, 10) is False


# ── build_alert ─────────────────────────────────────────────────────────────

def _entry(**kw):
    base = {"id": 1, "item_type": "card", "item_id": 7, "direction": "drop",
            "threshold_pct": 10.0, "baseline_price": 100.0, "muted": False,
            "note": None}
    base.update(kw)
    return base


def test_build_alert_emits_on_trigger():
    a = alert_logic.build_alert(_entry(), current_price=80)
    assert a is not None
    assert a["pct_change"] == -20.0
    assert a["current_price"] == 80
    assert a["item_id"] == 7


def test_build_alert_silent_when_not_triggered():
    assert alert_logic.build_alert(_entry(), current_price=98) is None


def test_muted_entry_does_not_alert_while_triggered():
    assert alert_logic.build_alert(_entry(muted=True), current_price=80) is None


def test_muted_entry_rearms_when_recovered():
    out = alert_logic.build_alert(_entry(muted=True), current_price=99)
    assert out == {"rearm": True, "id": 1}
