import unittest
from unittest.mock import patch

from backend.app import create_app


class RouteTests(unittest.TestCase):
    def setUp(self):
        app = create_app()
        app.config.update(TESTING=True)
        self.client = app.test_client()

    def test_page_routes(self):
        paths = [
            "/",
            "/auth/login",
            "/auth/register",
            "/missions",
            "/missions/TSLA",
            "/futures",
            "/stock/SPY",
            "/scanners",
            "/scanners/movers",
            "/scanners/volume",
            "/scanners/reversals",
            "/scanners/breakouts",
            "/scanners/extended-hours",
            "/scanners/futures",
            "/brains",
            "/brains/candle?ticker=SPY",
        ]

        for path in paths:
            response = self.client.get(path)
            self.assertIn(response.status_code, (200, 404), msg=path)

    @patch("backend.app.get_stock_snapshot")
    def test_stock_api_response_schema(self, mock_snapshot):
        mock_snapshot.return_value = {
            "success": True,
            "data": {"ticker": "SPY", "current_price": 500.0},
            "error": None,
            "timestamp": "2026-07-10T00:00:00+00:00",
            "data_status": "delayed",
            "provider": "Yahoo Finance",
        }

        response = self.client.get("/api/stock/SPY")
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertIn("success", payload)
        self.assertIn("data", payload)
        self.assertIn("error", payload)
        self.assertIn("timestamp", payload)
        self.assertIn("data_status", payload)
        self.assertIn("provider", payload)

    @patch("backend.app.get_chart_data")
    def test_chart_api_response_schema(self, mock_chart):
        mock_chart.return_value = {
            "success": True,
            "data": {"ticker": "SPY", "timeframe": "1M", "candles": [], "volume": [], "indicators": {}},
            "error": None,
            "timestamp": "2026-07-10T00:00:00+00:00",
            "data_status": "delayed",
            "provider": "Yahoo Finance",
        }

        response = self.client.get("/api/chart/SPY?timeframe=1M")
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["success"])
        self.assertIn("candles", payload["data"])

    def test_invalid_ticker_rejected(self):
        response = self.client.post("/api/missions/assign", json={"ticker_symbol": "$$$"})
        payload = response.get_json()

        self.assertEqual(response.status_code, 400)
        self.assertFalse(payload["success"])

    @patch("backend.app.get_stock_snapshot")
    @patch("backend.app.ScannerRegistry.run")
    @patch("backend.app.BrainRegistry.run_overview")
    def test_assign_mission_schema(self, mock_brain, mock_scanner, mock_snapshot):
        mock_snapshot.return_value = {
            "success": True,
            "data": {
                "ticker": "TSLA",
                "company": "Tesla",
                "asset_type": "Stock",
                "last_updated": "2026-07-10T00:00:00+00:00",
            },
            "error": None,
            "timestamp": "2026-07-10T00:00:00+00:00",
            "data_status": "delayed",
            "provider": "Yahoo Finance",
        }
        mock_scanner.return_value = {"success": True, "data": {"rows": []}, "error": None}
        mock_brain.return_value = {
            "success": True,
            "data": {
                "confidence": 82,
                "risk_score": 1.9,
                "trade_thesis": "Test thesis",
                "support": 100,
                "resistance": 120,
                "call_put_wait_bias": "CALL",
                "recommended_strategy": "Test strategy",
            },
            "error": None,
        }

        response = self.client.post(
            "/api/missions/assign",
            json={
                "ticker_symbol": "TSLA",
                "mission_type": "Options",
                "quick_action": "Analyze Everywhere",
                "priority": "★★★★★ Critical",
                "assigned_scanners": ["Scanner Center", "Breakout Scanner"],
                "assigned_brains": ["Options Brain", "Risk Brain", "Trade Thesis"],
                "monitoring_flags": ["Monitor Continuously", "Notify on Breakout"],
            },
        )
        payload = response.get_json()

        self.assertEqual(response.status_code, 201)
        self.assertTrue(payload["success"])
        self.assertIn("mission", payload["data"])
        self.assertEqual(payload["data"]["mode"], "anonymous_session")

    @patch("backend.app.save_user_store")
    @patch("backend.app.load_user_store")
    @patch("backend.app.get_stock_snapshot")
    @patch("backend.app.ScannerRegistry.run")
    @patch("backend.app.BrainRegistry.run_overview")
    def test_authenticated_mission_persists(self, mock_brain, mock_scanner, mock_snapshot, mock_load_user_store, mock_save_user_store):
        mock_snapshot.return_value = {
            "success": True,
            "data": {
                "ticker": "NVDA",
                "company": "NVIDIA",
                "asset_type": "Stock",
                "last_updated": "2026-07-10T00:00:00+00:00",
            },
            "error": None,
            "timestamp": "2026-07-10T00:00:00+00:00",
            "data_status": "delayed",
            "provider": "Yahoo Finance",
        }
        mock_scanner.return_value = {"success": True, "data": {"rows": []}, "error": None}
        mock_brain.return_value = {
            "success": True,
            "data": {
                "confidence": 88,
                "risk_score": 1.5,
                "trade_thesis": "Test thesis",
                "support": 100,
                "resistance": 120,
                "call_put_wait_bias": "CALL",
                "recommended_strategy": "Test strategy",
            },
            "error": None,
        }
        mock_load_user_store.return_value = {"missions": []}

        with self.client.session_transaction() as flask_session:
            flask_session["pluto_authenticated"] = True
            flask_session["pluto_user"] = "curtis"

        response = self.client.post(
            "/api/missions/assign",
            json={
                "ticker_symbol": "NVDA",
                "mission_type": "Research",
                "quick_action": "Analyze Everywhere",
                "priority": "★★★★ High",
                "assigned_scanners": ["Scanner Center"],
                "assigned_brains": ["Confidence Engine"],
                "monitoring_flags": ["Monitor Continuously"],
            },
        )

        payload = response.get_json()
        self.assertEqual(response.status_code, 201)
        self.assertTrue(payload["success"])
        self.assertEqual(payload["data"]["mode"], "authenticated_persistent")
        mock_save_user_store.assert_called_once()

    @patch("backend.app.save_user_store")
    @patch("backend.app.get_stock_snapshot")
    @patch("backend.app.ScannerRegistry.run")
    @patch("backend.app.BrainRegistry.run_overview")
    def test_anonymous_mission_stays_session_only(self, mock_brain, mock_scanner, mock_snapshot, mock_save_user_store):
        mock_snapshot.return_value = {
            "success": True,
            "data": {
                "ticker": "SPY",
                "company": "SPDR S&P 500 ETF",
                "asset_type": "ETF",
                "last_updated": "2026-07-10T00:00:00+00:00",
            },
            "error": None,
            "timestamp": "2026-07-10T00:00:00+00:00",
            "data_status": "delayed",
            "provider": "Yahoo Finance",
        }
        mock_scanner.return_value = {"success": True, "data": {"rows": []}, "error": None}
        mock_brain.return_value = {
            "success": True,
            "data": {
                "confidence": 70,
                "risk_score": 2.0,
                "trade_thesis": "Test thesis",
                "support": 100,
                "resistance": 120,
                "call_put_wait_bias": "WAIT",
                "recommended_strategy": "Test strategy",
            },
            "error": None,
        }

        response = self.client.post(
            "/api/missions/assign",
            json={"ticker_symbol": "SPY", "mission_type": "Research"},
        )

        payload = response.get_json()
        self.assertEqual(response.status_code, 201)
        self.assertTrue(payload["success"])
        self.assertEqual(payload["data"]["mode"], "anonymous_session")
        mock_save_user_store.assert_not_called()

    def test_mission_summary_route(self):
        response = self.client.get("/api/missions/summary")
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["success"])
        self.assertIn("mission_control", payload["data"])
        self.assertIn("dashboard", payload["data"])

    def test_futures_route(self):
        response = self.client.get("/api/futures")
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["success"])
        self.assertIn("rows", payload["data"])

    @patch("backend.app._find_mission_by_ticker")
    def test_mission_timeline_route(self, mock_find_mission):
        mock_find_mission.return_value = {
            "ticker": "TSLA",
            "company": "Tesla",
            "priority": "★★★★ High",
            "mission_type": "Options",
            "mission_status": "Active",
            "confidence_history": [{"timestamp": "2026-07-10T00:00:00+00:00", "value": 80}],
            "risk_history": [{"timestamp": "2026-07-10T00:00:00+00:00", "value": 1.8}],
            "trade_thesis_history": [{"timestamp": "2026-07-10T00:00:00+00:00", "value": "Test thesis"}],
            "support_history": [{"timestamp": "2026-07-10T00:00:00+00:00", "value": 200}],
            "resistance_history": [{"timestamp": "2026-07-10T00:00:00+00:00", "value": 250}],
            "options_history": [{"timestamp": "2026-07-10T00:00:00+00:00", "value": {"direction": "CALL"}}],
        }

        response = self.client.get("/api/missions/TSLA/timeline")
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["success"])
        self.assertIn("timeline", payload["data"])

    def test_graceful_404_page(self):
        response = self.client.get("/this-route-does-not-exist")
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
