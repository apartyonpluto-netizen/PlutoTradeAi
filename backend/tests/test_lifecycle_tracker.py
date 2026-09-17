from __future__ import annotations

import order_lifecycle as ol
from autonomy.closed_trades import record_closed_trade
from autonomy.lifecycle_tracker import (
    FIXES_COMPLETE_AT,
    READINESS_TARGET_CLEAN_TRADES,
    build_lifecycle_summary,
)
from autonomy.overnight_orders import record_overnight_order

"""autonomy/lifecycle_tracker.py: answers the 2026-09-11 readiness question
("has a trade ever completed found -> entered -> filled -> protected ->
exited by stop/target cleanly?") off real closed_trades.py/overnight_orders.py
data instead of by hand each time. See [[production-trading-state]]."""

AFTER_FIXES = "2026-09-14T00:00:00+00:00"  # > FIXES_COMPLETE_AT
BEFORE_FIXES = "2026-09-05T00:00:00+00:00"  # < FIXES_COMPLETE_AT


def _clean_entry(trade_id: str, **extra) -> dict:
    """The happy path: submitted -> filled -> protection pending ->
    protection confirmed -> closed, no detours."""
    entry: dict = {"ticker": "NVDA"}
    ol.initialize(entry, ol.ENTRY_SUBMITTED, entry_client_order_id=trade_id)
    ol.transition(entry, ol.ENTRY_FILLED, filled_quantity=10.0)
    ol.transition(entry, ol.PROTECTION_PENDING)
    ol.transition(entry, ol.PROTECTION_CONFIRMED_ACTIVE)
    ol.transition(entry, ol.CLOSED, closed_trade_id=trade_id)
    entry.update(extra)
    return entry


def _recovered_entry(trade_id: str, **extra) -> dict:
    """Detoured through PROTECTION_FAILED before eventually closing - a
    real, working recovery (see the 2026-09-10 reconciliation fix), but not
    a clean run."""
    entry: dict = {"ticker": "SLB"}
    ol.initialize(entry, ol.ENTRY_SUBMITTED, entry_client_order_id=trade_id)
    ol.transition(entry, ol.ENTRY_FILLED, filled_quantity=10.0)
    ol.transition(entry, ol.PROTECTION_PENDING)
    ol.transition(entry, ol.PROTECTION_FAILED, error="could not confirm protection active")
    ol.transition(entry, ol.PROTECTION_PENDING)
    ol.transition(entry, ol.PROTECTION_CONFIRMED_ACTIVE)
    ol.transition(entry, ol.CLOSED, closed_trade_id=trade_id)
    entry.update(extra)
    return entry


def _closed_trade(
    trade_id: str,
    entry_timestamp: str = AFTER_FIXES,
    close_reason: str = "target_exit_executed",
    pnl_status: str = "complete",
    net_realized_pnl: float | None = 25.0,
) -> dict:
    return {
        "ticker": "NVDA",
        "entry_client_order_id": trade_id,
        "entry_timestamp": entry_timestamp,
        "exit_timestamp": "2026-09-15T00:00:00+00:00",
        "close_reason": close_reason,
        "pnl_status": pnl_status,
        "net_realized_pnl": net_realized_pnl,
    }


def test_no_closed_trades_returns_empty_report_without_crashing(user_id):
    report = build_lifecycle_summary(user_id)
    assert report["total_closed_trades"] == 0
    assert report["clean_lifecycle_count"] == 0
    assert report["clean_lifecycle_rate_percent"] is None
    assert report["since_fixes_total"] == 0
    assert report["since_fixes_clean_count"] == 0
    assert report["since_fixes_clean_rate_percent"] is None
    assert report["readiness_target_clean_trades"] == READINESS_TARGET_CLEAN_TRADES
    assert report["readiness_progress_percent"] == 0.0
    assert report["fixes_complete_at"] == FIXES_COMPLETE_AT
    assert report["trades"] == []


def test_a_genuinely_clean_trade_since_fixes_counts_as_clean(user_id):
    record_overnight_order(user_id, _clean_entry("clean-1"))
    record_closed_trade(user_id, "clean-1", _closed_trade("clean-1"))

    report = build_lifecycle_summary(user_id)
    assert report["total_closed_trades"] == 1
    assert report["clean_lifecycle_count"] == 1
    assert report["clean_lifecycle_rate_percent"] == 100.0
    assert report["since_fixes_total"] == 1
    assert report["since_fixes_clean_count"] == 1
    assert report["since_fixes_clean_rate_percent"] == 100.0
    row = report["trades"][0]
    assert row["is_clean"] is True
    assert row["disqualifying_reasons"] == []


