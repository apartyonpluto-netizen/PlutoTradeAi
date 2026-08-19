from __future__ import annotations

from unittest.mock import patch

from integrations import webull as webull_api


def _clear_cache_entry(app_key: str, app_secret: str) -> None:
    webull_api._trade_client_cache.pop((app_key, app_secret), None)


def _no_real_network_trade_client_construction():
    """TradeClient.__init__ -> ClientInitializer.initializer -> init_token
    -> _check_token_enable ALWAYS makes a real GET /openapi/config network
    call, unconditionally, with no opt-out - confirmed by reading
    webull/core/http/initializer/client_initializer.py in the installed SDK
    (webull-openapi-python-sdk==2.0.16). That means every PRE-FIX
    _get_trade_client() call was making TWO real Webull API calls (one for
    this config/token check, one for the actual intended operation) - not
    just one - which is also relevant to this app's previously
    uncharacterized Webull rate-limit exposure. Patched out here so these
    tests exercise ONLY the caching behavior, not the network."""
    return patch(
        "webull.core.http.initializer.client_initializer.ClientInitializer.initializer",
        return_value=None,
    )


def test_same_credentials_reuse_the_same_trade_client_instance():
    """The fix for the continuous-monitor-tick OOM issue: a fresh
    ApiClient/TradeClient (SignerFactory, DefaultEndpointResolver, retry
    policy, etc.) must not be constructed on every single webull_api call -
    that churn, running every ~10s around the clock for every connected
    user, was the leading candidate for the web service's repeated
    out-of-memory restarts on Render."""
    app_key, app_secret = "cache-test-key", "cache-test-secret"
    _clear_cache_entry(app_key, app_secret)
    try:
        with _no_real_network_trade_client_construction():
            first = webull_api._get_trade_client(app_key, app_secret)
            second = webull_api._get_trade_client(app_key, app_secret)
            third = webull_api._get_trade_client(app_key, app_secret)
        assert first is second is third
    finally:
        _clear_cache_entry(app_key, app_secret)


def test_different_credentials_get_different_trade_client_instances():
    """Caching must be keyed per credential pair - two different connected
    accounts must never share a client (and, structurally, never share the
    signer built from the other account's app_secret)."""
    key_a, secret_a = "cache-test-key-a", "cache-test-secret-a"
    key_b, secret_b = "cache-test-key-b", "cache-test-secret-b"
    _clear_cache_entry(key_a, secret_a)
    _clear_cache_entry(key_b, secret_b)
    try:
        with _no_real_network_trade_client_construction():
            client_a = webull_api._get_trade_client(key_a, secret_a)
            client_b = webull_api._get_trade_client(key_b, secret_b)
        assert client_a is not client_b
    finally:
        _clear_cache_entry(key_a, secret_a)
        _clear_cache_entry(key_b, secret_b)


def test_missing_credentials_still_raise_without_populating_the_cache():
    try:
        webull_api._get_trade_client("", "")
        assert False, "expected ValueError for missing credentials"
    except ValueError:
        pass
    assert ("", "") not in webull_api._trade_client_cache


def test_a_second_call_with_the_same_credentials_never_reaches_the_network():
    """The whole point of caching: once a credential pair's client is built,
    subsequent calls must be pure dict lookups - proven here by NOT patching
    the network call and instead asserting the constructor path (which is
    what triggers it) only runs once."""
    app_key, app_secret = "cache-test-key-network", "cache-test-secret-network"
    _clear_cache_entry(app_key, app_secret)
    construction_count = 0

    def _counting_initializer(_api_client):
        nonlocal construction_count
        construction_count += 1

    try:
        with patch(
            "webull.core.http.initializer.client_initializer.ClientInitializer.initializer",
            side_effect=_counting_initializer,
        ):
            webull_api._get_trade_client(app_key, app_secret)
            webull_api._get_trade_client(app_key, app_secret)
            webull_api._get_trade_client(app_key, app_secret)
        assert construction_count == 1, f"expected exactly 1 real construction across 3 calls, got {construction_count}"
    finally:
        _clear_cache_entry(app_key, app_secret)
