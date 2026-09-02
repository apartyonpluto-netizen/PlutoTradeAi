from __future__ import annotations

from unittest.mock import patch

import app as pluto_app

CREDS = {"app_key": "key", "app_secret": "secret"}

"""TEMPORARY - matching the endpoint's own removal condition (remove once
real short selling on the margin sandbox account is verified/adopted or
ruled out, same as the earlier combo-order diagnostic's tests, which were
removed alongside it in 38a0c8b)."""


def _make_admin(username_suffix: str) -> str:
    import auth

    user = auth.register_user(f"admin-preview-short-{username_suffix}", "TestPassword123!")
    auth.approve_user(user["id"])
    auth.set_user_role(user["id"], "admin")
    return user["id"]


def _make_plain_user(username_suffix: str) -> str:
    import auth

    if not any(u.get("role") == "admin" for u in auth.list_all_users()):
        _make_admin(f"{username_suffix}-seed")
    user = auth.register_user(f"plainuser-preview-short-{username_suffix}", "TestPassword123!")
    auth.approve_user(user["id"])
    return user["id"]


def test_route_requires_admin(user_id):
    plain_user_id = _make_plain_user(user_id[:8])
    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = plain_user_id
        response = client.post("/api/admin/diagnostic/preview-short-sell", json={})
    assert response.status_code == 403


def test_auto_resolves_the_margin_account_and_reports_acceptance(user_id):
    admin_id = _make_admin(user_id[:8] + "a")
    accounts = [
        {"account_id": "acct-cash-1", "account_class": "INDIVIDUAL_CASH"},
        {"account_id": "acct-margin-1", "account_class": "INDIVIDUAL_MARGIN"},
    ]
    with patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=accounts), \
         patch.object(pluto_app.webull_api, "preview_stock_order", return_value={"ok": True}) as mock_preview:
        with pluto_app.app.test_client() as client:
            with client.session_transaction() as sess:
                sess["user_id"] = admin_id
            response = client.post(
                f"/api/admin/diagnostic/preview-short-sell?user_id={admin_id}",
                json={"symbol": "AAPL", "quantity": 1, "limit_price": 200.0},
            )

    assert response.status_code == 200
    payload = response.get_json()["data"]
    assert payload["classification"] == "accepted"
    assert payload["account_id"] == "acct-margin-1"
    mock_preview.assert_called_once_with(
        CREDS["app_key"], CREDS["app_secret"], "acct-margin-1",
        symbol="AAPL", side="SELL", quantity=1.0, limit_price=200.0,
    )


def test_a_broker_rejection_is_classified_not_raised(user_id):
    admin_id = _make_admin(user_id[:8] + "b")
    accounts = [{"account_id": "acct-margin-1", "account_class": "INDIVIDUAL_MARGIN"}]
    with patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=accounts), \
         patch.object(pluto_app.webull_api, "preview_stock_order", side_effect=ValueError("OPENAPI_GENERATE_NEW_SHORT_POSITION")):
        with pluto_app.app.test_client() as client:
            with client.session_transaction() as sess:
                sess["user_id"] = admin_id
            response = client.post(f"/api/admin/diagnostic/preview-short-sell?user_id={admin_id}", json={})

    assert response.status_code == 200
    payload = response.get_json()["data"]
    assert payload["classification"] == "rejected_or_errored"
    assert "OPENAPI_GENERATE_NEW_SHORT_POSITION" in payload["detail"]


def test_missing_margin_account_is_reported(user_id):
    admin_id = _make_admin(user_id[:8] + "c")
    with patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=[{"account_id": "acct-cash-1", "account_class": "INDIVIDUAL_CASH"}]):
        with pluto_app.app.test_client() as client:
            with client.session_transaction() as sess:
                sess["user_id"] = admin_id
            response = client.post(f"/api/admin/diagnostic/preview-short-sell?user_id={admin_id}", json={})

    assert response.status_code == 404