def test_a_trade_that_detoured_through_protection_failed_is_not_clean(user_id):
    record_overnight_order(user_id, _recovered_entry("recovered-1"))
    record_closed_trade(user_id, "recovered-1", _closed_trade("recovered-1"))

    report = build_lifecycle_summary(user_id)
    assert report["clean_lifecycle_count"] == 0
    row = report["trades"][0]
    assert row["is_clean"] is False
    assert any("protection_failed" in reason for reason in row["disqualifying_reasons"])


def test_manual_reconciliation_close_reason_is_not_clean_even_with_a_pristine_history(user_id):
    # Edge case: the overnight_orders entry itself never touched a bad
    # state (e.g. it was flagged position_absent_unexplained and the flag
    # later self-healed, wiping the field - see
    # _self_heal_stale_position_absent_flag), but the close_reason on
    # record shows a human resolved it, not a real stop/target fill.
    record_overnight_order(user_id, _clean_entry("manual-1"))
    record_closed_trade(
        user_id, "manual-1", _closed_trade("manual-1", close_reason="manual_reconciliation_position_absent")
    )

    report = build_lifecycle_summary(user_id)
    assert report["clean_lifecycle_count"] == 0
    reasons = report["trades"][0]["disqualifying_reasons"]
    assert any("not a self-executed stop/target/expiration exit" in r for r in reasons)


def test_incomplete_pnl_status_is_not_clean(user_id):
    record_overnight_order(user_id, _clean_entry("incomplete-1"))
    record_closed_trade(
        user_id, "incomplete-1", _closed_trade("incomplete-1", pnl_status="incomplete_missing_fill_price")
    )

    report = build_lifecycle_summary(user_id)
    assert report["clean_lifecycle_count"] == 0
    reasons = report["trades"][0]["disqualifying_reasons"]
    assert any("fill price was never confirmed" in r for r in reasons)


def test_position_absent_unexplained_flag_disqualifies_even_with_a_clean_close_reason(user_id):
    entry = _clean_entry("flagged-1")
    entry["position_absent_unexplained"] = True
    record_overnight_order(user_id, entry)
    record_closed_trade(user_id, "flagged-1", _closed_trade("flagged-1"))

    report = build_lifecycle_summary(user_id)
    assert report["clean_lifecycle_count"] == 0
    reasons = report["trades"][0]["disqualifying_reasons"]
    assert any("position_absent_unexplained" in r for r in reasons)


def test_closed_trade_with_no_matching_overnight_order_fails_closed_not_clean(user_id):
    # A legacy trade, or one whose overnight_orders record was never
    # written for some reason - lifecycle history is genuinely unknown, so
    # this must never be assumed clean just because closed_trades.py alone
    # looks fine.
    record_closed_trade(user_id, "orphan-1", _closed_trade("orphan-1"))

    report = build_lifecycle_summary(user_id)
    assert report["clean_lifecycle_count"] == 0
    reasons = report["trades"][0]["disqualifying_reasons"]
    assert any("no matching overnight_orders entry" in r for r in reasons)


def test_a_trade_entered_before_the_fixes_is_excluded_from_since_fixes_even_if_clean(user_id):
    record_overnight_order(user_id, _clean_entry("old-1"))
    record_closed_trade(user_id, "old-1", _closed_trade("old-1", entry_timestamp=BEFORE_FIXES))

    report = build_lifecycle_summary(user_id)
    assert report["clean_lifecycle_count"] == 1  # still counts all-time
    assert report["since_fixes_total"] == 0
    assert report["since_fixes_clean_count"] == 0
    assert report["since_fixes_clean_rate_percent"] is None
    assert report["trades"][0]["is_since_fixes"] is False


def test_readiness_progress_percent_scales_to_the_target_and_caps_at_100(user_id):
    for i in range(READINESS_TARGET_CLEAN_TRADES + 5):
        trade_id = f"clean-{i}"
        record_overnight_order(user_id, _clean_entry(trade_id))
        record_closed_trade(user_id, trade_id, _closed_trade(trade_id))

    report = build_lifecycle_summary(user_id)
    assert report["since_fixes_clean_count"] == READINESS_TARGET_CLEAN_TRADES + 5
    assert report["readiness_progress_percent"] == 100.0


def test_todays_real_history_is_honestly_not_ready(user_id):
    # This account's real closed-trade history (4 trades, all pre-fixes,
    # per the 2026-09-10 production diagnosis) must not be misread as
    # progress toward readiness just because it has some closed trades.
    for i in range(4):
        record_closed_trade(user_id, f"real-{i}", _closed_trade(f"real-{i}", entry_timestamp=BEFORE_FIXES))

    report = build_lifecycle_summary(user_id)
    assert report["total_closed_trades"] == 4
    assert report["since_fixes_total"] == 0
    assert report["since_fixes_clean_count"] == 0
    assert report["readiness_progress_percent"] == 0.0
