"""Origin/Referer check on state-changing requests (CSRF defense in depth)."""

from __future__ import annotations

import app as pluto_app
import auth


def _client(user_id):
    user = auth.register_user(f"csrf-{user_id[:8]}", "TestPassword123!")
    auth.approve_user(user["id"])
    client = pluto_app.app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = user["id"]
    return client


def test_a_post_from_another_site_is_refused(user_id):
    client = _client(user_id)
    response = client.post("/api/trade-tickets/x/decline", json={}, headers={"Origin": "https://evil.example"})
    assert response.status_code == 403 and response.get_json()["error"]["code"] == "cross_site"
    assert client.post("/api/trade-tickets/x/decline", json={}, headers={"Origin": "null"}).status_code == 403
    assert client.post("/api/trade-tickets/x/decline", json={}, headers={"Referer": "https://evil.example/page"}).status_code == 403


def test_same_origin_and_headerless_posts_pass_the_check(user_id):
    client = _client(user_id)
    same = client.post("/api/trade-tickets/x/decline", json={}, headers={"Origin": "http://localhost"})
    assert same.status_code == 409  # reached the handler: unknown ticket
    assert client.post("/api/trade-tickets/x/decline", json={}).status_code == 409


def test_reads_and_secret_authenticated_endpoints_are_unaffected(user_id):
    client = _client(user_id)
    assert client.get("/api/trade-tickets", headers={"Origin": "https://evil.example"}).status_code == 200
    cron = client.post("/api/autonomy/cron-trigger", headers={"Origin": "https://evil.example"})
    assert cron.status_code == 401  # its own secret check, not the cross-site guard
