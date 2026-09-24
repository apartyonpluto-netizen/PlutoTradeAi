import json
import os
import time
import types
import unittest
from unittest.mock import patch

import backend.services.live_market_stream as stream_mod
from backend.services.live_market_stream import (
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
        for listener in self._listeners:
            listener(event, payload)

    def add_listener(self, listener):
        self._listeners.append(listener)


class LiveMarketStreamTests(unittest.TestCase):
    def test_cache_live_stale_unavailable(self):
        cache = MarketDataCache(ttl_seconds=1, stale_seconds=3, max_symbols=32)
        row = cache.update("TSLA", {"symbol": "TSLA", "price": 100.0, "provider": "Test", "source_status": "LIVE"})
        self.assertIsNotNone(row)

        cached, status, age = cache.get("TSLA")
        self.assertIsNotNone(cached)
        self.assertEqual(status, "live")
        self.assertIsNotNone(age)

        time.sleep(2)
        self.assertEqual(cache.get("TSLA")[1], "stale")
        time.sleep(2)
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

    @patch("backend.services.live_market_stream._now_epoch")
    def test_cache_cleanup_stale(self, mock_now_epoch):
        cache = MarketDataCache(ttl_seconds=2, stale_seconds=5, max_symbols=32)
        mock_now_epoch.return_value = 100.0
        cache.update("TSLA", {"symbol": "TSLA"})
        mock_now_epoch.return_value = 107.0
        removed = cache.cleanup_stale()
        self.assertEqual(removed, 1)
        self.assertIsNone(cache.get("TSLA")[0])

    @patch("backend.services.live_market_stream.YFinancePollingProvider.get_quotes")
    def test_market_quotes_uses_provider_and_returns_schema(self, mock_get_quotes):
        mock_get_quotes.return_value = {
            "TSLA": {
                "symbol": "TSLA",
                "price": 250.12,
                "bid": 250.10,
                "ask": 250.14,
                "volume": 1000,
                "timestamp": "2026-07-21T00:00:00+00:00",
                "source_status": "DELAYED",
                "provider": "YFinancePollingProvider",
            }
        }

        service = MarketStreamService(socketio=None)
        response = service.get_quotes(["TSLA"])

        self.assertTrue(response["success"])
        self.assertIn("quotes", response["data"])
        self.assertGreaterEqual(len(response["data"]["quotes"]), 1)
        quote = response["data"]["quotes"][0]
        self.assertEqual(quote["symbol"], "TSLA")
        self.assertIn("sequence", quote)
        self.assertIn("timestamp", quote)

    @patch("backend.services.live_market_stream.YFinancePollingProvider.get_quote")
    def test_single_quote_schema(self, mock_get_quote):
        mock_get_quote.return_value = {
            "symbol": "TSLA",
            "price": 250.12,
            "bid": 250.10,
            "ask": 250.14,
            "volume": 1000,
            "timestamp": "2026-07-21T00:00:00+00:00",
            "source_status": "DELAYED",
            "provider": "YFinancePollingProvider",
        }

        service = MarketStreamService(socketio=None)
        response = service.get_quote("TSLA")

        self.assertTrue(response["success"])
        self.assertIn("data", response)
        self.assertEqual(response["data"]["symbol"], "TSLA")

    def test_symbol_validation_failure(self):
        service = MarketStreamService(socketio=None)
        response = service.get_quote("$$$")
        self.assertFalse(response["success"])
        self.assertIn("invalid", response["error"].lower())

    def test_watchlist_is_bounded_and_lru(self):
        with patch.dict(os.environ, {"PLUTO_MARKET_MAX_SYMBOLS": "8"}):
            service = MarketStreamService(socketio=None)
            for idx in range(30):
                service.add_symbol(f"T{idx}")

            symbols = service.symbols()
            self.assertLessEqual(len(symbols), 8)
            self.assertIn("T29", symbols)

    def test_missing_alpaca_credentials_falls_back_cleanly(self):
        fake_socket = FakeSocketIO()
        # The polling fallback must succeed deterministically; with the network disabled (or, before, with
        # a rate-limited Yahoo) it would overwrite last_error with "polling quotes unavailable".
        quotes = lambda symbols: {s: {"symbol": s, "price": 100.0, "bid": None, "ask": None, "volume": 1, "timestamp": "2026-01-01T00:00:00+00:00", "source_status": "DELAYED", "provider": "test"} for s in symbols}  # noqa: E731
        with patch.dict(os.environ, {"PLUTO_MARKET_PROVIDER": "alpaca", "ALPACA_API_KEY": "", "ALPACA_API_SECRET": ""}), patch(
            "backend.services.live_market_stream.YFinancePollingProvider.get_quotes", side_effect=quotes
        ):
            service = MarketStreamService(socketio=fake_socket)
            service.start()
            status = service.connection_status()
            service.stop()

        self.assertEqual(status["state"], "polling")
        self.assertTrue(status["fallback_active"])
        self.assertIn("credentials", (status.get("last_error") or "").lower())
        event_names = [name for name, _ in fake_socket.events]
        self.assertIn("connection_status", event_names)
        self.assertIn("fallback_activated", event_names)

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

        names = [name for name, _ in fake_socket.events]
        self.assertIn("connection_status", names)
        self.assertIn("fallback_activated", names)
        self.assertIn("stream_recovered", names)
        self.assertIn("market_data_error", names)

    def test_multiple_browser_clients_receive_updates(self):
        fake_socket = FakeSocketIO()
        service = MarketStreamService(socketio=fake_socket)

        c1 = []
        c2 = []
        fake_socket.add_listener(lambda event, payload: c1.append((event, payload)))
        fake_socket.add_listener(lambda event, payload: c2.append((event, payload)))

        service._broadcast_quote(
            {
                "symbol": "TSLA",
                "price": 250.1,
                "provider": "YFinancePollingProvider",
                "source_status": "DELAYED",
                "timestamp": "2026-07-21T00:00:00+00:00",
            }
        )

        self.assertTrue(any(name == "market_update" for name, _ in c1))
        self.assertTrue(any(name == "market_update" for name, _ in c2))

    def test_alpaca_provider_ignores_malformed_messages(self):
        health = ConnectionHealthService()
        seen = []
        provider = AlpacaStreamingProvider(lambda q: seen.append(q), health)

        provider._on_message(None, "{bad json")
        provider._on_message(None, json.dumps({"x": 1}))
        provider._on_message(None, json.dumps([{"T": "unknown"}]))
        provider._on_message(None, json.dumps([{"T": "q", "S": "", "bp": 1}]))

        self.assertEqual(len(seen), 0)

    def test_alpaca_provider_parses_quote_and_trade(self):
        health = ConnectionHealthService()
        seen = []
        provider = AlpacaStreamingProvider(lambda q: seen.append(q), health)

        provider._on_message(None, json.dumps([{"T": "q", "S": "TSLA", "bp": 250.1, "ap": 250.2, "bs": 10, "as": 10}]))
        provider._on_message(None, json.dumps([{"T": "t", "S": "TSLA", "p": 250.15, "s": 100}]))
        provider_health = provider.health()

        self.assertEqual(len(seen), 2)
        self.assertEqual(provider_health["message_counts"]["quote"], 1)
        self.assertEqual(provider_health["message_counts"]["trade"], 1)

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

        provider = AlpacaStreamingProvider(lambda _q: None, health, on_event=lambda name, payload: events.append((name, payload)))
        provider._key = "valid"
        provider._secret = "valid"

        original_websocket = stream_mod.websocket
        stream_mod.websocket = types.SimpleNamespace(WebSocketApp=FakeWebSocketApp)
        try:
            provider.start(["TSLA", "SPY"])
            time.sleep(1.5)
            provider.stop()
        finally:
            stream_mod.websocket = original_websocket

        self.assertGreaterEqual(connections["count"], 2)
        self.assertTrue(any('"action": "auth"' in row for row in sent_messages))
        self.assertTrue(any('"action": "subscribe"' in row for row in sent_messages))
        event_names = [name for name, _ in events]
        self.assertIn("provider_connected", event_names)
        self.assertIn("provider_subscribe", event_names)

    @patch("backend.services.live_market_stream.YFinancePollingProvider.get_quotes")
    def test_double_start_does_not_spawn_duplicate_poll_threads(self, mock_get_quotes):
        mock_get_quotes.return_value = {}
        service = MarketStreamService(socketio=None)
        service.start()
        first = service._poll_thread
        service.start()
        second = service._poll_thread
        service.stop()

        self.assertIs(first, second)

    def test_virtual_30_minute_soak_stability(self):
        with patch.dict(os.environ, {"PLUTO_MARKET_MAX_SYMBOLS": "32", "PLUTO_MARKET_CACHE_SECONDS": "30", "PLUTO_MARKET_STALE_SECONDS": "300"}):
            service = MarketStreamService(socketio=None)

        # Simulate 30 minutes (1800 ticks) of updates across a large symbol set.
        for tick in range(1800):
            symbol = f"T{tick % 120}"
            service._broadcast_quote(
                {
                    "symbol": symbol,
                    "price": float(100 + (tick % 25)),
                    "provider": "YFinancePollingProvider",
                    "source_status": "DELAYED",
                    "timestamp": "2026-07-21T00:00:00+00:00",
                }
            )
            if tick % 25 == 0:
                service._cache.cleanup_stale()

        status = service.connection_status()
        self.assertLessEqual(status["cache_size"], status["max_symbols"])
        self.assertLessEqual(len(status["watch_symbols"]), status["max_symbols"])

    @patch("backend.services.live_market_stream.YFinancePollingProvider.get_quotes")
    def test_no_thread_leak_on_start_stop_cycles(self, mock_get_quotes):
        mock_get_quotes.return_value = {}
        service = MarketStreamService(socketio=None)
        saw_poll_thread = False

        for _ in range(3):
            service.start()
            if service._poll_thread is not None:
                saw_poll_thread = True
            time.sleep(0.05)
            service.stop()

        self.assertTrue(saw_poll_thread)
        self.assertFalse(service._poll_thread.is_alive() if service._poll_thread else False)


if __name__ == "__main__":
    unittest.main()
