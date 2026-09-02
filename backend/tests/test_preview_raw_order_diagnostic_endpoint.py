from __future__ import annotations

from unittest.mock import patch

import app as pluto_app

CREDS = {"app_key": "key", "app_secret": "secret"}

"""TEMPORARY - matching preview-short-sell's own removal condition."""


def _make_admin(username_suffix: str) -> str:
    import auth

    user = auth.register_user(f"admin-preview-raw-{username_suffix}", "TestPassword123!")
    auth.approve_user(user["id"])
    auth.set_user_role(user["id"], "admin")
    return user["id"]


def _make_plain_user(username_suffix: str) -> str:
    import auth

    if not any(u.get("role") == "admin" for u in auth.list_all_users()):
        _make_admin(f"{username_suffix}-seed")
    user = auth.register_user(f"plainuser-preview-raw-{username_suffix}", "TestPassword123!")
    auth.approve_user(user["id"])
    return user["id"]


def test_route_requires_admin(user_id):
    plain_user_id = _make_plain_user(user_id[:8])
    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = plain_user_id
        response = client.post("/api/admin/diagnostic/preview-raw-order", json={"order": {"symbol": "AAPL"}})
    assert response.status_code == 403


def test_missing_order_is_rejected(user_id):
    admin_id = _make_admin(user_id[:8] + "a")
    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = admin_id
        response = client.post(f"/api/admin/diagnostic/preview-raw-order?user_id={admin_id}", json={})
    assert response.status_code == 400


def test_auto_resolves_margin_account_and_previews_the_raw_order(user_id):
    admin_id = _make_admin(user_id[:8] + "b")
    accounts = [
        {"account_id": "acct-cash-1", "account_class": "INDIVIDUAL_CASH"},
        {"account_id": "acct-margin-1", "account_class": "INDIVIDUAL_MARGIN"},
    ]
    order = {"symbol": "AAPL", "side": "BUY", "order_type": "STOP_LOSS", "stop_price": "210.00", "quantity": "1"}
    with patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=accounts), \
         patch.object(pluto_app.webull_api, "preview_raw_order", return_value={"ok": True}) as mock_preview:
        with pluto_app.app.test_client() as client:
            with client.session_transaction() as sess:
                sess["user_id"] = admin_id
            response = client.post(f"/api/admin/diagnostic/preview-raw-order?user_id={admin_id}", json={"order": order})

    assert response.status_code == 200
    payload = response.get_json()["data"]
    assert payload["classification"] == "accepted"
    assert payload["account_id"] == "acct-margin-1"
    mock_preview.assert_called_once_with(CREDS["app_key"], CREDS["app_secret"], "acct-margin-1", order)


def test_a_broker_rejection_is_classified_not_raised(user_id):
    admin_id = _make_admin(user_id[:8] + "c")
    accounts = [{"account_id": "acct-margin-1", "account_class": "INDIVIDUAL_MARGIN"}]
    with patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=accounts), \
         patch.object(pluto_app.webull_api, "preview_raw_order", side_effect=ValueError("invalid side for STOP_LOSS")):
        with pluto_app.app.test_client() as client:
            with client.session_transaction() as sess:
                sess["user_id"] = admin_id
            response = client.post(f"/api/admin/diagnostic/preview-raw-order?user_id={admin_id}", json={"order": {"symbol": "AAPL"}})

    assert response.status_code == 200
    payload = response.get_json()["data"]
    assert payload["classification"] == "rejected_or_errored"
    assert "invalid side for STOP_LOSS" in payload["detail"]
