from __future__ import annotations

from autonomy.closed_trades import record_closed_trade
from autonomy.outcomes_analysis import MIN_SAMPLE_SIZE_FOR_RATES, build_outcomes_analysis
from autonomy.research_log import record_research_decision

"""autonomy/outcomes_analysis.py: joins closed_trades.py back to the
research_log.py signal_snapshot recorded for that entry (the memory layer
added 2026-09-11) and reports whether strategy_brain's own technical
picture at entry (RSI, EMA stack, relative volume, price vs VWAP)
correlated with the real outcome. Reporting-only - see the module's own
docstring for why this never feeds a live decision."""


def _closed_trade(trade_id: str, net_realized_pnl: float | None, pnl_status: str = "complete") -> dict:
    return {
        "ticker": "NVDA",
        "entry_client_order_id": trade_id,
        "strategy": "Momentum",
        "net_realized_pnl": net_realized_pnl,
        "pnl_status": pnl_status,
        "exit_type": "target",
    }


def _research_decision(trade_id: str, market_context: dict | None) -> dict:
    return {
        "entry_client_order_id": trade_id,
        "raw_confidence": 80,
        "ticker": "NVDA",
        "signal_snapshot": {"market_context": market_context, "strategies_evaluated": []} if market_context is not None else None,
    }


def _market_context(**overrides) -> dict:
    base = {
        "current_price": 100.0,
        "rsi_14": 50.0,
        "relative_volume": 1.5,
        "vwap": 100.0,
        "ema_9": 100.0,
        "ema_20": 99.0,
        "ema_50": 98.0,
    }
    base.update(overrides)
    return base


def test_no_closed_trades_returns_empty_report_without_crashing(user_id):
    report = build_outcomes_analysis(user_id)
    assert report["total_closed_trades"] == 0
    assert report["overall"]["win_rate_percent"] is None
    assert report["by_rsi_at_entry"] == []
    assert report["by_ema_stack_alignment"] == []
    assert report["by_relative_volume"] == []
    assert report["by_price_vs_vwap"] == []
    assert report["signal_snapshot_available_count"] == 0
    assert report["not_yet_captured"] == ["candle_brain", "pattern_brain", "neural_engine"]


def test_a_closed_trade_with_no_matching_research_log_record_buckets_as_unknown_everywhere(user_id):
    # A legacy trade closed before signal_snapshot existed, or one closed
    # via manual resolution with no research-log record at all - must
    # degrade to "Unknown" buckets, never crash on the missing join.
    record_closed_trade(user_id, "orphan-1", _closed_trade("orphan-1", 10.0))
    report = build_outcomes_analysis(user_id)
    assert report["total_closed_trades"] == 1
    assert report["signal_snapshot_available_count"] == 0
    assert report["by_rsi_at_entry"][0]["label"] == "Unknown"
    assert report["by_rsi_at_entry"][0]["count"] == 1
    assert report["by_ema_stack_alignment"][0]["label"] == "Unknown"
    assert report["by_relative_volume"][0]["label"] == "Unknown"
    assert report["by_price_vs_vwap"][0]["label"] == "Unknown"


def test_a_research_record_with_no_signal_snapshot_also_buckets_as_unknown(user_id):
    # A record written under schema_version 1, before signal_snapshot was
    # added - present, but with signal_snapshot=None.
    record_research_decision(user_id, _research_decision("legacy-1", None))
    record_closed_trade(user_id, "legacy-1", _closed_trade("legacy-1", 10.0))
    report = build_outcomes_analysis(user_id)
    assert report["signal_snapshot_available_count"] == 0
    assert report["by_rsi_at_entry"][0]["label"] == "Unknown"


def test_rsi_bucket_boundaries_are_assigned_correctly(user_id):
    record_research_decision(user_id, _research_decision("oversold", _market_context(rsi_14=29.9)))
    record_research_decision(user_id, _research_decision("neutral", _market_context(rsi_14=30.0)))
    record_research_decision(user_id, _research_decision("overbought", _market_context(rsi_14=70.0)))
    for trade_id in ("oversold", "neutral", "overbought"):
        record_closed_trade(user_id, trade_id, _closed_trade(trade_id, 5.0))

    report = build_outcomes_analysis(user_id)
    labels_by_count = {row["label"]: row["count"] for row in report["by_rsi_at_entry"]}
    assert labels_by_count.get("Oversold (<30)") == 1
    assert labels_by_count.get("Neutral (30-70)") == 1
    assert labels_by_count.get("Overbought (70+)") == 1
    assert report["signal_snapshot_available_count"] == 3


