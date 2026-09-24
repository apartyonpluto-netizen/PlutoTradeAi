from pathlib import Path
import time
import unittest
from unittest.mock import patch

from backend.app import create_app
from backend.services import research_service as rs_module


def _ok_snapshot(symbol: str):
    return {
        "success": True,
        "data": {
            "ticker": symbol.upper(),
            "company": symbol.upper(),
            "asset_type": "Stock",
            "current_price": 100.0,
            "daily_change": 1.0,
            "daily_change_percent": 1.0,
            "volume": 100000,
            "relative_volume": 1.4,
            "market_cap": 1000000000,
            "previous_close": 99.0,
            "day_high": 101.0,
            "day_low": 98.0,
            "week_52_high": 130.0,
            "week_52_low": 70.0,
            "market_session": "regular",
            "last_updated": "2026-07-24T12:00:00+00:00",
            "market_data_status": "DELAYED",
            "market_data_age_seconds": 8,
        },
        "error": None,
        "timestamp": "2026-07-24T12:00:00+00:00",
        "data_status": "delayed",
        "provider": "YFinanceProvider",
    }


def _ok_chart(symbol: str, timeframe: str = "3M"):
    candles = [
        {"time": 1700000000, "open": 99.0, "high": 101.0, "low": 98.0, "close": 100.0},
        {"time": 1700003600, "open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0},
        {"time": 1700007200, "open": 101.0, "high": 103.0, "low": 100.0, "close": 102.0},
    ]
    return {
        "success": True,
        "data": {
            "ticker": symbol.upper(),
            "timeframe": timeframe,
            "candles": candles,
            "volume": [{"time": row["time"], "value": 1000} for row in candles],
            "indicators": {
                "ema9": 100.2,
                "ema20": 99.8,
                "ema50": 98.1,
                "ema200": 92.0,
                "vwap": 100.0,
                "major_support": 98.5,
                "major_resistance": 102.5,
                "breakout_level": 102.0,
                "breakdown_level": 98.0,
                "reversal_zone_low": 99.0,
                "reversal_zone_high": 101.5,
            },
            "last_updated": "2026-07-24T12:00:00+00:00",
            "market_data_status": "DELAYED",
            "market_data_age_seconds": 7,
        },
        "error": None,
        "timestamp": "2026-07-24T12:00:00+00:00",
        "data_status": "delayed",
        "provider": "YFinanceProvider",
    }


class _FakeTicker:
    def __init__(self, symbol: str):
        self.info = {
            "exchange": "NASDAQ",
            "longName": f"{symbol.upper()} Corp",
            "quoteType": "EQUITY",
            "sector": None,
            "industry": None,
            "marketCap": None,
        }
        self.news = []
        self.calendar = {}


class ReliabilityAuditTests(unittest.TestCase):
    def setUp(self):
        app = create_app()
        app.config.update(TESTING=True)
        self.client = app.test_client()

    def _patch_success_stack(self):
        return [
            patch("backend.services.symbol_universe_service.SymbolUniverseService.ensure_fresh", autospec=True, return_value={"symbol_count": 10}),
            patch(
                "backend.services.symbol_universe_service.SymbolUniverseService.resolve",
                autospec=True,
                side_effect=lambda _self, symbol: {
                    "symbol": str(symbol).upper(),
                    "name": f"{str(symbol).upper()} Corp",
                    "exchange": "NASDAQ",
                    "asset_type": "stock",
                },
            ),
            patch("backend.services.research_service.get_stock_snapshot", side_effect=lambda symbol: _ok_snapshot(symbol)),
            patch("backend.services.research_service.get_chart_data", side_effect=lambda symbol, timeframe="3M": _ok_chart(symbol, timeframe)),
            patch("backend.services.research_service.yf.Ticker", side_effect=lambda symbol: _FakeTicker(symbol)),
            patch("backend.brains.registry.BrainRegistry.run_overview", return_value={"success": True, "data": {"confidence": 70, "bull_case": "Bull", "bear_case": "Bear", "entry_zone": 100, "stop_invalidation": 98, "targets": [105, 110], "risk_score": 2.0, "data_quality": "DELAYED", "components": {"confidence": {"reasons": ["Volume normal"]}}}, "timestamp": "2026-07-24T12:00:00+00:00"}),
            patch("backend.brains.registry.BrainRegistry.run", return_value={"success": True, "data": {"trend": "Uptrend", "risk_status": "MODERATE"}}),
        ]

    def test_invalid_ticker_returns_400_json(self):
        response = self.client.get("/api/stocks/$$$/resolve")
        payload = response.get_json()
        self.assertEqual(response.status_code, 400)
        self.assertFalse(payload["success"])
        self.assertEqual(payload["data"]["error_kind"], "INVALID_TICKER")

    def test_lowercase_ticker_normalizes(self):
        patches = self._patch_success_stack()
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in reversed(patches)])

        response = self.client.get("/api/stocks/sofi/resolve")
        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["success"])
        self.assertEqual(payload["data"]["normalized_symbol"], "SOFI")

    def test_provider_timeout_classification(self):
        with patch("backend.services.symbol_universe_service.SymbolUniverseService.ensure_fresh", autospec=True, return_value={"symbol_count": 10}), \
             patch("backend.services.symbol_universe_service.SymbolUniverseService.resolve", autospec=True, return_value=None), \
             patch("backend.services.research_service.get_stock_snapshot", return_value={"success": False, "error": "provider timeout reached", "provider": "YFinanceProvider", "data_status": "unavailable", "timestamp": "2026-07-24T12:00:00+00:00"}):
            response = self.client.get("/api/stocks/TEST/resolve")
            payload = response.get_json()
            self.assertEqual(response.status_code, 504)
            self.assertFalse(payload["success"])
            self.assertEqual(payload["data"]["error_kind"], "TEMPORARY_TIMEOUT")

    def test_stale_cache_fallback(self):
        patches = self._patch_success_stack()
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in reversed(patches)])

        app = self.client.application
        service = app.extensions["research_service"]

        payload = service.overview("AAPL")
        self.assertTrue(payload["success"])

        key = "overview:AAPL"
        service._cache[key]["created_at"] = rs_module._now_epoch() - (rs_module.OVERVIEW_TTL_SECONDS + 2)

        stale = service.overview("AAPL")
        self.assertEqual(stale.get("data_status"), "stale")
        self.assertIn("stale_age_seconds", stale.get("data", {}))

    def test_quote_only_response_marks_partial(self):
        patches = self._patch_success_stack()
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in reversed(patches)])

        app = self.client.application
        service = app.extensions["research_service"]

        with patch.object(service, "technicals", return_value={"success": False, "data": {}, "error": "history down", "data_status": "unavailable"}), \
             patch.object(service, "analysis", return_value={"success": False, "data": {}, "error": "analysis down", "data_status": "unavailable"}):
            payload = service.overview("AAPL")
            self.assertTrue(payload["success"])
            self.assertTrue(payload["data"]["availability"]["quote"])
            self.assertFalse(payload["data"]["availability"]["technicals"])

    def test_technical_only_response_marks_partial(self):
        patches = self._patch_success_stack()
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in reversed(patches)])

        app = self.client.application
        service = app.extensions["research_service"]

        with patch.object(service, "quote", return_value={"success": False, "data": {}, "error": "quote down", "data_status": "unavailable"}), \
             patch.object(service, "technicals", return_value={"success": True, "data": {"trend": "UPTREND"}, "error": None, "data_status": "cached"}), \
             patch.object(service, "analysis", return_value={"success": True, "data": {"confidence_score": 50}, "error": None, "data_status": "cached"}), \
             patch.object(service, "news", return_value={"success": True, "data": {"news": []}, "error": None, "data_status": "partial"}):
            payload = service.overview("AAPL")
            self.assertTrue(payload["success"])
            self.assertEqual(payload["data_status"], "unavailable")
            self.assertFalse(payload["data"]["availability"]["quote"])
            self.assertTrue(payload["data"]["availability"]["technicals"])

    def test_missing_news_has_status_note(self):
        patches = self._patch_success_stack()
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in reversed(patches)])

        payload = self.client.get("/api/stocks/AAPL/news").get_json()
        self.assertTrue(payload["success"])
        self.assertIn("status_note", payload["data"])

    def test_missing_fundamentals_are_null(self):
        patches = self._patch_success_stack()
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in reversed(patches)])

        payload = self.client.get("/api/stocks/AAPL/resolve").get_json()
        self.assertTrue(payload["success"])
        identity = payload["data"]["identity"]
        self.assertIsNone(identity["sector"])
        self.assertIsNone(identity["industry"])

    def test_provider_recovery(self):
        patches = self._patch_success_stack()
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in reversed(patches)])

        app = self.client.application
        service = app.extensions["research_service"]
        service._cache.clear()

        first = {"count": 0}

        def flaky_quote(_symbol):
            first["count"] += 1
            if first["count"] == 1:
                return {"success": False, "data": {"error_kind": "PROVIDER_OUTAGE"}, "error": "provider unavailable", "data_status": "unavailable"}
            return {"success": True, "data": {"price": 10.0, "freshness": {"status": "CACHED", "last_updated": "2026-07-24T12:00:00+00:00", "age_seconds": 3}, "reliability": {"provider": "YFinanceProvider"}}, "error": None, "data_status": "cached", "provider": "YFinanceProvider"}

        with patch.object(service, "quote", side_effect=flaky_quote), \
             patch.object(service, "technicals", return_value={"success": True, "data": {"trend": "UPTREND", "reliability": {}}, "error": None, "data_status": "cached"}), \
             patch.object(service, "analysis", return_value={"success": True, "data": {"confidence_score": 70, "reliability": {}}, "error": None, "data_status": "cached"}), \
             patch.object(service, "news", return_value={"success": True, "data": {"news": [], "status_note": "News unavailable. Quote provider healthy. Technical analysis completed.", "reliability": {}}, "error": None, "data_status": "partial"}):
            a = service.overview("AAPL")
            service._cache.clear()
            b = service.overview("AAPL")
            self.assertTrue(a["success"])
            self.assertFalse(a["data"]["availability"]["quote"])
            self.assertTrue(b["success"])
            self.assertTrue(b["data"]["availability"]["quote"])

    def test_api_404_is_json(self):
        response = self.client.get("/api/not-a-real-route")
        self.assertEqual(response.status_code, 404)
        payload = response.get_json()
        self.assertIsInstance(payload, dict)
        self.assertFalse(payload["success"])

    def test_route_health_contract(self):
        api_routes = [
            rule.rule
            for rule in self.client.application.url_map.iter_rules()
            if rule.rule.startswith("/api/") and "GET" in rule.methods
        ]

        sample_values = {
            "ticker": "AAPL",
            "symbol": "AAPL",
            "signal_id": "test",
            "scanner_name": "top_gainers",
            "brain_name": "overview",
        }

        checked = 0
        for route in api_routes:
            if "<" in route:
                built = route
                for key, value in sample_values.items():
                    built = built.replace(f"<{key}>", value)
                if "<" in built:
                    continue
                path = built
            else:
                path = route

            response = self.client.get(path)
            if response.status_code in {200, 400, 404, 409, 422, 500, 503, 504}:
                payload = response.get_json(silent=True)
                self.assertIsInstance(payload, dict, msg=f"Expected JSON at {path}")
                self.assertIn("success", payload, msg=f"Missing success field at {path}")
                checked += 1

        self.assertGreater(checked, 20)

    def test_blank_state_prevention_literals(self):
        with open(Path(__file__).resolve().parents[1] / "backend" / "static" / "app.js", "r", encoding="utf-8") as handle:
            content = handle.read()
        self.assertNotIn('No data.', content)


if __name__ == "__main__":
    unittest.main()
