"""Fees from the broker's own order payloads (replayed from sanitized
Webull sandbox order history captured read-only on 2026-09-30)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

import app as pluto_app
import broker_fees

HISTORY = json.loads((Path(__file__).parent / "fixtures" / "webull_sandbox_cash_order_history_2026-09-30.json").read_text())["orders"]
FILLED_BUY = next(o for o in HISTORY if o["status"] == "FILLED" and o["side"] == "BUY")
FILLED_SELL = next(o for o in HISTORY if o["status"] == "FILLED" and o["side"] == "SELL" and o["fees"])


def test_real_sell_fees_are_summed_by_type():
    fees = broker_fees.from_order(FILLED_SELL)
    assert fees["total"] == pytest.approx(sum(float(f["actual_value"]) for f in FILLED_SELL["fees"]))
    assert {item["type"] for item in fees["items"]} == {"SEC_FEE", "FINRA_FEE"}


def test_no_fees_reported_is_zero_but_missing_fields_are_unknown():
    assert broker_fees.from_order(FILLED_BUY) == {"total": 0.0, "items": []}
    assert broker_fees.from_order({"status": "FILLED"}) is None
    assert broker_fees.combine([broker_fees.from_order(FILLED_BUY), None]) is None


def test_closed_trade_net_pnl_uses_broker_fees():
    record = {"entry_client_order_id": "e", "target_client_order_id": "t", "exit_type": "target", "gross_realized_pnl": 10.0,
              "net_realized_pnl": 10.0, "fees": None}
    history = [{**FILLED_BUY, "client_order_id": "e"}, {**FILLED_SELL, "client_order_id": "t"}]
    with patch.object(pluto_app.webull_api, "get_order_history", return_value=history):
        pluto_app._apply_broker_fees({"app_key": "k", "app_secret": "s"}, "acct", record)
    total = broker_fees.from_order(FILLED_SELL)["total"]
    assert record["fees"] == total and record["net_realized_pnl"] == pytest.approx(10.0 - total)
    assert record["fees_source"] == "broker order history" and record["net_excludes_fees"] is False


def test_an_unreadable_order_leaves_fees_unknown():
    record = {"entry_client_order_id": "e", "stop_client_order_id": "s", "exit_type": "stop", "gross_realized_pnl": -5.0,
              "net_realized_pnl": -5.0, "fees": None}
    with patch.object(pluto_app.webull_api, "get_order_history", side_effect=RuntimeError("HTTP 500")):
        pluto_app._apply_broker_fees({"app_key": "k", "app_secret": "s"}, "acct", record)
    assert record["fees"] is None and record["net_excludes_fees"] is True and record["net_realized_pnl"] == -5.0
    # An exit order missing from history is unknown too, not zero.
    with patch.object(pluto_app.webull_api, "get_order_history", return_value=[{**FILLED_BUY, "client_order_id": "e"}]):
        pluto_app._apply_broker_fees({"app_key": "k", "app_secret": "s"}, "acct", record)
    assert record["fees"] is None
