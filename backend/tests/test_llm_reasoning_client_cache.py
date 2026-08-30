from __future__ import annotations

from unittest.mock import MagicMock, patch

from brains import llm_reasoning


def _clear_cache_entry(api_key: str) -> None:
    llm_reasoning._client_cache.pop(api_key, None)


def test_same_api_key_reuses_the_same_client_instance():
    """The fix for the residual OOM leak: get_llm_verdict runs once per
    qualifying candidate per scan, all day, every trading day - a fresh
    anthropic.Anthropic(api_key=...) (its own httpx.Client, connection pool,
    TLS context, and pydantic schema resolution) must not be built on every
    single call. A live tracemalloc snapshot on the deployed service, taken
    in the minutes before a real OOM crash on 2026-08-28, showed exactly
    that construction's allocation signature as the single largest
    still-growing site."""
    api_key = "cache-test-key"
    _clear_cache_entry(api_key)
    try:
        with patch.object(llm_reasoning, "anthropic") as mock_anthropic:
            mock_anthropic.Anthropic.side_effect = lambda api_key: MagicMock(name=f"client-{api_key}")
            first = llm_reasoning._get_client(api_key)
            second = llm_reasoning._get_client(api_key)
            third = llm_reasoning._get_client(api_key)
        assert first is second is third
        mock_anthropic.Anthropic.assert_called_once_with(api_key=api_key)
    finally:
        _clear_cache_entry(api_key)


def test_different_api_keys_get_different_client_instances():
    """Caching must be keyed per API key - two different accounts' keys must
    never share a client."""
    key_a, key_b = "cache-test-key-a", "cache-test-key-b"
    _clear_cache_entry(key_a)
    _clear_cache_entry(key_b)
    try:
        with patch.object(llm_reasoning, "anthropic") as mock_anthropic:
            mock_anthropic.Anthropic.side_effect = lambda api_key: MagicMock(name=f"client-{api_key}")
            client_a = llm_reasoning._get_client(key_a)
            client_b = llm_reasoning._get_client(key_b)
        assert client_a is not client_b
    finally:
        _clear_cache_entry(key_a)
        _clear_cache_entry(key_b)


def test_get_llm_verdict_reuses_the_cached_client_across_calls():
    """Proves the caching is actually wired into the real call path, not
    just correct in isolation - two full get_llm_verdict calls with the same
    key must only construct the Anthropic client once."""
    api_key = "cache-test-key-verdict"
    _clear_cache_entry(api_key)
    candidate = {
        "ticker": "AAPL", "strategy": "breakout", "confidence": 80, "recommendation": "CALL",
        "ideal_entry": 100.0, "target": 110.0, "stop": 95.0,
        "why_ai_likes_it": "x", "invalidation_rule": "y", "risk_warning": "z",
    }

    def _fake_response():
        block = MagicMock()
        block.type = "tool_use"
        block.name = "submit_trade_verdict"
        block.input = {"verdict": "approve", "confidence_adjustment": 5, "reasoning": "fine"}
        response = MagicMock()
        response.content = [block]
        return response

    try:
        with patch.object(llm_reasoning, "fetch_news_bundle", return_value={"items": []}), \
             patch.object(llm_reasoning, "anthropic") as mock_anthropic:
            constructed_client = MagicMock()
            constructed_client.messages.create.return_value = _fake_response()
            mock_anthropic.Anthropic.return_value = constructed_client

            first = llm_reasoning.get_llm_verdict(candidate, api_key)
            second = llm_reasoning.get_llm_verdict(candidate, api_key)

        assert first == {"available": True, "verdict": "approve", "confidence_adjustment": 5, "reasoning": "fine"}
        assert second == first
        mock_anthropic.Anthropic.assert_called_once_with(api_key=api_key)
        assert constructed_client.messages.create.call_count == 2
    finally:
        _clear_cache_entry(api_key)


def test_no_api_key_never_touches_the_client_cache():
    result = llm_reasoning.get_llm_verdict({"ticker": "AAPL"}, "")
    assert result == {"available": False, "reason": "No Anthropic API key configured."}


def test_anthropic_not_installed_degrades_gracefully_without_raising():
    with patch.object(llm_reasoning, "anthropic", None):
        result = llm_reasoning.get_llm_verdict({"ticker": "AAPL"}, "some-key")
    assert result == {"available": False, "reason": "anthropic package not installed."}


def test_a_flaky_api_call_degrades_to_unavailable_rather_than_raising():
    api_key = "cache-test-key-flaky"
    _clear_cache_entry(api_key)
    try:
        with patch.object(llm_reasoning, "fetch_news_bundle", return_value={"items": []}), \
             patch.object(llm_reasoning, "anthropic") as mock_anthropic:
            constructed_client = MagicMock()
            constructed_client.messages.create.side_effect = RuntimeError("connection reset")
            mock_anthropic.Anthropic.return_value = constructed_client
            result = llm_reasoning.get_llm_verdict({"ticker": "AAPL"}, api_key)
        assert result["available"] is False
        assert "connection reset" in result["reason"]
    finally:
        _clear_cache_entry(api_key)
