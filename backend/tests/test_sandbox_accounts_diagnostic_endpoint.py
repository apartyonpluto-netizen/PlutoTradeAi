from __future__ import annotations

from unittest.mock import patch

import app as pluto_app

CREDS = {"app_key": "key", "app_secret": "secret"}

"""Built live 2026-09-01: every real trading code path in this app filters
straight to the single INDIVIDUAL_CASH sandbox account via
find_individual_cash_account, which is why PUT/short entries hit
OPENAPI_GENERATE_NEW_SHORT_POSITION - a cash account cannot hold a short
position. But no code path had ever looked at the FULL account list these
credentials expose, so it was genuinely unknown whether a second,
margin-class sandbox account already existed and was simply never being
selected. This diagnostic answers that with real evidence instead of a
guess."""


def _make_admin(username_suffix: str) -> str:
    import auth

    user = auth.register_user(f"admin-sandbox-accounts-{username_suffix}", "TestPassword123!")
    auth.approve_user(user["id"])
    auth.set_user_role(user["id"], "admin")
    return user["id"]


def _make_plain_user(username_suffix: str) -> str:
    import auth

    if not any(u.get("role") == "admin" for u in auth.list_all_users()):
        _make_admin(f"{username_suffix}-seed")
    user = auth.register_user(f"plainuser-sandbox-accounts-{username_suffix}", "TestPassword123!")
    auth.approve_user(user["id"])
    return user["id"]


def test_route_requires_admin(user_id):
    plain_user_id = _make_plain_user(user_id[:8])
    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = plain_user_id
        response = client.get("/api/admin/diagnostic/sandbox-accounts")
    assert response.status_code == 403


def test_returns_the_full_raw_account_list_not_just_the_cash_one(user_id):
    admin_id = _make_admin(user_id[:8] + "a")
    accounts = [
        {"account_id": "acct-cash-1", "account_class": "INDIVIDUAL_CASH"},
        {"account_id": "acct-margin-1", "account_class": "INDIVIDUAL_MARGIN"},
    ]
    with patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=accounts) as mock_accounts, \
         patch.object(pluto_app.webull_api, "get_account_balance", return_value={"buying_power": "10000"}):
        with pluto_app.app.test_client() as client:
            with client.session_transaction() as sess:
                sess["user_id"] = admin_id
            response = client.get(f"/api/admin/diagnostic/sandbox-accounts?user_id={admin_id}")

    assert response.status_code == 200
    payload = response.get_json()["data"]
    assert payload["count"] == 2
    assert payload["accounts"][0]["account_id"] == "acct-cash-1"
    assert payload["accounts"][0]["balance"] == {"buying_power": "10000"}
    assert payload["accounts"][1]["balance"] == {"buying_power": "10000"}
    mock_accounts.assert_called_once_with(CREDS["app_key"], CREDS["app_secret"])


def test_one_account_balance_failing_does_not_hide_the_others(user_id):
    admin_id = _make_admin(user_id[:8] + "e")
    accounts = [
        {"account_id": "acct-cash-1", "account_class": "INDIVIDUAL_CASH"},
        {"account_id": "acct-futures-1", "account_class": "FUTURES"},
    ]

    def _balance_side_effect(app_key, app_secret, account_id):
        if account_id == "acct-futures-1":
            raise RuntimeError("unsupported account type for balance lookup")
        return {"buying_power": "10000"}

    with patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=accounts), \
         patch.object(pluto_app.webull_api, "get_account_balance", side_effect=_balance_side_effect):
        with pluto_app.app.test_client() as client:
            with client.session_transaction() as sess:
                sess["user_id"] = admin_id
            response = client.get(f"/api/admin/diagnostic/sandbox-accounts?user_id={admin_id}")

    assert response.status_code == 200
    payload = response.get_json()["data"]
    assert payload["accounts"][0]["balance"] == {"buying_power": "10000"}
    assert "unsupported account type" in payload["accounts"][1]["balance_error"]


def test_a_broker_failure_is_reported_not_swallowed(user_id):
    admin_id = _make_admin(user_id[:8] + "b")
    with patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app.webull_api, "get_paper_accounts", side_effect=RuntimeError("connection reset")):
        with pluto_app.app.test_client() as client:
            with client.session_transaction() as sess:
                sess["user_id"] = admin_id
            response = client.get(f"/api/admin/diagnostic/sandbox-accounts?user_id={admin_id}")

    assert response.status_code == 502
    assert "connection reset" in response.get_json()["error"]["message"]
