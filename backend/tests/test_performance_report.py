from __future__ import annotations
from unittest.mock import Mock, patch

import auth
import app as pluto_app
from autonomy.closed_trades import record_closed_trade
from autonomy.performance_report import MIN_SAMPLE_SIZE_FOR_RATES, build_performance_report
from autonomy.research_log import record_research_decision

"""Tier 1 of the "make autonomy learn" roadmap - a reporting-only layer
joining realized outcomes (closed_trades.py) back to the research-log
decision that produced them (research_log.py), broken down by strategy,
confidence bucket, and VIX regime bucket. See performance_report.py's own
module docstring for why this deliberately does NOT feed back into live
trading decisions - that's a separate, later, much bigger undertaking."""


def _closed_trade(trade_id: str, strategy: str, net_realized_pnl: float | None, pnl_status: str = "complete") -> dict:
    return {
        "ticker": "NVDA",
        "entry_client_order_id": trade_id,
        "strategy": strategy,
        "net_realized_pnl": net_realized_pnl,
        "pnl_status": pnl_status,
        "exit_type": "target",
    }


def _research_decision(trade_id: str, raw_confidence: int, vix_level: float | None) -> dict:
    return {
        "entry_client_order_id": trade_id,
        "raw_confidence": raw_confidence,
        "ticker": "NVDA",
        "regime_shadow": {"vix_level": vix_level},
    }


def test_no_closed_trades_returns_empty_report_without_crashing(user_id):
    report = build_performance_report(user_id)
    assert report["total_closed_trades"] == 0
    assert report["overall"]["win_rate_percent"] is None
    assert report["overall"]["total_pnl"] is None
    assert report["by_strategy"] == []
    assert report["by_confidence"] == []
    assert report["by_regime"] == []


def test_a_single_win_and_a_single_loss_are_counted_correctly(user_id):
    record_closed_trade(user_id, "win-1", _closed_trade("win-1", "Momentum", 100.0))
    record_closed_trade(user_id, "loss-1", _closed_trade("loss-1", "Momentum", -40.0))

    report = build_performance_report(user_id)
    assert report["total_closed_trades"] == 2
    assert report["overall"]["wins"] == 1
    assert report["overall"]["losses"] == 1
    assert report["overall"]["win_rate_percent"] == 50.0
    assert report["overall"]["total_pnl"] == 60.0
    assert report["overall"]["avg_pnl"] == 30.0


def test_a_breakeven_trade_counts_toward_pnl_but_is_neither_a_win_nor_a_loss(user_id):
    record_closed_trade(user_id, "scratch-1", _closed_trade("scratch-1", "Momentum", 0.0))
    report = build_performance_report(user_id)
    assert report["overall"]["wins"] == 0
    assert report["overall"]["losses"] == 0
    assert report["overall"]["count"] == 1
    # No decided (win+loss) trades - a rate computed from zero decided
    # trades would be a divide-by-zero lie, not a real 0%.
    assert report["overall"]["win_rate_percent"] is None
    assert report["overall"]["total_pnl"] == 0.0


def test_incomplete_pnl_trades_are_excluded_from_pnl_but_still_counted(user_id):
    record_closed_trade(user_id, "complete-1", _closed_trade("complete-1", "Momentum", 50.0))
    record_closed_trade(user_id, "incomplete-1", _closed_trade("incomplete-1", "Momentum", None, pnl_status="incomplete_missing_fill_price"))

    report = build_performance_report(user_id)
    assert report["total_closed_trades"] == 2
    assert report["incomplete_pnl_count"] == 1
    # The incomplete trade still counts toward the bucket's "count", just not toward P&L/win-loss.
    strategy_row = report["by_strategy"][0]
    assert strategy_row["count"] == 2
    assert strategy_row["total_pnl"] == 50.0
    assert strategy_row["wins"] == 1


def test_strategy_with_no_value_recorded_is_grouped_as_unknown(user_id):
    """Regression coverage for the actual bug this feature found: entry["strategy"]
    was never set in app.py, so every historical closed trade has strategy=None -
    this must degrade to a visible "Unknown" bucket, not crash or silently vanish."""
    record_closed_trade(user_id, "legacy-1", _closed_trade("legacy-1", None, 25.0))
    report = build_performance_report(user_id)
    assert report["by_strategy"][0]["label"] == "Unknown"
    assert report["by_strategy"][0]["count"] == 1


def test_confidence_bucket_boundaries_are_assigned_correctly(user_id):
    record_research_decision(user_id, _research_decision("cid-64", 64, None))
    record_research_decision(user_id, _research_decision("cid-65", 65, None))
    record_research_decision(user_id, _research_decision("cid-99", 99, None))
    record_closed_trade(user_id, "cid-64", _closed_trade("cid-64", "S", 10.0))
    record_closed_trade(user_id, "cid-65", _closed_trade("cid-65", "S", 10.0))
    record_closed_trade(user_id, "cid-99", _closed_trade("cid-99", "S", 10.0))

    report = build_performance_report(user_id)
    labels_by_count = {row["label"]: row["count"] for row in report["by_confidence"]}
    assert labels_by_count.get("55-64") == 1
    assert labels_by_count.get("65-74") == 1
    assert labels_by_count.get("85+") == 1
    # Fixed display order, not alphabetical - "55-64" must appear before "65-74".
    labels_in_order = [row["label"] for row in report["by_confidence"]]
    assert labels_in_order.index("55-64") < labels_in_order.index("65-74") < labels_in_order.index("85+")


