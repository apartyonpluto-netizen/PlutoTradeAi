import unittest
from unittest.mock import patch

from backend.app import create_app
from backend.services.market_scanner_engine import MarketScannerEngine


def _mock_snapshot(symbol):
    symbol = str(symbol or "").upper()
    if symbol in {"INVALID", "$$$"}:
        return {
            "success": False,
            "data": {},
            "error": "Data unavailable",
            "timestamp": "2026-07-22T00:00:00+00:00",
            "data_status": "unavailable",
            "provider": "YFinanceProvider",
        }

    price_map = {
        "AAPL": 220.5,
        "TSLA": 245.0,
        "RKLB": 8.4,
        "SOFI": 7.2,
        "QQQ": 541.4,
        "TQQQ": 95.1,
    }
    pct_map = {
        "AAPL": 1.2,
        "TSLA": 3.9,
        "RKLB": 2.1,
        "SOFI": -1.6,
        "QQQ": 0.8,
        "TQQQ": 2.4,
    }

    price = price_map.get(symbol, 25.0)
    pct = pct_map.get(symbol, 0.4)
    change = round(price * pct / 100.0, 3)

    return {
        "success": True,
        "data": {
            "ticker": symbol,
            "company": symbol,
            "asset_type": "ETF" if symbol in {"QQQ", "TQQQ"} else "Stock",
            "current_price": price,
            "daily_change": change,
            "daily_change_percent": pct,
            "session_status": "regular",
            "market_session": "regular",
            "premarket_price": None,
            "after_hours_price": None,
            "volume": 1000000,
            "relative_volume": 1.7 if symbol in {"TSLA", "TQQQ"} else 1.2,
            "market_cap": 1000000000,
            "previous_close": round(price - change, 3),
            "day_high": round(price * 1.01, 3),
            "day_low": round(price * 0.99, 3),
            "week_52_high": round(price * 1.25, 3),
            "week_52_low": round(price * 0.65, 3),
            "data_source": "YFinanceProvider",
            "last_updated": "2026-07-22T13:42:00+00:00",
            "market_data_status": "DELAYED",
            "market_data_age_seconds": 11,
            "market_object": {
                "ticker": symbol,
                "price": price,
                "provider": "YFinanceProvider",
                "timestamp": "2026-07-22T13:42:00+00:00",
                "market_session": "regular",
                "age_seconds": 11,
                "status": "DELAYED",
            },
        },
        "error": None,
        "timestamp": "2026-07-22T13:42:00+00:00",
        "data_status": "delayed",
        "provider": "YFinanceProvider",
    }


def _mock_chart(symbol, timeframe="3M"):
    symbol = str(symbol or "").upper()
    if symbol in {"INVALID", "$$$"}:
        return {
            "success": False,
            "data": {},
            "error": "Data unavailable",
            "timestamp": "2026-07-22T00:00:00+00:00",
            "data_status": "unavailable",
            "provider": "YFinanceProvider",
        }

    candles = [
        {"time": 1700000000, "open": 10.0, "high": 10.3, "low": 9.9, "close": 10.1},
        {"time": 1700003600, "open": 10.1, "high": 10.5, "low": 10.0, "close": 10.4},
        {"time": 1700007200, "open": 10.4, "high": 10.8, "low": 10.2, "close": 10.7},
        {"time": 1700010800, "open": 10.7, "high": 11.0, "low": 10.6, "close": 10.9},
        {"time": 1700014400, "open": 10.9, "high": 11.2, "low": 10.8, "close": 11.1},
    ]

    return {
        "success": True,
        "data": {
            "ticker": symbol,
            "timeframe": timeframe,
            "market_session": "regular",
            "candles": candles,
            "volume": [{"time": row["time"], "value": 100000} for row in candles],
            "indicators": {
                "ema9": 10.8,
                "ema20": 10.6,
                "ema50": 10.2,
                "ema200": 9.7,
                "vwap": 10.75,
                "major_support": 10.2,
                "major_resistance": 11.2,
                "breakout_level": 11.0,
                "breakdown_level": 10.0,
                "reversal_zone_low": 10.3,
                "reversal_zone_high": 11.0,
                "premarket_high": None,
                "premarket_low": None,
                "after_hours_high": None,
                "after_hours_low": None,
                "ema9_series": [],
                "ema20_series": [],
                "ema50_series": [],
                "ema200_series": [],
                "vwap_series": [],
            },
            "last_updated": "2026-07-22T13:42:00+00:00",
            "market_data_status": "DELAYED",
            "market_data_age_seconds": 8,
            "market_object": {
                "ticker": symbol,
                "price": 11.1,
                "provider": "YFinanceProvider",
                "timestamp": "2026-07-22T13:42:00+00:00",
                "market_session": "regular",
                "age_seconds": 8,
                "status": "DELAYED",
            },
        },
        "error": None,
        "timestamp": "2026-07-22T13:42:00+00:00",
        "data_status": "delayed",
        "provider": "YFinanceProvider",
    }


