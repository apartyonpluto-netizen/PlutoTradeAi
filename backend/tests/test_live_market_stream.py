"""Alpaca data-stream behavior ported from feature/alpaca-live-market-stream.

The socket is market data only. These tests never open a real network
connection: the websocket client is faked, and snapshot calls are mocked.
"""

from __future__ import annotations

import json
import os
import threading
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import live_market_stream as stream_mod
from live_market_stream import (
    AlpacaRestPollingProvider,
    AlpacaStreamingProvider,
    ConnectionHealthService,
    MarketDataCache,
    MarketStreamService,
)


class FakeSocketIO:
    def __init__(self):
        self.events = []
        self._listeners = []

    def emit(self, event, payload):
        self.events.append((event, payload))
        for listener in list(self._listeners):
            listener(event, payload)

    def add_listener(self, listener):
        self._listeners.append(listener)


def _sample_quote(symbol="TSLA", price=250.12):
    return {
        "symbol": symbol,
        "price": price,
        "bid": price - 0.02,
        "ask": price + 0.02,
        "volume": 1000,
        "timestamp": "2026-07-21T00:00:00+00:00",
        "source_status": "DELAYED",
        "provider": "AlpacaRestPollingProvider",
    }


class LiveMarketStreamTests(unittest.TestCase):
    @patch("live_market_stream._now_epoch")
    def test_cache_live_stale_unavailable(self, mock_now):
        cache = MarketDataCache(ttl_seconds=1, stale_seconds=3, max_symbols=32)
        mock_now.return_value = 100.0
        row = cache.update("TSLA", {"symbol": "TSLA", "price": 100.0, "provider": "Test", "source_status": "LIVE"})
        self.assertIsNotNone(row)

        mock_now.return_value = 100.4
        cached, status, age = cache.get("TSLA")
        self.assertIsNotNone(cached)
        self.assertEqual(status, "live")
        self.assertEqual(age, 0)

        mock_now.return_value = 102.0
        self.assertEqual(cache.get("TSLA")[1], "stale")
        mock_now.return_value = 104.0
        self.assertEqual(cache.get("TSLA")[1], "unavailable")

    def test_cache_lru_eviction_bound(self):
        cache = MarketDataCache(ttl_seconds=10, stale_seconds=60, max_symbols=3)
        cache.update("A", {"symbol": "A"})
        cache.update("B", {"symbol": "B"})
        cache.update("C", {"symbol": "C"})
        cache.update("D", {"symbol": "D"})

        self.assertEqual(cache.size(), 3)
        self.assertIsNone(cache.get("A")[0])
        self.assertIsNotNone(cache.get("D")[0])

    @patch("live_market_stream._now_epoch")
    def test_cache_cleanup_stale(self, mock_now_epoch):
        cache = MarketDataCache(ttl_seconds=2, stale_seconds=5, max_symbols=32)
        mock_now_epoch.return_value = 100.0
        cache.update("TSLA", {"symbol": "TSLA"})
        mock_now_epoch.return_value = 107.0
        removed = cache.cleanup_stale()
        self.assertEqual(removed, 1)
        self.assertIsNone(cache.get("TSLA")[0])

    @patch("live_market_stream.AlpacaRestPollingProvider.get_quotes")
    def test_market_quotes_uses_provider_and_returns_schema(self, mock_get_quotes):
        mock_get_quotes.return_value = {"TSLA": _sample_quote()}

        service = MarketStreamService(socketio=None)
        response = service.get_quotes(["TSLA"])

        self.assertTrue(response["success"])
        self.assertIn("quotes", response["data"])
        self.assertGreaterEqual(len(response["data"]["quotes"]), 1)
        quote = response["data"]["quotes"][0]
        self.assertEqual(quote["symbol"], "TSLA")
        self.assertIn("sequence", quote)
        self.assertIn("timestamp", quote)
        self.assertFalse(response["orders_enabled"] if "orders_enabled" in response else False)

    @patch("live_market_stream.AlpacaRestPollingProvider.get_quote")
    def test_single_quote_schema(self, mock_get_quote):
        mock_get_quote.return_value = _sample_quote()

        service = MarketStreamService(socketio=None)
        response = service.get_quote("TSLA")

        self.assertTrue(response["success"])
        self.assertEqual(response["data"]["symbol"], "TSLA")
        self.assertIn("sequence", response["data"])

    def test_symbol_validation_failure(self):
        service = MarketStreamService(socketio=None)
        response = service.get_quote("$$$")
        self.assertFalse(response["success"])
        self.assertIn("invalid", response["error"].lower())

    def test_nan_and_inf_prices_are_dropped(self):
        self.assertIsNone(stream_mod._to_number(float("nan")))
        self.assertIsNone(stream_mod._to_number(float("inf")))
        health = ConnectionHealthService()
        seen = []
        provider = AlpacaStreamingProvider(lambda quote: seen.append(quote), health)
        provider._on_message(None, json.dumps([{"T": "t", "S": "TSLA", "p": "NaN", "s": 1}]))
        self.assertEqual(seen[0]["price"], None)

    def test_watchlist_is_bounded_and_lru(self):
        with patch.dict(os.environ, {"PLUTO_MARKET_MAX_SYMBOLS": "8"}):
            service = MarketStreamService(socketio=None)
            for idx in range(30):
                service.add_symbol(f"T{idx}")

            symbols = service.symbols()
            self.assertLessEqual(len(symbols), 8)
            self.assertIn("T29", symbols)

    @patch("live_market_stream.AlpacaRestPollingProvider.get_quotes")
    def test_missing_alpaca_credentials_falls_back_cleanly(self, mock_get_quotes):
        mock_get_quotes.return_value = {"SPY": _sample_quote("SPY", 100.0)}
        fake_socket = FakeSocketIO()
        env = {
            "PLUTO_MARKET_PROVIDER": "alpaca",
            "ALPACA_API_KEY_ID": "",
            "ALPACA_API_SECRET_KEY": "",
            "ALPACA_API_KEY": "legacy-key-must-not-be-used",
            "ALPACA_API_SECRET": "legacy-secret-must-not-be-used",
        }
        service = None
        try:
            with patch.dict(os.environ, env):
                service = MarketStreamService(socketio=fake_socket)
                self.assertNotEqual(service._provider._key, "legacy-key-must-not-be-used")
                service.start()
                status = service.connection_status()
        finally:
            if service is not None:
                service.stop()

        self.assertEqual(status["state"], "polling")
        self.assertTrue(status["fallback_active"])
        self.assertFalse(status["orders_enabled"])
        self.assertIn("credentials", (status.get("last_error") or "").lower())
        event_names = [name for name, _payload in fake_socket.events]
        self.assertIn("connection_status", event_names)
        self.assertIn("fallback_activated", event_names)
        blob = json.dumps(fake_socket.events)
        self.assertNotIn("legacy-key-must-not-be-used", blob)
        self.assertNotIn("legacy-secret-must-not-be-used", blob)

    def test_state_transition_events_live_reconnect_polling_live(self):
        fake_socket = FakeSocketIO()
        service = MarketStreamService(socketio=fake_socket)

        service._health.connected()
        service._health.set_fallback(False)
        service._emit_status_events()

        service._health.disconnected("wire dropped")
        service._health.set_fallback(False)
        service._emit_status_events()

        service._health.set_fallback(True)
        service._emit_status_events()

        service._health.connected()
        service._health.set_fallback(False)
        service._emit_status_events()

        names = [name for name, _payload in fake_socket.events]
        self.assertIn("connection_status", names)
        self.assertIn("fallback_activated", names)
        self.assertIn("stream_recovered", names)
        self.assertIn("market_data_error", names)

    def test_multiple_browser_clients_receive_updates(self):
        fake_socket = FakeSocketIO()
        service = MarketStreamService(socketio=fake_socket)
        received = []
        fake_socket.add_listener(lambda event, payload: received.append((event, payload)))
        second = []
        fake_socket.add_listener(lambda event, payload: second.append((event, payload)))

        service._broadcast_quote(_sample_quote())

        self.assertTrue(any(name == "market_update" for name, _payload in received))
        self.assertTrue(any(name == "market_update" for name, _payload in second))

    def test_alpaca_provider_ignores_malformed_messages(self):
        health = ConnectionHealthService()
        seen = []
        provider = AlpacaStreamingProvider(lambda quote: seen.append(quote), health)

        provider._on_message(None, "{bad json")
        provider._on_message(None, json.dumps({"x": 1}))
        provider._on_message(None, json.dumps([{"T": "unknown"}]))
        provider._on_message(None, json.dumps([{"T": "q", "S": "", "bp": 1}]))

        self.assertEqual(len(seen), 0)

    def test_alpaca_provider_parses_quote_and_trade(self):
        health = ConnectionHealthService()
        seen = []
        provider = AlpacaStreamingProvider(lambda quote: seen.append(quote), health)

        provider._on_message(None, json.dumps([{"T": "q", "S": "TSLA", "bp": 250.1, "ap": 250.2, "bs": 10, "as": 10}]))
        provider._on_message(None, json.dumps([{"T": "t", "S": "TSLA", "p": 250.15, "s": 100}]))
        provider_health = provider.health()

        self.assertEqual(len(seen), 2)
        self.assertEqual(provider_health["message_counts"]["quote"], 1)
        self.assertEqual(provider_health["message_counts"]["trade"], 1)
        self.assertNotIn("secret", json.dumps(provider_health).lower())

    def test_reconnect_and_resubscribe(self):
        sent_messages = []
        connections = {"count": 0}

        class FakeWebSocketApp:
            def __init__(self, _url, on_open=None, on_message=None, on_error=None, on_close=None):
                self.on_open = on_open
                self.on_message = on_message
                self.on_close = on_close

            def send(self, message):
                sent_messages.append(message)

            def close(self):
                return None

            def run_forever(self, ping_interval=20, ping_timeout=10):
                _ = ping_interval, ping_timeout
                connections["count"] += 1
                if self.on_open:
                    self.on_open(self)
                if self.on_message:
                    self.on_message(self, '[{"T":"q","S":"TSLA","bp":200.1,"ap":200.2}]')
                if self.on_close:
                    self.on_close(self, 1006, "forced close")

        health = ConnectionHealthService()
        events = []
        provider = AlpacaStreamingProvider(
            lambda _quote: None,
            health,
            on_event=lambda name, payload: events.append((name, payload)),
        )
        provider._key = "valid"
        provider._secret = "valid"

        original_websocket = stream_mod.websocket
        stream_mod.websocket = types.SimpleNamespace(WebSocketApp=FakeWebSocketApp)
        try:
            provider.start(["TSLA", "SPY"])
            deadline = time.time() + 3
            while time.time() < deadline and connections["count"] < 2:
                time.sleep(0.05)
            provider.stop()
        finally:
            stream_mod.websocket = original_websocket

        self.assertGreaterEqual(connections["count"], 2)
        self.assertTrue(any('"action": "auth"' in row for row in sent_messages))
        self.assertTrue(any('"action": "subscribe"' in row for row in sent_messages))
        event_names = [name for name, _payload in events]
        self.assertIn("provider_connected", event_names)
        self.assertIn("provider_subscribe", event_names)
        self.assertNotIn("valid", json.dumps(events))

    def test_stream_errors_redact_the_api_key(self):
        key = "PK-TEST-KEY-SHOULD-NOT-LEAK"
        secret = "SECRET-VALUE-SHOULD-NOT-LEAK"
        with patch.dict(os.environ, {"ALPACA_API_KEY_ID": key, "ALPACA_API_SECRET_KEY": secret}):
            health = ConnectionHealthService()
            events = []
            provider = AlpacaStreamingProvider(lambda _quote: None, health, on_event=lambda name, payload: events.append(payload))
        with self.assertLogs("live_market_stream", level="WARNING") as captured:
            provider._on_message(None, json.dumps([{"T": "error", "msg": f"auth failed for {key} secret {secret}"}]))
        blob = json.dumps(provider.health()) + json.dumps(events) + "\n".join(captured.output)
        self.assertNotIn(key, blob)
        self.assertNotIn(secret, blob)
        self.assertIn("[redacted]", blob)

    def test_ws_url_stays_on_the_official_iex_host(self):
        env = {
            "ALPACA_DATA_FEED": "iex",
            "ALPACA_WS_URL": "wss://evil.example/v2/iex",
            "ALPACA_API_KEY_ID": "k",
            "ALPACA_API_SECRET_KEY": "s",
        }
        with patch.dict(os.environ, env):
            provider = AlpacaStreamingProvider(lambda _quote: None, ConnectionHealthService())
        self.assertEqual(provider._stream_url, "wss://stream.data.alpaca.markets/v2/iex")

        sip_override = dict(env, ALPACA_WS_URL="wss://user:secret@stream.data.alpaca.markets/v2/sip")
        with patch.dict(os.environ, sip_override):
            provider = AlpacaStreamingProvider(lambda _quote: None, ConnectionHealthService())
        self.assertEqual(provider._stream_url, "wss://stream.data.alpaca.markets/v2/iex")

    def test_sip_feed_is_explicit_only(self):
        with patch.dict(os.environ, {"ALPACA_DATA_FEED": "sip", "ALPACA_WS_URL": ""}, clear=False):
            self.assertEqual(stream_mod.resolve_stream_url(), "wss://stream.data.alpaca.markets/v2/sip")
        with patch.dict(os.environ, {"ALPACA_DATA_FEED": "iex"}):
            self.assertTrue(stream_mod.resolve_stream_url().endswith("/v2/iex"))

    def test_yfinance_provider_name_is_ignored(self):
        with patch.dict(os.environ, {"PLUTO_MARKET_PROVIDER": "yfinance"}):
            service = MarketStreamService()
        self.assertFalse(service._streaming_mode)
        self.assertEqual(service._provider.name, "AlpacaRestPollingProvider")
        self.assertEqual(service.connection_status()["ignored_provider"], "yfinance")
        source = Path(stream_mod.__file__).read_text(encoding="utf-8")
        for forbidden in ("import yfinance", "place_order", "/v2/orders", "paper-api.alpaca.markets", "submit_order"):
            self.assertNotIn(forbidden, source)

    def test_rest_poll_maps_snapshot_and_skips_the_network_without_keys(self):
        snapshot = {
            "SPY": {
                "latestTrade": {"p": 500.25, "s": 10, "t": "2026-10-15T14:00:00Z"},
                "latestQuote": {"bp": 500.2, "ap": 500.3, "bs": 1, "as": 1},
            }
        }
        with patch.dict(os.environ, {"ALPACA_API_KEY_ID": "key123", "ALPACA_API_SECRET_KEY": "secret456"}), patch(
            "integrations.alpaca_data.get_snapshots", return_value=snapshot
        ) as mocked:
            rows = AlpacaRestPollingProvider().get_quotes(["spy"])
        mocked.assert_called_once()
        self.assertEqual(rows["SPY"]["price"], 500.25)
        self.assertEqual(rows["SPY"]["bid"], 500.2)
        self.assertEqual(rows["SPY"]["ask"], 500.3)
        self.assertEqual(rows["SPY"]["source_status"], "LIVE")
        self.assertNotIn("key123", json.dumps(rows))

        with patch.dict(os.environ, {"ALPACA_API_KEY_ID": "", "ALPACA_API_SECRET_KEY": ""}), patch(
            "integrations.alpaca_data.get_snapshots"
        ) as mocked_again:
            self.assertEqual(AlpacaRestPollingProvider().get_quotes(["SPY"]), {})
        mocked_again.assert_not_called()

    def test_rest_quote_does_not_open_a_socket(self):
        created = []

        class Boom:
            def __init__(self, *_args, **_kwargs):
                created.append(True)

        with patch.object(stream_mod, "websocket", types.SimpleNamespace(WebSocketApp=Boom)), patch(
            "live_market_stream.AlpacaRestPollingProvider.get_quote", return_value=_sample_quote("SPY", 500.0)
        ):
            service = MarketStreamService()
            response = service.get_quote("SPY")
        self.assertTrue(response["success"])
        self.assertEqual(created, [])
        self.assertFalse(service.connection_status()["orders_enabled"])

    @patch("live_market_stream.AlpacaRestPollingProvider.get_quotes")
    def test_only_one_service_opens_the_data_socket(self, mock_get_quotes):
        mock_get_quotes.return_value = {"SPY": _sample_quote("SPY", 100.0)}
        connections = {"count": 0}
        release = threading.Event()

        class BlockingSocket:
            def __init__(self, _url, on_open=None, on_message=None, on_error=None, on_close=None):
                self.on_open = on_open
                connections["count"] += 1

            def send(self, _message):
                return None

            def close(self):
                release.set()

            def run_forever(self, ping_interval=20, ping_timeout=10):
                if self.on_open:
                    self.on_open(self)
                release.wait(timeout=5)

        env = {"PLUTO_MARKET_PROVIDER": "alpaca", "ALPACA_API_KEY_ID": "key123", "ALPACA_API_SECRET_KEY": "secret456"}
        original = stream_mod.websocket
        stream_mod.websocket = types.SimpleNamespace(WebSocketApp=BlockingSocket)
        first = second = None
        try:
            with patch.dict(os.environ, env):
                first = MarketStreamService()
                first.start()
                deadline = time.time() + 2
                while time.time() < deadline and connections["count"] < 1:
                    time.sleep(0.02)
                second = MarketStreamService()
                second.start()
                time.sleep(0.2)
                status = second.connection_status()
        finally:
            stream_mod.websocket = original
            if second is not None:
                second.stop()
            if first is not None:
                first.stop()

        self.assertEqual(connections["count"], 1)
        self.assertTrue(status["fallback_active"])
        self.assertIn("another process", (status.get("last_error") or ""))

    @patch("live_market_stream.AlpacaRestPollingProvider.get_quotes")
    def test_double_start_does_not_spawn_duplicate_poll_threads(self, mock_get_quotes):
        mock_get_quotes.return_value = {}
        service = MarketStreamService(socketio=None)
        try:
            service.start()
            first = service._poll_thread
            service.start()
            second = service._poll_thread
        finally:
            service.stop()
        self.assertIs(first, second)

    def test_virtual_30_minute_soak_stability(self):
        with patch.dict(
            os.environ,
            {"PLUTO_MARKET_MAX_SYMBOLS": "32", "PLUTO_MARKET_CACHE_SECONDS": "30", "PLUTO_MARKET_STALE_SECONDS": "300"},
        ):
            service = MarketStreamService(socketio=None)

        for tick in range(1800):
            symbol = f"T{tick % 120}"
            service._broadcast_quote(_sample_quote(symbol, float(100 + (tick % 25))))
            if tick % 25 == 0:
                service._cache.cleanup_stale()

        status = service.connection_status()
        self.assertLessEqual(status["cache_size"], status["max_symbols"])
        self.assertLessEqual(len(status["watch_symbols"]), status["max_symbols"])
        self.assertFalse(status["orders_enabled"])

    @patch("live_market_stream.AlpacaRestPollingProvider.get_quotes")
    def test_no_thread_leak_on_start_stop_cycles(self, mock_get_quotes):
        mock_get_quotes.return_value = {}
        service = MarketStreamService(socketio=None)
        saw_poll_thread = False
        try:
            for _ in range(3):
                service.start()
                if service._poll_thread is not None:
                    saw_poll_thread = True
                time.sleep(0.05)
                service.stop()
        finally:
            service.stop()
        self.assertTrue(saw_poll_thread)
        self.assertFalse(service._poll_thread.is_alive() if service._poll_thread else False)

    def test_autostart_is_off_unless_enabled_and_disable_wins(self):
        with patch.dict(os.environ, {"PLUTO_ENABLE_MARKET_STREAM": "", "PLUTO_DISABLE_MARKET_STREAM": ""}):
            self.assertFalse(stream_mod.autostart_enabled())
        with patch.dict(os.environ, {"PLUTO_ENABLE_MARKET_STREAM": "1", "PLUTO_DISABLE_MARKET_STREAM": ""}):
            self.assertTrue(stream_mod.autostart_enabled())
        with patch.dict(os.environ, {"PLUTO_ENABLE_MARKET_STREAM": "1", "PLUTO_DISABLE_MARKET_STREAM": "true"}):
            self.assertFalse(stream_mod.autostart_enabled())

    @patch("live_market_stream.AlpacaRestPollingProvider.get_quotes")
    def test_get_service_does_not_start_without_the_flag(self, mock_get_quotes):
        mock_get_quotes.return_value = {}
        stream_mod.reset_service_for_tests()
        try:
            with patch.dict(os.environ, {"PLUTO_ENABLE_MARKET_STREAM": "0"}):
                service = stream_mod.get_service()
                self.assertFalse(service._started)
                self.assertFalse(service._streaming_mode)
        finally:
            stream_mod.reset_service_for_tests()


