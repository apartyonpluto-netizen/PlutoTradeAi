from __future__ import annotations

from pathlib import Path

import daily_loss


def test_combined_day_pnl_adds_cash_and_margin():
    cash = {"total_day_profit_loss": -40}
    margin = {"total_day_profit_loss": -15.5}
    assert daily_loss.combined_day_pnl(cash, margin) == -55.5


def test_combined_day_pnl_ignores_a_missing_margin_account():
    cash = {"total_day_profit_loss": -12.25}
    assert daily_loss.combined_day_pnl(cash, None) == -12.25
    assert daily_loss.combined_day_pnl(None) == 0.0


def test_evaluate_halts_at_the_scan_threshold():
    # 5% of 2000 is 100. The old _is_daily_loss_limit_hit cases:
    state = daily_loss.evaluate(-100, 2000, 5)
    assert state["halted"] is True
    assert state["cap_dollars"] == 100
    assert state["source"] == "broker_total_day_profit_loss"
    assert state["message"] == (
        "Daily loss limit reached (today's P/L $-100.00 vs -$100.00 limit, "
        "5.0% of $2,000.00 balance). No new trades until tomorrow."
    )
    assert daily_loss.evaluate(-50, 2000, 5)["halted"] is False
    assert daily_loss.evaluate(-150, 2000, 5)["halted"] is True
    assert daily_loss.evaluate(300, 2000, 5)["halted"] is False
    assert daily_loss.evaluate(-999999, 2000, 0)["halted"] is False
    assert daily_loss.evaluate(-10, 0, 5)["halted"] is False


def test_home_meter_does_not_mix_in_local_closed_trades():
    template = Path(__file__).resolve().parents[1] / "templates" / "home_status_bar.html"
    text = template.read_text(encoding="utf-8")
    assert "loss_cap" in text
    assert "loss_today_dollars" not in text
    assert "broker_day" not in text
    assert "Today after fees" in text