class _FakeTicker:
    def __init__(self, symbol):
        self.symbol = symbol
        self.info = {
            "exchange": "NASDAQ",
            "longName": f"{symbol} Holdings",
            "shortName": symbol,
            "quoteType": "ETF" if symbol in {"QQQ", "TQQQ"} else "EQUITY",
            "sector": "Technology",
            "industry": "Software",
            "marketCap": 1234567890,
        }
        self.news = [
            {
                "title": f"{symbol} moves on earnings",
                "publisher": "Newswire",
                "link": "https://example.com/story",
                "providerPublishTime": 1700000000,
                "type": "STORY",
            }
        ]
        self.calendar = {}


class FullMarketResearchApiTests(unittest.TestCase):
    def setUp(self):
        self.patches = [
            patch("backend.services.symbol_universe_service.SymbolUniverseService.ensure_fresh", autospec=True, return_value={"symbol_count": 4}),
            patch(
                "backend.services.symbol_universe_service.SymbolUniverseService.search",
                autospec=True,
                side_effect=lambda _self, q, limit=20: [
                    {"symbol": "SOFI", "name": "SoFi Technologies", "exchange": "NASDAQ", "asset_type": "stock"},
                    {"symbol": "RKLB", "name": "Rocket Lab USA", "exchange": "NASDAQ", "asset_type": "stock"},
                    {"symbol": "QQQ", "name": "Invesco QQQ Trust", "exchange": "NASDAQ", "asset_type": "etf"},
                ][:limit],
            ),
            patch(
                "backend.services.symbol_universe_service.SymbolUniverseService.resolve",
                autospec=True,
                side_effect=lambda _self, s: {
                    "symbol": str(s).upper(),
                    "name": f"{str(s).upper()} Inc.",
                    "exchange": "NASDAQ",
                    "asset_type": "etf" if str(s).upper() in {"QQQ", "TQQQ"} else "stock",
                    "is_active": True,
                },
            ),
            patch(
                "backend.services.symbol_universe_service.SymbolUniverseService.symbols",
                autospec=True,
                return_value=[
                    {"symbol": "TSLA", "name": "Tesla", "exchange": "NASDAQ", "asset_type": "stock"},
                    {"symbol": "RKLB", "name": "Rocket Lab", "exchange": "NASDAQ", "asset_type": "stock"},
                    {"symbol": "QQQ", "name": "Invesco QQQ", "exchange": "NASDAQ", "asset_type": "etf"},
                ],
            ),
            patch(
                "backend.services.symbol_universe_service.SymbolUniverseService.status",
                autospec=True,
                return_value={
                    "symbol_count": 3,
                    "last_refresh": "2026-07-22T13:42:00+00:00",
                    "refresh_age_seconds": 12,
                    "source": "nasdaqtrader",
                    "refresh_interval_seconds": 43200,
                    "error": None,
                },
            ),
            patch("backend.services.research_service.get_stock_snapshot", side_effect=_mock_snapshot),
            patch("backend.services.research_service.get_chart_data", side_effect=_mock_chart),
            patch("backend.services.research_service.yf.Ticker", side_effect=lambda symbol: _FakeTicker(symbol)),
            patch("backend.brains.registry.BrainRegistry.run_overview", return_value={"success": True, "data": {"confidence": 77, "bull_case": "Bull", "bear_case": "Bear", "entry_zone": 10.8, "stop_invalidation": 10.2, "targets": [11.5, 12.1], "risk_score": 2.3, "data_quality": "delayed", "components": {"confidence": {"reasons": ["Volume is above normal"]}}}, "timestamp": "2026-07-22T13:42:00+00:00"}),
            patch("backend.brains.registry.BrainRegistry.run", return_value={"success": True, "data": {"trend": "Uptrend", "risk_status": "MODERATE"}}),
        ]
        for p in self.patches:
            p.start()

        app = create_app()
        app.config.update(TESTING=True)
        self.client = app.test_client()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()

    def test_company_name_search(self):
        response = self.client.get("/api/symbols/search?q=sofi")
        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["success"])
        self.assertTrue(any(item["symbol"] == "SOFI" for item in payload["data"]["items"]))

    def test_lowercase_normalization_and_non_seeded_symbol(self):
        response = self.client.get("/api/stocks/rklb/resolve")
        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["success"])
        self.assertEqual(payload["data"]["normalized_symbol"], "RKLB")

    def test_etf_research_overview(self):
        response = self.client.get("/api/stocks/qqq/overview")
        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["success"])
        self.assertEqual(payload["data"]["identity"]["asset_type"], "etf")
        self.assertIn("market_data", payload["data"])
        self.assertIn("technicals", payload["data"])

    def test_invalid_ticker_error(self):
        response = self.client.get("/api/stocks/$$$/resolve")
        payload = response.get_json()
        self.assertEqual(response.status_code, 400)
        self.assertFalse(payload["success"])

    def test_partial_metadata_path(self):
        with patch("backend.services.research_service.ResearchService._fetch_fundamentals", return_value={}):
            response = self.client.get("/api/stocks/sofi/resolve")
            payload = response.get_json()
            self.assertEqual(response.status_code, 200)
            self.assertTrue(payload["success"])
            self.assertIn("identity", payload["data"])

    def test_quote_and_history_and_analysis_contracts(self):
        quote = self.client.get("/api/stocks/tsla/quote").get_json()
        history = self.client.get("/api/stocks/tsla/history?timeframe=1M").get_json()
        technicals = self.client.get("/api/stocks/tsla/technicals").get_json()
        analysis = self.client.get("/api/stocks/tsla/analysis").get_json()
        news = self.client.get("/api/stocks/tsla/news").get_json()

        self.assertTrue(quote["success"])
        self.assertIn("price", quote["data"])
        self.assertTrue(history["success"])
        self.assertIn("candles", history["data"])
        self.assertTrue(technicals["success"])
        self.assertIn("moving_averages", technicals["data"])
        self.assertTrue(analysis["success"])
        self.assertIn("confidence_score", analysis["data"])
        self.assertTrue(news["success"])
        self.assertIn("news", news["data"])

    def test_market_scanners_and_movers_contracts(self):
        scanners = self.client.get("/api/market/scanners").get_json()
        movers = self.client.get("/api/market/movers").get_json()
        one = self.client.get("/api/market/scanners/top_gainers").get_json()

        self.assertTrue(scanners["success"])
        self.assertIn("scanners", scanners["data"])
        self.assertTrue(movers["success"])
        self.assertIn("top_gainers", movers["data"])
        self.assertTrue(one["success"])
        self.assertIn("rows", one["data"])