def test_ema_stack_alignment_is_labeled_correctly(user_id):
    record_research_decision(user_id, _research_decision("bull", _market_context(ema_9=110, ema_20=105, ema_50=100)))
    record_research_decision(user_id, _research_decision("bear", _market_context(ema_9=90, ema_20=95, ema_50=100)))
    record_research_decision(user_id, _research_decision("mixed", _market_context(ema_9=100, ema_20=110, ema_50=105)))
    for trade_id in ("bull", "bear", "mixed"):
        record_closed_trade(user_id, trade_id, _closed_trade(trade_id, 5.0))

    report = build_outcomes_analysis(user_id)
    labels_by_count = {row["label"]: row["count"] for row in report["by_ema_stack_alignment"]}
    assert labels_by_count.get("Bullish stack (9>20>50)") == 1
    assert labels_by_count.get("Bearish stack (9<20<50)") == 1
    assert labels_by_count.get("Mixed") == 1


def test_relative_volume_bucket_boundaries_are_assigned_correctly(user_id):
    record_research_decision(user_id, _research_decision("low", _market_context(relative_volume=0.9)))
    record_research_decision(user_id, _research_decision("elevated", _market_context(relative_volume=1.0)))
    record_research_decision(user_id, _research_decision("high", _market_context(relative_volume=2.0)))
    for trade_id in ("low", "elevated", "high"):
        record_closed_trade(user_id, trade_id, _closed_trade(trade_id, 5.0))

    report = build_outcomes_analysis(user_id)
    labels_by_count = {row["label"]: row["count"] for row in report["by_relative_volume"]}
    assert labels_by_count.get("Below average (<1.0x)") == 1
    assert labels_by_count.get("Elevated (1.0-2.0x)") == 1
    assert labels_by_count.get("High (2.0x+)") == 1


def test_price_vs_vwap_is_labeled_correctly(user_id):
    record_research_decision(user_id, _research_decision("above", _market_context(current_price=105.0, vwap=100.0)))
    record_research_decision(user_id, _research_decision("below", _market_context(current_price=95.0, vwap=100.0)))
    record_research_decision(user_id, _research_decision("at", _market_context(current_price=100.0, vwap=100.0)))
    for trade_id in ("above", "below", "at"):
        record_closed_trade(user_id, trade_id, _closed_trade(trade_id, 5.0))

    report = build_outcomes_analysis(user_id)
    labels_by_count = {row["label"]: row["count"] for row in report["by_price_vs_vwap"]}
    assert labels_by_count.get("Above VWAP") == 1
    assert labels_by_count.get("Below VWAP") == 1
    assert labels_by_count.get("At VWAP") == 1


def test_bucket_below_minimum_sample_size_is_flagged_not_hidden(user_id):
    for i in range(MIN_SAMPLE_SIZE_FOR_RATES - 1):
        trade_id = f"small-{i}"
        record_research_decision(user_id, _research_decision(trade_id, _market_context(rsi_14=50.0)))
        record_closed_trade(user_id, trade_id, _closed_trade(trade_id, 5.0))

    report = build_outcomes_analysis(user_id)
    row = next(row for row in report["by_rsi_at_entry"] if row["label"] == "Neutral (30-70)")
    assert row["sufficient_sample"] is False
    assert row["win_rate_percent"] == 100.0  # still computed and shown, just flagged


def test_bucket_at_minimum_sample_size_is_marked_sufficient(user_id):
    for i in range(MIN_SAMPLE_SIZE_FOR_RATES):
        trade_id = f"enough-{i}"
        record_research_decision(user_id, _research_decision(trade_id, _market_context(rsi_14=50.0)))
        record_closed_trade(user_id, trade_id, _closed_trade(trade_id, 5.0))

    report = build_outcomes_analysis(user_id)
    row = next(row for row in report["by_rsi_at_entry"] if row["label"] == "Neutral (30-70)")
    assert row["sufficient_sample"] is True


def test_today_on_this_accounts_real_history_every_bucket_is_honestly_insufficient(user_id):
    # This account's real closed-trade history (4 trades, per the
    # 2026-09-10 production diagnosis) predates signal_snapshot entirely -
    # the honest expected result is sufficient_sample=False everywhere,
    # not a report that fools itself into showing confident-looking rates
    # off a handful of trades.
    for i in range(4):
        record_closed_trade(user_id, f"real-{i}", _closed_trade(f"real-{i}", 10.0))
    report = build_outcomes_analysis(user_id)
    assert report["total_closed_trades"] == 4
    for row in report["by_rsi_at_entry"] + report["by_ema_stack_alignment"] + report["by_relative_volume"] + report["by_price_vs_vwap"]:
        assert row["sufficient_sample"] is False