def _logged_in_client(user_id: str):
    import auth
    import app as pluto_app

    user = auth.register_user(f"stream{user_id[:8]}", "TestPassword123!")
    auth.approve_user(user["id"])
    client = pluto_app.app.test_client()
    return user, client


def test_market_stream_routes_require_login_and_do_not_open_a_socket(user_id):
    created = []

    class Boom:
        def __init__(self, *_args, **_kwargs):
            created.append(True)

    _user, client = _logged_in_client(user_id)
    anonymous = client.get("/api/market/quote/SPY")
    assert anonymous.status_code == 401

    stream_mod.reset_service_for_tests()
    try:
        with patch.object(stream_mod, "websocket", types.SimpleNamespace(WebSocketApp=Boom)), patch(
            "live_market_stream.AlpacaRestPollingProvider.get_quote", return_value=_sample_quote("SPY", 500.1)
        ), patch(
            "live_market_stream.AlpacaRestPollingProvider.get_quotes",
            return_value={"SPY": _sample_quote("SPY", 500.1), "QQQ": _sample_quote("QQQ", 400.0)},
        ):
            with client.session_transaction() as sess:
                sess["user_id"] = _user["id"]
            quote = client.get("/api/market/quote/spy")
            invalid = client.get("/api/market/quote/$$$")
            quotes = client.get("/api/market/quotes?symbols=SPY,QQQ,$$$")
            status = client.get("/api/market/status")
    finally:
        stream_mod.reset_service_for_tests()

    assert quote.status_code == 200
    body = quote.get_json()
    assert body["success"] is True
    assert body["data"]["symbol"] == "SPY"
    assert body["data"]["price"] == 500.1
    assert "sequence" in body["data"]
    assert invalid.status_code == 400
    assert "invalid" in invalid.get_json()["error"]["message"].lower()
    assert quotes.status_code == 200
    symbols = [row["symbol"] for row in quotes.get_json()["data"]["quotes"]]
    assert symbols == ["SPY", "QQQ"]
    status_body = status.get_json()
    assert status_body["success"] is True
    assert status_body["data"]["orders_enabled"] is False
    assert status_body["data"]["started"] is False
    assert created == []
    blob = json.dumps(status_body)
    assert "ALPACA_API_SECRET_KEY" not in blob