class _FakeUniverse:
    def ensure_fresh(self):
        return {"symbol_count": 3}

    def symbols(self, max_symbols=0):
        rows = [
            {"symbol": "AAA", "name": "AAA Co", "exchange": "NASDAQ", "asset_type": "stock"},
            {"symbol": "BBB", "name": "BBB Co", "exchange": "NASDAQ", "asset_type": "stock"},
            {"symbol": "CCC", "name": "CCC Co", "exchange": "NASDAQ", "asset_type": "stock"},
        ]
        return rows if max_symbols <= 0 else rows[:max_symbols]


class _FakeResearch:
    def provider_map(self):
        return {
            "symbol_directory_provider": "FakeDirectory",
            "quote_provider": "FakeQuote",
            "history_provider": "FakeHistory",
            "fundamentals_provider": "FakeFundamentals",
            "news_provider": "FakeNews",
            "scanner_engine": "PlutoScannerEngine",
            "chart_provider": "TRADINGVIEW",
            "signal_provider": "TRADINGVIEW",
        }

    def quote(self, symbol):
        data = {
            "AAA": {"price": 11, "change_percent": 6.2, "volume": 2000000, "relative_volume": 2.2, "market_cap": 1000, "freshness": {"status": "DELAYED", "age_seconds": 5, "last_updated": "2026-07-22T13:42:00+00:00"}},
            "BBB": {"price": 21, "change_percent": -4.4, "volume": 900000, "relative_volume": 1.1, "market_cap": 900, "freshness": {"status": "DELAYED", "age_seconds": 7, "last_updated": "2026-07-22T13:42:00+00:00"}},
            "CCC": {"price": 31, "change_percent": 1.3, "volume": 4000000, "relative_volume": 3.1, "market_cap": 800, "freshness": {"status": "DELAYED", "age_seconds": 6, "last_updated": "2026-07-22T13:42:00+00:00"}},
        }
        return {"success": True, "data": data[symbol], "data_status": "delayed", "provider": "FakeQuote"}

    def technicals(self, symbol):
        data = {
            "AAA": {"trend": "UPTREND", "momentum": 5.0, "volatility": 3.1, "breakout_status": "BREAKOUT", "gap_status": "GAP_UP", "support_levels": {"major_support": 10.5}, "resistance_levels": {"major_resistance": 11.2}},
            "BBB": {"trend": "DOWNTREND", "momentum": -5.0, "volatility": 2.2, "breakout_status": "BELOW_BREAKOUT", "gap_status": "GAP_DOWN", "support_levels": {"major_support": 20.2}, "resistance_levels": {"major_resistance": 21.5}},
            "CCC": {"trend": "UPTREND", "momentum": 2.3, "volatility": 4.0, "breakout_status": "BREAKOUT", "gap_status": "NO_GAP", "support_levels": {"major_support": 30.4}, "resistance_levels": {"major_resistance": 31.4}},
        }
        return {"success": True, "data": data[symbol], "data_status": "delayed", "provider": "FakeHistory"}

    def analysis(self, symbol):
        confidence = {"AAA": 82, "BBB": 41, "CCC": 74}
        return {"success": True, "data": {"confidence_score": confidence[symbol]}, "data_status": "cached", "provider": "PlutoTradeAI"}


class MarketScannerEngineTests(unittest.TestCase):
    def test_market_mover_ranking_and_empty_state(self):
        engine = MarketScannerEngine(_FakeUniverse(), _FakeResearch())
        movers = engine.movers(force=True)
        self.assertTrue(movers["success"])
        gainers = movers["data"]["top_gainers"]
        self.assertGreaterEqual(len(gainers), 1)
        self.assertEqual(gainers[0]["symbol"], "AAA")

        scanners = engine.scanners(force=True)
        self.assertTrue(scanners["success"])
        self.assertIn("unusual_relative_volume", scanners["data"]["scanners"])

        unknown = engine.scanner_by_name("does_not_exist", force=True)
        self.assertFalse(unknown["success"])


if __name__ == "__main__":
    unittest.main()