def test_a_closed_trade_with_no_matching_research_log_record_buckets_as_unknown_confidence(user_id):
    """An old/legacy trade, or one closed via manual resolution rather than
    the autonomous scan, may have no research-log record at all - must
    degrade to "Unknown", never crash on the missing join."""
    record_closed_trade(user_id, "orphan-1", _closed_trade("orphan-1", "S", 10.0))
    report = build_performance_report(user_id)
    unknown_row = next(row for row in report["by_confidence"] if row["label"] == "Unknown")
    assert unknown_row["count"] == 1


def test_vix_regime_bucket_boundaries_are_assigned_correctly(user_id):
    record_research_decision(user_id, _research_decision("low-vix", 80, 14.9))
    record_research_decision(user_id, _research_decision("normal-vix", 80, 15.0))
    record_research_decision(user_id, _research_decision("elevated-vix", 80, 25.0))
    record_research_decision(user_id, _research_decision("high-vix", 80, 35.0))
    for trade_id in ("low-vix", "normal-vix", "elevated-vix", "high-vix"):
        record_closed_trade(user_id, trade_id, _closed_trade(trade_id, "S", 10.0))

    report = build_performance_report(user_id)
    labels_by_count = {row["label"]: row["count"] for row in report["by_regime"]}
    assert labels_by_count.get("Low (<15)") == 1
    assert labels_by_count.get("Normal (15-25)") == 1
    assert labels_by_count.get("Elevated (25-35)") == 1
    assert labels_by_count.get("High (35+)") == 1


def test_bucket_below_minimum_sample_size_is_flagged_not_hidden(user_id):
    for i in range(MIN_SAMPLE_SIZE_FOR_RATES - 1):
        record_closed_trade(user_id, f"small-{i}", _closed_trade(f"small-{i}", "RareStrategy", 5.0))

    report = build_performance_report(user_id)
    row = next(row for row in report["by_strategy"] if row["label"] == "RareStrategy")
    assert row["sufficient_sample"] is False
    # Still computed and shown, just flagged - not suppressed to None.
    assert row["win_rate_percent"] == 100.0


def test_bucket_at_minimum_sample_size_is_marked_sufficient(user_id):
    for i in range(MIN_SAMPLE_SIZE_FOR_RATES):
        record_closed_trade(user_id, f"enough-{i}", _closed_trade(f"enough-{i}", "CommonStrategy", 5.0))

    report = build_performance_report(user_id)
    row = next(row for row in report["by_strategy"] if row["label"] == "CommonStrategy")
    assert row["sufficient_sample"] is True


def test_strategy_breakdown_is_sorted_alphabetically(user_id):
    record_closed_trade(user_id, "z-1", _closed_trade("z-1", "Zeta", 1.0))
    record_closed_trade(user_id, "a-1", _closed_trade("a-1", "Alpha", 1.0))
    report = build_performance_report(user_id)
    labels = [row["label"] for row in report["by_strategy"]]
    assert labels == sorted(labels)
    assert labels[0] == "Alpha"


def test_performance_page_renders_with_no_closed_trades(user_id):
    """Catches a Jinja syntax error the pure aggregation tests above would
    never see, and confirms the empty state doesn't crash the page."""
    user = auth.register_user(f"perf-{user_id[:8]}", "TestPassword123!")
    auth.approve_user(user["id"])
    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = user["id"]
        response = client.get("/performance")
    body = response.data.decode("utf-8")
    assert response.status_code == 200
    assert "Performance Report" in body
    assert "No closed trades in this breakdown yet." in body


def test_performance_page_renders_a_real_breakdown_row(user_id):
    user = auth.register_user(f"perfdata-{user_id[:8]}", "TestPassword123!")
    auth.approve_user(user["id"])
    record_closed_trade(user["id"], "render-1", _closed_trade("render-1", "Momentum", 42.5))

    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = user["id"]
        body = client.get("/performance").data.decode("utf-8")
    assert "Momentum" in body
    assert "42.50" in body


def test_performance_page_never_triggers_the_market_scan(user_id):
    """Found live: this page took ~21s to load in production because
    get_market_data() (the CORE_SCAN_UNIVERSE yfinance fetch, up to its
    own 20s hard deadline) ran unconditionally regardless of
    include_opportunities - for scanner_rows this page never reads.
    Proves the fix actually skips the call rather than just documenting
    the intent."""
    user = auth.register_user(f"perfscan-{user_id[:8]}", "TestPassword123!")
    auth.approve_user(user["id"])
    mock_scan = Mock(return_value=([], [], ""))
    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = user["id"]
        with patch.object(pluto_app, "get_market_data", mock_scan):
            response = client.get("/performance")
    assert response.status_code == 200
    mock_scan.assert_not_called()
