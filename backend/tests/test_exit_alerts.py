from __future__ import annotations

from datetime import datetime, timedelta, timezone

import exit_alerts
from settings_store import update_settings


def _record(**overrides):
    start = datetime(2026, 10, 15, 14, 0, tzinfo=timezone.utc)
    payload = {
        "trade_id": "t-1",
        "ticker": "NVDA",
        "exit_type": "target",
        "close_reason": "target",
        "net_realized_pnl": 58,
        "fees": {"total": 1.2},
        "pnl_status": "complete",
        "entry_timestamp": start.isoformat(),
        "exit_timestamp": (start + timedelta(minutes=14)).isoformat(),
    }
    payload.update(overrides)
    return payload


def test_exit_message_states_the_gain_and_omits_a_goal_that_is_off(user_id):
    message = exit_alerts.format_message(_record(), exit_alerts.goal_line(user_id))
    assert message.startswith("Exited NVDA at target, +$58 after fees.")
    assert "Held 14 min." in message
    assert "MacBook" not in message
    assert exit_alerts.goal_line(user_id) is None


def test_exit_message_uses_the_account_goal_when_it_is_shown(user_id):
    update_settings(user_id, {
        "savings_goal_visible": True,
        "savings_goal_name": "House fund",
        "savings_goal_amount": 2500,
    })
    message = exit_alerts.format_message(_record(), exit_alerts.goal_line(user_id))
    assert "House fund: $0 of $2500." in message
    assert "MacBook" not in message


def test_exit_message_omits_the_goal_when_it_is_hidden(user_id):
    update_settings(user_id, {
        "savings_goal_visible": False,
        "savings_goal_name": "House fund",
        "savings_goal_amount": 2500,
    })
    assert exit_alerts.goal_line(user_id) is None
    message = exit_alerts.format_message(_record(), exit_alerts.goal_line(user_id))
    assert "House fund" not in message


def test_stop_out_shows_the_loss(user_id):
    message = exit_alerts.format_message(_record(
        exit_type="stop",
        close_reason="stop",
        net_realized_pnl=-25,
    ))
    assert "at the stop" in message
    assert "-$25 after fees" in message
    assert "+" not in message.split("Held")[0]
