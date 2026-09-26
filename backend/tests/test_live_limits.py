"""Live dollar caps: only bind when live is armed, only on opening orders, fail closed."""

from unittest.mock import MagicMock, patch

import pytest

import live_limits
from integrations import webull as webull_api

LIVE_ENV = {
    "PLUTO_WEBULL_TRADING_ENVIRONMENT": "live",
    "PLUTO_LIVE_TRADING_CONFIRMATION": "I_UNDERSTAND_THIS_PLACES_REAL_MONEY_ORDERS",
}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("PLUTO_DATA_DIR", str(tmp_path))
    for name in (live_limits.MAX_ORDER_VAR, live_limits.MAX_DAILY_VAR, *LIVE_ENV):
        monkeypatch.delenv(name, raising=False)


def _arm(monkeypatch, per_order=None, daily=None):
    for k, v in LIVE_ENV.items():
        monkeypatch.setenv(k, v)
    if per_order is not None:
        monkeypatch.setenv(live_limits.MAX_ORDER_VAR, str(per_order))
    if daily is not None:
        monkeypatch.setenv(live_limits.MAX_DAILY_VAR, str(daily))


def _client(response=None):
    client = MagicMock()
    client.order_v2.place_order.return_value.json.return_value = response or {"order_id": "1"}
    client.order_v2.place_option.return_value.json.return_value = response or {"order_id": "1"}
    return client


def _buy(client, qty=10, price=20.0, cid="c1", **kw):
    with patch.object(webull_api, "_get_trade_client", return_value=client):
        return webull_api.place_stock_order("k", "s", "acct", "AAPL", "BUY", qty, price, client_order_id=cid, **kw)


def test_sandbox_is_never_capped(monkeypatch):
    client = _client()
    _buy(client, qty=10000, price=500.0)  # $5M, no caps, not armed
    client.order_v2.place_order.assert_called_once()


def test_armed_without_caps_refuses_opening_order(monkeypatch):
    _arm(monkeypatch)
    client = _client()
    with pytest.raises(webull_api.DefiniteOrderRejection, match="not both set"):
        _buy(client)
    client.order_v2.place_order.assert_not_called()


def test_armed_with_only_one_cap_refuses(monkeypatch):
    _arm(monkeypatch, per_order=500)
    with pytest.raises(webull_api.DefiniteOrderRejection):
        _buy(_client())


def test_per_order_cap_blocks_and_allows(monkeypatch):
    _arm(monkeypatch, per_order=250, daily=1000)
    client = _client()
    with pytest.raises(webull_api.DefiniteOrderRejection, match="per-order cap"):
        _buy(client, qty=13, price=20.0, cid="big")  # $260
    client.order_v2.place_order.assert_not_called()
    _buy(client, qty=12, price=20.0, cid="ok")  # $240
    client.order_v2.place_order.assert_called_once()


def test_daily_cap_accumulates_across_orders(monkeypatch):
    _arm(monkeypatch, per_order=300, daily=500)
    client = _client()
    _buy(client, qty=10, price=20.0, cid="a")  # 200
    _buy(client, qty=10, price=20.0, cid="b")  # 400
    with pytest.raises(webull_api.DefiniteOrderRejection, match="daily cap"):
        _buy(client, qty=10, price=20.0, cid="c")  # would be 600
    assert client.order_v2.place_order.call_count == 2
    assert live_limits.today_usage()["spent_usd"] == 400.0


def test_retry_of_same_client_order_id_is_not_double_counted(monkeypatch):
    _arm(monkeypatch, per_order=300, daily=250)
    client = _client()
    _buy(client, qty=10, price=20.0, cid="same")
    _buy(client, qty=10, price=20.0, cid="same")
    assert live_limits.today_usage()["spent_usd"] == 200.0


def test_definite_rejection_releases_reservation(monkeypatch):
    _arm(monkeypatch, per_order=300, daily=300)
    client = _client()
    with patch.object(webull_api, "_send_order_with_retry", side_effect=webull_api.DefiniteOrderRejection("no")):
        with patch.object(webull_api, "_get_trade_client", return_value=client):
            with pytest.raises(webull_api.DefiniteOrderRejection):
                webull_api.place_stock_order("k", "s", "a", "AAPL", "BUY", 10, 20.0, client_order_id="x")
    assert live_limits.today_usage()["spent_usd"] == 0.0


def test_ambiguous_outcome_stays_counted(monkeypatch):
    _arm(monkeypatch, per_order=300, daily=300)
    with patch.object(webull_api, "_send_order_with_retry", side_effect=webull_api.AmbiguousOrderSubmission("?")):
        with patch.object(webull_api, "_get_trade_client", return_value=_client()):
            with pytest.raises(webull_api.AmbiguousOrderSubmission):
                webull_api.place_stock_order("k", "s", "a", "AAPL", "BUY", 10, 20.0, client_order_id="x")
    assert live_limits.today_usage()["spent_usd"] == 200.0


def test_exits_and_protective_orders_are_never_blocked(monkeypatch):
    _arm(monkeypatch)  # armed, NO caps at all
    client = _client()
    with patch.object(webull_api, "_get_trade_client", return_value=client):
        webull_api.place_stock_order("k", "s", "a", "AAPL", "SELL", 10, 20.0, client_order_id="e", opens_position=False)
        webull_api.place_stop_loss_order("k", "s", "a", "AAPL", 10, 19.0, client_order_id="s")
        webull_api.place_take_profit_order("k", "s", "a", "AAPL", 10, 22.0, client_order_id="t")
    assert client.order_v2.place_order.call_count == 3


def test_option_notional_uses_contract_multiplier(monkeypatch):
    _arm(monkeypatch, per_order=400, daily=1000)
    client = _client()
    with patch.object(webull_api, "_get_trade_client", return_value=client):
        with pytest.raises(webull_api.DefiniteOrderRejection, match="per-order cap"):
            # 1 contract at $5.00 premium = $500 with the x100 multiplier
            webull_api.place_option_order("k", "s", "a", "AAPL", "CALL", 200, "2026-10-16", "BUY", 1, 5.0, client_order_id="o")
        webull_api.place_option_order("k", "s", "a", "AAPL", "CALL", 200, "2026-10-16", "BUY", 1, 3.5, client_order_id="o2")
    assert client.order_v2.place_option.call_count == 1


def test_invalid_cap_values_fail_closed(monkeypatch):
    _arm(monkeypatch, per_order="abc", daily="-5")
    assert live_limits.caps_configured() is False
    with pytest.raises(webull_api.DefiniteOrderRejection):
        _buy(_client())
