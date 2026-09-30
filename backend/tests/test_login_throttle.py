"""Failed sign-in throttling and same-site redirects after sign-in."""

from __future__ import annotations

import pytest

import app as pluto_app
import auth
import login_throttle


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(login_throttle, "DATA_DIR", tmp_path)


def _post(client, username, password, next_path="", xff="203.0.113.9"):
    return client.post("/login", data={"username": username, "password": password, "next": next_path},
                       headers={"X-Forwarded-For": xff})


def test_repeated_failures_are_throttled_then_success_clears(user_id, isolated):
    name = f"thr-{user_id[:8]}"
    user = auth.register_user(name, "TestPassword123!")
    auth.approve_user(user["id"])
    with pluto_app.app.test_client() as client:
        for _ in range(login_throttle.MAX_FAILURES):
            assert _post(client, name, "wrong").status_code == 401
        blocked = _post(client, name, "TestPassword123!")
        assert blocked.status_code == 429 and b"Too many failed sign-in attempts" in blocked.data
    login_throttle.record_success(name)
    with pluto_app.app.test_client() as client:
        assert _post(client, name, "TestPassword123!", xff="198.51.100.7").status_code == 302


def test_one_address_guessing_many_usernames_is_throttled(isolated):
    with pluto_app.app.test_client() as client:
        for k in range(login_throttle.MAX_FAILURES):
            _post(client, f"nobody-{k}", "x", xff="198.51.100.50")
        assert _post(client, "someone-else", "x", xff="198.51.100.50").status_code == 429
        # A spoofed first X-Forwarded-For hop does not escape the limit.
        assert _post(client, "someone-else", "x", xff="10.0.0.1, 198.51.100.50").status_code == 429


@pytest.mark.parametrize("target,expected", [("/performance", "/performance"), ("//evil.example", "/mission-control"),
                                             ("/\\evil.example", "/mission-control"), ("https://evil.example", "/mission-control")])
def test_after_sign_in_only_same_site_paths_are_followed(user_id, isolated, target, expected):
    name = f"nx-{user_id[:8]}-{abs(hash(target)) % 1000}"
    user = auth.register_user(name, "TestPassword123!")
    auth.approve_user(user["id"])
    with pluto_app.app.test_client() as client:
        response = _post(client, name, "TestPassword123!", next_path=target)
    assert response.status_code == 302 and response.headers["Location"].endswith(expected)
