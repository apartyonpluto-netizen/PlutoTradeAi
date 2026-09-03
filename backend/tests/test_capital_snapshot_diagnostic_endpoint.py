from __future__ import annotations

from unittest.mock import patch

import app as pluto_app

CREDS = {"app_key": "key", "app_secret": "secret"}

"""Built live 2026-09-03: the real scan's own reason text ("available
buying power could not be determined") correctly fails closed when
_build_capital_snapshot's combined try/except catches ANY failure among
get_account_balance/get_account_positions/get_open_orders, but only ever
logs a server-side warning - no way to see WHICH call actually failed, or
why, without live server/log access. This diagnostic reports each of the
three calls separately."""


def _make_admin(username_suffix: str) -> str:
    import auth

    user = auth.register_user(f"admin-capital-snap-{username_suffix}", "TestPassword123!")
    auth.approve_user(user["id"])
    auth.set_user_role(user["id"], "admin")
    return user["id"]


def _make_plain_user(username_suffix: str) -> str:
    import auth

    if not any(u.get("role") == "admin" for u in auth.list_all_users()):
        _make_admin(f"{username_suffix}-seed")
    user = auth.register_user(f"plainuser-capital-snap-{username_suffix}", "TestPassword123!")
    auth.approve_user(user["id"])
    return user["id"]


def test_route_requires_admin(user_id):
    plain_user_id = _make_plain_user(user_id[:8])
    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = plain_user_id
        response = client.get("/api/admin/diagnostic/capital-snapshot")
    assert response.status_code == 403


def test_reports_each_call_separately_including_a_real_failure(user_id):
    admin_id = _make_admin(user_id[:8] + "a")
    with patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app.webull_api, "get_account_balance", return_value={"total_net_liquidation_value": 100000.0}), \
         patch.object(pluto_app.webull_api, "get_account_positions", return_value=[]), \
         patch.object(pluto_app.webull_api, "get_open_orders", side_effect=RuntimeError("HTTP 429")):
        with pluto_app.app.test_client() as client:
            with client.session_transaction() as sess:
                sess["user_id"] = admin_id
            response = client.get(f"/api/admin/diagnostic/capital-snapshot?user_id={admin_id}&account_id=acct-1")

    assert response.status_code == 200
    payload = response.get_json()["data"]
    assert payload["balance"]["ok"] is True
    assert payload["positions"]["ok"] is True
    assert payload["open_orders"]["ok"] is False
    assert "HTTP 429" in payload["open_orders"]["error"]
    assert payload["open_orders"]["error_type"] == "RuntimeError"


def test_auto_resolves_the_cash_account_when_none_given(user_id):
    admin_id = _make_admin(user_id[:8] + "b")
    accounts = [{"account_id": "acct-cash-1", "account_class": "INDIVIDUAL_CASH"}]
    with patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=accounts), \
         patch.object(pluto_app.webull_api, "get_account_balance", return_value={}) as mock_balance, \
         patch.object(pluto_app.webull_api, "get_account_positions", return_value=[]), \
         patch.object(pluto_app.webull_api, "get_open_orders", return_value=[]):
        with pluto_app.app.test_client() as client:
            with client.session_transaction() as sess:
                sess["user_id"] = admin_id
            response = client.get(f"/api/admin/diagnostic/capital-snapshot?user_id={admin_id}")

    assert response.status_code == 200
    payload = response.get_json()["data"]
    assert payload["account_id"] == "acct-cash-1"
    mock_balance.assert_called_once_with(CREDS["app_key"], CREDS["app_secret"], "acct-cash-1")
