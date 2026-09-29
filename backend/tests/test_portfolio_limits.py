"""Portfolio limits: largest position, total exposure, one sector, and a
drawdown pause. All off until set; total and sector limits size a trade down
to fit rather than only vetoing it; holdings the agent didn't open still
count; trades earlier in the same scan count."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

import app as pluto_app
import auth
import order_lifecycle as ol
import portfolio_limits as pl
from autonomy import trade_tickets as tt
from autonomy.autonomous_controller import get_autonomy_status, set_mode, update_risk_settings

CREDS = {"app_key": "key", "app_secret": "secret"}
ACCOUNT_ID = "acct-1"


# --- pure rules -------------------------------------------------------------------


def test_sectors_and_option_underlyings():
    assert pl.sector_for("nvda") == "Technology"
    assert pl.sector_for("XLE") == "Energy"
    assert pl.sector_for("SPY") == pl.BROAD_MARKET
    assert pl.sector_for("ZZZZ") == pl.UNKNOWN_SECTOR
    assert pl.underlying_symbol({"symbol": "NVDA260918C00105000"}) == "NVDA"
    assert pl.underlying_symbol({"symbol": "AAPL"}) == "AAPL"
    assert pl.underlying_symbol({"symbol": "X", "underlying_symbol": "msft"}) == "MSFT"


def test_exposure_counts_positions_and_working_buys_but_not_exits():
    snapshot = pl.exposure_snapshot(
        [{"symbol": "AAPL", "market_value": "5000"}, {"symbol": "NVDA260918C00105000", "market_value": "300"},
         {"symbol": "XOM", "quantity": "10", "last_price": "110"}],
        [{"symbol": "MSFT", "side": "BUY", "total_quantity": "10", "filled_quantity": "4", "limit_price": "400"},
         {"symbol": "AAPL", "side": "SELL", "total_quantity": "10", "limit_price": "190"},
         {"symbol": "AMD", "side": "BUY", "total_quantity": "2", "limit_price": "3", "instrument_type": "OPTION"}],
    )
    assert snapshot["by_ticker"] == {"AAPL": 5000.0, "NVDA": 300.0, "XOM": 1100.0, "MSFT": 2400.0, "AMD": 600.0}
    assert snapshot["by_sector"]["Technology"] == 8300.0
    assert snapshot["by_sector"]["Energy"] == 1100.0
    assert snapshot["total"] == 9400.0


def test_room_is_empty_when_limits_are_off():
    assert pl.room_for("AAPL", 10_000, pl.exposure_snapshot([]), pl.limits_from_settings({})) == {}


def test_room_under_total_and_sector_limits():
    snapshot = pl.exposure_snapshot([{"symbol": "AAPL", "market_value": "1500"}, {"symbol": "XOM", "market_value": "1000"}])
    limits = pl.limits_from_settings({"max_total_exposure_percent": 80, "max_sector_exposure_percent": 25})
    caps = pl.room_for("NVDA", 10_000, snapshot, limits)
    assert caps["total_exposure"][0] == 5500.0      # $8,000 allowed - $2,500 held
    assert caps["sector_exposure"][0] == 1000.0     # $2,500 Technology allowed - $1,500 held
    assert "Technology exposure limit" in caps["sector_exposure"][1]


def test_unclassified_ticker_fails_closed_only_when_the_sector_limit_is_on():
    limits_on = pl.limits_from_settings({"max_sector_exposure_percent": 25})
    caps = pl.room_for("ZZZZ", 10_000, pl.exposure_snapshot([]), limits_on)
    assert caps["sector_exposure"][0] == 0.0 and "no sector classification" in caps["sector_exposure"][1]
    assert pl.room_for("ZZZZ", 10_000, pl.exposure_snapshot([]), pl.limits_from_settings({"max_total_exposure_percent": 50})).get("sector_exposure") is None


def test_add_exposure_counts_same_scan_trades():
    snapshot = pl.exposure_snapshot([])
    pl.add_exposure(snapshot, "AAPL", 1000)
    pl.add_exposure(snapshot, "MSFT", 500)
    assert snapshot["by_sector"]["Technology"] == 1500 and snapshot["total"] == 1500


def test_drawdown_peak_tracking(user_id):
    assert pl.record_equity_and_get_peak(user_id, 3000, "configured paper balance")["peak"] == 3000
    assert pl.record_equity_and_get_peak(user_id, 3300, "configured paper balance")["peak"] == 3300
    assert pl.record_equity_and_get_peak(user_id, 2900, "configured paper balance")["peak"] == 3300
    assert pl.drawdown_block_reason(2900, 3300, 10) is not None           # 12.1% down, past a 10% limit
    assert pl.drawdown_block_reason(2900, 3300, 13) is None               # within a 13% limit
    assert pl.drawdown_block_reason(2900, 3300, 0) is None                 # off
    # A different equity source restarts the peak.
    assert pl.record_equity_and_get_peak(user_id, 100_000, "broker net liquidation value")["peak"] == 100_000


def test_drawdown_threshold_is_inclusive():
    assert pl.drawdown_block_reason(2700, 3000, 10) is not None            # exactly 10%
    assert pl.drawdown_block_reason(2701, 3000, 10) is None


def test_previews_do_not_write_the_peak(user_id):
    pl.record_equity_and_get_peak(user_id, 3000, "configured paper balance")
    assert pl.record_equity_and_get_peak(user_id, 5000, "configured paper balance", persist=False)["peak"] == 5000
    assert pl.record_equity_and_get_peak(user_id, 3000, "configured paper balance")["peak"] == 3000


# --- sizing -----------------------------------------------------------------------


def test_equity_sizing_shrinks_to_the_room_left_and_names_the_limit():
    sizing = pluto_app._compute_position_quantity(
        risk_budget=150.0, entry_price=50.0, stop_price=48.0, available_buying_power=10_000.0, broker_buying_power=10_000.0,
        extra_caps={"sector_exposure": (500.0, "Technology exposure limit reached")},
    )
    assert sizing["quantity"] == 10                     # risk alone would allow 75
    assert sizing["binding_constraints"] == ["sector_exposure"]
    blocked = pluto_app._compute_position_quantity(
        risk_budget=150.0, entry_price=50.0, stop_price=48.0, available_buying_power=10_000.0, broker_buying_power=10_000.0,
        extra_caps={"total_exposure": (20.0, "total exposure limit reached (test)")},
    )
    assert blocked["quantity"] == 0 and blocked["reason"] == "total exposure limit reached (test)"


def test_option_sizing_uses_premium_for_the_room():
    sizing = pluto_app._compute_option_contract_quantity(
        risk_budget=1000.0, ask_price=1.50, available_buying_power=10_000.0, broker_option_buying_power=10_000.0,
        extra_caps={"sector_exposure": (320.0, "sector")},
    )
    assert sizing["quantity"] == 2 and sizing["binding_constraints"] == ["sector_exposure"]  # $150 per contract


# --- scan ---------------------------------------------------------------------------


def _fake_submit(user_id, creds, account_id, ticker, requested_quantity, limit_price, stop_price, target_price, trading_day, entry):
    ol.initialize(entry, ol.ENTRY_SUBMITTED, entry_client_order_id=f"fake-{ticker}")
    ol.transition(entry, ol.ENTRY_FILLED, filled_quantity=requested_quantity)
    ol.transition(entry, ol.PROTECTION_PENDING)
    ol.transition(entry, ol.PROTECTION_CONFIRMED_ACTIVE)
    return entry


def _candidate(ticker="AAPL", confidence=80):
    return {"ticker": ticker, "recommendation": "CALL", "confidence": confidence, "strategy": "Breakout",
            "ideal_entry": 100.0, "stop": 95.0, "target": 110.0}


def _scan(user_id, candidates, positions=(), dry_run=False):
    with patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app, "is_webull_configured", return_value=True), \
         patch.object(pluto_app, "get_anthropic_api_key", return_value=""), \
         patch.object(pluto_app, "get_accounts", return_value=[{"platform": "webull", "status": "Connected"}]), \
         patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=[{"account_id": ACCOUNT_ID}]), \
         patch.object(pluto_app.webull_api, "find_individual_cash_account", return_value={"account_id": ACCOUNT_ID}), \
         patch.object(pluto_app.webull_api, "find_individual_margin_account", return_value=None), \
         patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"), \
         patch.object(pluto_app.webull_api, "get_account_positions", return_value=list(positions)), \
         patch.object(pluto_app.webull_api, "get_open_orders", return_value=[]), \
         patch.object(pluto_app.webull_api, "get_order_history", return_value=[]), \
         patch.object(pluto_app.alpaca_data, "get_latest_trade_price", return_value=100.0), \
         patch.object(pluto_app.webull_api, "get_account_balance",
                      return_value={"total_net_liquidation_value": 100000.0, "total_day_profit_loss": 0.0,
                                    "account_currency_assets": [{"buying_power": "1000000"}]}), \
         patch.object(pluto_app, "_build_page_context", return_value={"upcoming_opportunities": list(candidates)}), \
         patch.object(pluto_app, "_submit_and_protect_entry", side_effect=_fake_submit), \
         patch.object(pluto_app, "record_overnight_order", side_effect=lambda user_id, entry: entry), \
         patch.object(pluto_app, "time"):
        return pluto_app._run_autonomous_trade_scan_locked(user_id, dry_run=dry_run)


def test_with_limits_off_the_scan_sizes_exactly_as_before(user_id):
    result = _scan(user_id, [_candidate()])
    assert result["placed"][0]["quantity"] == 1000  # 5% of $100k risk / $5 per share
    assert "portfolio" in result["placed"][0]["trade_plan"]


def test_a_manual_holding_uses_up_sector_room_and_the_trade_is_sized_to_fit(user_id):
    update_risk_settings(user_id, max_sector_exposure_percent=20)          # $20,000 of Technology
    manual_holding = [{"symbol": "MSFT", "market_value": "15000", "quantity": "37"}]  # not opened by the agent
    result = _scan(user_id, [_candidate("AAPL")], positions=manual_holding)
    placed = result["placed"][0]
    assert placed["quantity"] == 50                                        # $5,000 of room / $100
    assert placed["binding_constraints"] == ["sector_exposure"]
    portfolio = placed["trade_plan"]["portfolio"]
    assert portfolio["sector"] == "Technology" and portfolio["sector_exposure_after_percent"] == 20.0


def test_trades_earlier_in_the_same_scan_count_against_the_limit(user_id):
    update_risk_settings(user_id, max_sector_exposure_percent=10)          # $10,000 of Technology
    result = _scan(user_id, [_candidate("AAPL", 90), _candidate("NVDA", 85)])
    quantities = {e["ticker"]: e["quantity"] for e in result["placed"]}
    assert quantities == {"AAPL": 100}                                     # AAPL takes all $10,000
    nvda = next(e for e in result["skipped"] if e["ticker"] == "NVDA")
    assert "Technology exposure limit reached" in nvda["reason_skipped"]


def test_total_exposure_full_skips_with_the_reason(user_id):
    update_risk_settings(user_id, max_total_exposure_percent=50)
    held = [{"symbol": "XOM", "market_value": "50000"}]
    result = _scan(user_id, [_candidate("AAPL")], positions=held)
    assert result["placed_count"] == 0
    assert "total exposure limit reached" in result["skipped"][0]["reason_skipped"]


def test_the_largest_position_limit_now_applies(user_id):
    update_risk_settings(user_id, max_position_exposure_percent=5)         # $5,000 per position
    result = _scan(user_id, [_candidate("AAPL")])
    assert result["placed"][0]["quantity"] == 50 and "position_cap" in result["placed"][0]["binding_constraints"]


def test_a_drawdown_past_the_limit_pauses_new_entries(user_id):
    update_risk_settings(user_id, max_drawdown_percent=10)
    pl.record_equity_and_get_peak(user_id, 120_000, "broker net liquidation value")  # earlier high
    result = _scan(user_id, [_candidate("AAPL")])                                      # now $100,000: 16.7% down
    assert result["entries_allowed"] is False
    assert "below its peak" in result["new_entries_blocked_reason"]
    assert result["placed_count"] == 0


def test_a_preview_reports_the_drawdown_without_moving_the_peak(user_id):
    update_risk_settings(user_id, max_drawdown_percent=50)
    pl.record_equity_and_get_peak(user_id, 90_000, "broker net liquidation value")
    _scan(user_id, [_candidate()], dry_run=True)                                       # sees $100,000
    assert pl.record_equity_and_get_peak(user_id, 80_000, "broker net liquidation value", persist=False)["peak"] == 90_000


# --- approval -------------------------------------------------------------------------


def _registered_user(suffix):
    user = auth.register_user(f"portfolio-{suffix}", "TestPassword123!")
    auth.approve_user(user["id"])
    return user["id"]


def test_approval_refuses_a_ticket_that_no_longer_fits_the_sector_limit(user_id):
    uid = _registered_user(user_id[:8])
    update_risk_settings(uid, max_sector_exposure_percent=20)
    ticket, _ = tt.create_or_refresh_ticket(uid, {
        "trading_day": pluto_app._trading_day_key(), "ticker": "AAPL", "instrument_type": "EQUITY", "direction": "long",
        "quantity": 100, "limit_price": 100.0, "stop": 95.0, "target": 110.0, "account_id": ACCOUNT_ID,
    })
    held_since = [{"symbol": "NVDA", "market_value": "15000"}]  # bought after the ticket was proposed
    cross = {"price": 100.0, "disagreement": False, "provider_status": []}
    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = uid
        with patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
             patch.object(pluto_app, "is_webull_configured", return_value=True), \
             patch.object(pluto_app, "_current_webull_trading_session", return_value="CORE"), \
             patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=[{"account_id": ACCOUNT_ID}]), \
             patch.object(pluto_app.webull_api, "find_individual_cash_account", return_value={"account_id": ACCOUNT_ID}), \
             patch.object(pluto_app.webull_api, "find_individual_margin_account", return_value=None), \
             patch.object(pluto_app.webull_api, "get_account_positions", return_value=held_since), \
             patch.object(pluto_app.webull_api, "get_open_orders", return_value=[]), \
             patch.object(pluto_app.webull_api, "get_account_balance",
                          return_value={"total_net_liquidation_value": 100000.0, "total_day_profit_loss": 0.0,
                                        "account_currency_assets": [{"buying_power": "1000000"}]}), \
             patch.object(pluto_app.market_data_aggregator, "get_cross_checked_price", return_value=cross), \
             patch.object(pluto_app, "_submit_and_protect_entry") as submit, \
             patch.object(pluto_app, "time"):
            response = client.post(f"/api/trade-tickets/{ticket['ticket_id']}/approve", json={"version": ticket["version"]})
    assert response.status_code == 409
    assert "Technology exposure limit reached" in response.get_json()["error"]["message"]
    submit.assert_not_called()


# --- settings ---------------------------------------------------------------------------


def test_limits_default_off_and_can_be_saved(user_id):
    uid = _registered_user(user_id[:8] + "s")
    status = get_autonomy_status(uid)
    assert all(status[k] == 0.0 for k in ("max_position_exposure_percent", "max_total_exposure_percent", "max_sector_exposure_percent", "max_drawdown_percent"))
    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = uid
        ok = client.post("/api/autonomy/risk-settings", json={"max_position_exposure_percent": 25, "max_total_exposure_percent": 90,
                                                              "max_sector_exposure_percent": 40, "max_drawdown_percent": 15})
        assert ok.status_code == 200
        bad = client.post("/api/autonomy/risk-settings", json={"max_sector_exposure_percent": 150})
        assert bad.status_code == 400
    status = get_autonomy_status(uid)
    assert (status["max_position_exposure_percent"], status["max_total_exposure_percent"],
            status["max_sector_exposure_percent"], status["max_drawdown_percent"]) == (25.0, 90.0, 40.0, 15.0)
