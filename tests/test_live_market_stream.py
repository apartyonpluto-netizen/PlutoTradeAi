import unittest
from unittest.mock import patch

from backend.services.live_market_stream import MarketDataCache, MarketStreamService


class LiveMarketStreamTests(unittest.TestCase):
    def test_cache_live_then_stale(self):
        cache = MarketDataCache(ttl_seconds=1, stale_seconds=5)
        row = cache.update("TSLA", {"symbol": "TSLA", "price": 100.0, "provider": "Test", "source_status": "LIVE"})
        self.assertIsNotNone(row)

        cached, status, age = cache.get("TSLA")
        self.assertIsNotNone(cached)
        self.assertEqual(status, "live")
        self.assertIsNotNone(age)

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


if __name__ == "__main__":
    unittest.main()
