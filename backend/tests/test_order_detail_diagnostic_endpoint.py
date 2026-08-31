from __future__ import annotations

from unittest.mock import patch

import app as pluto_app

CREDS = {"app_key": "key", "app_secret": "secret"}
ACCOUNT_ID = "acct-1"


def _make_admin(username_suffix: str) -> str:
    import auth

    user = auth.register_user(f"admin-order-detail-{username_suffix}", "TestPassword123!")
    auth.approve_user(user["id"])
    auth.set_user_role(user["id"], "admin")
    return user["id"]


def _make_plain_user(username_suffix: str) -> str:
    import auth

    if not any(u.get("role") == "admin" for u in auth.list_all_users()):
        _make_admin(f"{username_suffix}-seed")
    user = auth.register_user(f"plainuser-order-detail-{username_suffix}", "TestPassword123!")
    auth.approve_user(user["id"])
    return user["id"]


def test_route_requires_admin(user_id):
    plain_user_id = _make_plain_user(user_id[:8])
    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = plain_user_id
        response = client.get("/api/admin/diagnostic/order-detail?client_order_id=x")
    assert response.status_code == 403


def test_missing_client_order_id_is_rejected(user_id):
    admin_id = _make_admin(user_id[:8] + "a")
    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = admin_id
        response = client.get("/api/admin/diagnostic/order-detail")
    assert response.status_code == 400


def test_returns_the_real_order_detail_and_a_summary(user_id):
    admin_id = _make_admin(user_id[:8] + "b")
    detail = {"orders": [{"status": "SUBMITTED", "total_quantity": "30", "filled_quantity": "0", "order_id": "X"}]}
    with patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=[{"account_id": ACCOUNT_ID}]), \
         patch.object(pluto_app.webull_api, "find_individual_cash_account", return_value={"account_id": ACCOUNT_ID}), \
         patch.object(pluto_app.webull_api, "get_order_detail", return_value=detail) as mock_detail:
        with pluto_app.app.test_client() as client:
            with client.session_transaction() as sess:
                sess["user_id"] = admin_id
            response = client.get(f"/api/admin/diagnostic/order-detail?user_id={admin_id}&client_order_id=cid-1")

    assert response.status_code == 200
    payload = response.get_json()["data"]
    assert payload["classification"] == "ok"
    assert payload["raw"] == detail
    assert payload["summary"]["status"] == "SUBMITTED"
    mock_detail.assert_called_once_with(CREDS["app_key"], CREDS["app_secret"], ACCOUNT_ID, "cid-1")


def test_a_definite_rejection_is_classified_not_raised(user_id):
    admin_id = _make_admin(user_id[:8] + "c")
    with patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=[{"account_id": ACCOUNT_ID}]), \
         patch.object(pluto_app.webull_api, "find_individual_cash_account", return_value={"account_id": ACCOUNT_ID}), \
         patch.object(pluto_app.webull_api, "get_order_detail", side_effect=pluto_app.webull_api.DefiniteOrderRejection("Order not present")):
        with pluto_app.app.test_client() as client:
            with client.session_transaction() as sess:
                sess["user_id"] = admin_id
            response = client.get(f"/api/admin/diagnostic/order-detail?user_id={admin_id}&client_order_id=cid-missing")

    assert response.status_code == 200
    payload = response.get_json()["data"]
    assert payload["classification"] == "definite_rejection"
    assert "Order not present" in payload["detail"]


def test_an_ambiguous_broker_failure_is_reported_not_swallowed(user_id):
    admin_id = _make_admin(user_id[:8] + "d")
    with patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=[{"account_id": ACCOUNT_ID}]), \
         patch.object(pluto_app.webull_api, "find_individual_cash_account", return_value={"account_id": ACCOUNT_ID}), \
         patch.object(pluto_app.webull_api, "get_order_detail", side_effect=RuntimeError("connection reset")):
        with pluto_app.app.test_client() as client:
            with client.session_transaction() as sess:
                sess["user_id"] = admin_id
            response = client.get(f"/api/admin/diagnostic/order-detail?user_id={admin_id}&client_order_id=cid-1")

    assert response.status_code == 502
    assert "connection reset" in response.get_json()["error"]["message"]
