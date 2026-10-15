from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pandas as pd

import auth
import app as pluto_app
import home_status
from autonomy.closed_trades import record_closed_trade
from autonomy.research_log import record_research_decision
from settings_store import available_themes, get_settings, update_settings


def _bars_df(rows) -> pd.DataFrame:
    index = pd.date_range("2026-08-01", periods=len(rows), freq="h")
    return pd.DataFrame(rows, index=index, columns=["Open", "High", "Low", "Close", "Volume"])


def test_goal_is_private_named_amount_and_can_hide(user_id):
    goal = home_status.build_savings_goal(user_id)
    assert goal["visible"] is True
    assert goal["unset"] is True
    assert "placeholder" not in goal
    assert "MacBook" not in str(goal)

    saved = update_settings(user_id, {
        "savings_goal_name": "rent",
        "savings_goal_amount": 500,
        "savings_goal_visible": False,
    })
    assert saved["savings_goal_name"] == "rent"
    assert saved["savings_goal_visible"] is False
    hidden = home_status.build_savings_goal(user_id)
    assert hidden["visible"] is False
    assert hidden["name"] == "rent"
    assert hidden["amount"] == 500

    recap = home_status.build_daily_recap(user_id)
    assert recap["goal"] is None


def test_daily_recap_lists_skips_and_shows_goal_when_visible(user_id):
    update_settings(user_id, {"savings_goal_name": "a trip", "savings_goal_amount": 1000, "savings_goal_visible": True})
    today = datetime.now(timezone.utc).date().isoformat()
    record_research_decision(user_id, {
        "trading_day": today,
        "ticker": "AAPL",
        "decision": "skipped",
        "reason_skipped": "below the confidence floor",
        "logged_at": datetime.now(timezone.utc).isoformat(),
    })
    record_research_decision(user_id, {
        "trading_day": today,
        "ticker": "MSFT",
        "decision": "placed",
        "strategy": "pullback",
        "logged_at": datetime.now(timezone.utc).isoformat(),
    })
    recap = home_status.build_daily_recap(user_id)
    assert any(row["ticker"] == "AAPL" and "confidence" in row["why"].lower() for row in recap["skipped"])
    assert any(row["ticker"] == "MSFT" for row in recap["taken"])
    assert recap["goal"]["name"] == "a trip"
    assert recap["goal"]["amount"] == 1000


def test_palette_presets_and_legacy_names(user_id):
    assert available_themes() == ["Neon", "Icy", "Gold", "Light"]
    assert get_settings(user_id)["theme"] == "Neon"
    for name in ("Icy", "Gold", "Light", "Neon"):
        assert update_settings(user_id, {"theme": name})["theme"] == name
    assert update_settings(user_id, {"theme": "midnight"})["theme"] == "Neon"
    try:
        update_settings(user_id, {"theme": "casino"})
    except ValueError as error:
        assert "palette" in str(error).lower()
    else:
        raise AssertionError("unknown palette should be refused")


def test_resolve_chart_markers_prefers_overrides_then_local_trade(user_id):
    record_closed_trade(user_id, "trade-1", {
        "ticker": "AAPL",
        "average_entry_price": 100,
        "average_exit_price": 110,
        "environment": "paper",
    })
    from_disk = home_status.resolve_chart_markers(user_id, "aapl")
    assert from_disk["entry"] == 100
    assert from_disk["exit"] == 110
    overridden = home_status.resolve_chart_markers(user_id, "AAPL", {"entry": 90, "stop": 80, "target": 120})
    assert overridden["entry"] == 90
    assert overridden["stop"] == 80
    assert overridden["target"] == 120
    assert overridden["exit"] == 110
    blank = home_status.resolve_chart_markers(user_id, "AAPL", {"stop": 0, "target": ""})
    assert blank["stop"] is None
    assert blank["entry"] == 100


def test_chart_data_route_uses_cached_candles_and_does_not_store_markers(user_id):
    real_user = auth.register_user(f"chartdata-{user_id[:8]}", "TestPassword123!")
    auth.approve_user(real_user["id"])
    df = _bars_df([[100.0, 101.0, 99.0, 100.5, 1_000]])
    pluto_app.CHART_HISTORY_CACHE.clear()
    with patch.object(pluto_app.alpaca_data, "get_bars_single", return_value=df) as fetch:
        with pluto_app.app.test_client() as client:
            with client.session_transaction() as sess:
                sess["user_id"] = real_user["id"]
            first = client.get("/api/chart-data/AAPL?interval=15m&entry=100&stop=95&target=110")
            second = client.get("/api/chart-data/AAPL?interval=15m&entry=101")
    fetch.assert_called_once()
    assert first.status_code == 200
    payload = first.get_json()["data"]
    assert payload["cached"] is False
    assert payload["levels"]["entry"] == 100
    assert payload["levels"]["stop"] == 95
    assert payload["levels"]["target"] == 110
    assert len(payload["candles"]) == 1
    again = second.get_json()["data"]
    assert again["cached"] is True
    assert again["levels"]["entry"] == 101
    cached = pluto_app.CHART_HISTORY_CACHE["AAPL:15m"]["payload"]
    assert "levels" not in cached


def test_chart_data_route_requires_login():
    with pluto_app.app.test_client() as client:
        response = client.get("/api/chart-data/AAPL")
    assert response.status_code in (401, 302)
