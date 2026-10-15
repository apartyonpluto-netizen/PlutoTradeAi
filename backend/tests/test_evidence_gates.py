from __future__ import annotations

from unittest.mock import patch

import evidence_gates


def _trade(day: str, confidence: int, pnl: float) -> dict:
    return {"entry_date": day, "confidence": confidence, "pnl_percent": pnl, "strategy": "Momentum"}


def test_threshold_is_chosen_on_the_older_period_only():
    older = [_trade(f"2024-01-{day:02d}", 55, 1.0) for day in range(1, 13)]
    mid = [_trade(f"2024-03-{day:02d}", 75, -5.0) for day in range(1, 9)]
    boom = [_trade(f"2024-06-{day:02d}", 80, 50.0) for day in range(1, 9)]
    assert evidence_gates.choose_threshold(older) == 55
    assert evidence_gates.choose_threshold(older + mid + boom) == 80
    verdict = evidence_gates.evaluate_trades(older + boom, "Momentum")
    assert verdict["frozen_threshold"] == 55


def test_unknown_regime_does_not_block_and_a_range_does():
    trend = {"strategy": "Momentum", "recommendation": "CALL", "confidence": 80}
    assert evidence_gates.block_reason({**trend, "market_context": {"rsi_14": 50}}) is None
    blocked = evidence_gates.block_reason({
        **trend,
        "market_context": {"ema_9": 10, "ema_20": 12, "ema_50": 11},
    })
    assert blocked is not None
    assert blocked[1] == "skipped_by_regime"


def test_live_trading_is_not_blocked_by_these_gates(monkeypatch):
    monkeypatch.setenv("PLUTO_EVIDENCE_GATES", "1")
    opportunity = {
        "strategy": "Momentum",
        "recommendation": "CALL",
        "confidence": 90,
        "market_context": {"ema_9": 10, "ema_20": 12, "ema_50": 11},
    }
    with patch.object(evidence_gates, "_live_armed", return_value=True):
        assert evidence_gates.block_reason(opportunity) is None
        assert evidence_gates.evidence_gates_enabled() is False


def test_missing_stored_pass_fails_closed_for_paper(monkeypatch):
    monkeypatch.setenv("PLUTO_EVIDENCE_GATES", "1")
    monkeypatch.setenv("PLUTO_REGIME_FILTER", "0")
    blocked = evidence_gates.block_reason({"strategy": "Momentum", "recommendation": "CALL", "confidence": 90})
    assert blocked is not None
    assert blocked[1] == "backtest_gate"
