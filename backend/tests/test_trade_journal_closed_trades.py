from __future__ import annotations

import auth
import app as pluto_app
from autonomy.closed_trades import record_closed_trade


def _registered_user(username_suffix: str) -> str:
    """A real, approved, logged-in-able account - the before_request auth
    gate requires get_user_by_id to resolve and the account to be approved,
    which a bare fixture user_id string alone does not satisfy (that
    fixture only exists to namespace per-user DATA storage in tests that
    call backend functions directly, bypassing routes/sessions)."""
    user = auth.register_user(f"journal-{username_suffix}", "TestPassword123!")
    auth.approve_user(user["id"])
    return user["id"]


def _closed_trade_record(trade_id: str) -> dict:
    return {
        "ticker": "AAPL",
        "side": "BUY",
        "entry_client_order_id": trade_id,
        "stop_client_order_id": "stop-id",
        "target_client_order_id": "target-id",
        "requested_quantity": 10,
        "filled_quantity": 10,
        "average_entry_price": 100.0,
        "exit_type": "target",
        "exited_quantity": 10,
        "average_exit_price": 110.0,
        "entry_timestamp": "2026-08-11T14:30:00+00:00",
        "exit_timestamp": "2026-08-11T15:45:00+00:00",
        "gross_realized_pnl": 100.0,
        "fees": None,
        "net_realized_pnl": 100.0,
        "strategy": "momentum-v1",
        "close_reason": "target_filled",
        "broker_evidence": {"exited_leg_status": "FILLED"},
        "reconciled_at": "2026-08-11T15:45:01+00:00",
    }


def test_trade_journal_page_renders_a_durable_closed_trade(user_id):
    registered_user_id = _registered_user(user_id[:8])
    record_closed_trade(registered_user_id, "pt-closed-1", _closed_trade_record("pt-closed-1"))

    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = registered_user_id
        response = client.get("/trade-journal")

    assert response.status_code == 200
    body = response.data.decode("utf-8")
    assert "Autonomous Closed Trades" in body
    assert "AAPL" in body
    assert "TARGET" in body  # exit_type, uppercased by the template
    assert "target_filled" in body
    assert "$100.00" in body  # net_realized_pnl


def test_trade_journal_page_renders_cleanly_with_no_closed_trades(user_id):
    registered_user_id = _registered_user(user_id[:8] + "b")
    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = registered_user_id
        response = client.get("/trade-journal")

    assert response.status_code == 200
    body = response.data.decode("utf-8")
    assert "No autonomous positions have been conclusively closed yet." in body
