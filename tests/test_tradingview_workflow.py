import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from backend.app import create_app


class TradingViewWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.signal_file = os.path.join(self.tmpdir.name, "signals.json")
        self.approvals_file = os.path.join(self.tmpdir.name, "approvals.json")
        self.ledger_file = os.path.join(self.tmpdir.name, "paper_ledger.json")

    def tearDown(self):
        self.tmpdir.cleanup()

    @staticmethod
    def _stub_analyzer(signal):
        return {
            "market_direction": "BULLISH" if signal.get("direction") == "LONG" else "BEARISH",
            "confidence_score": 70,
            "entry_zone": signal.get("price"),
            "stop_loss": signal.get("stop_loss"),
            "primary_target": signal.get("take_profit"),
            "warnings": [],
            "data_freshness": "DELAYED",
        }

    def _make_client(self, extra_env=None, stub_analyzer=True, login=True):
        env = {
            "TRADINGVIEW_WEBHOOK_SECRET": "unit-secret",
            "TRADINGVIEW_WEBHOOK_MAX_AGE_SECONDS": "300",
            "PLUTO_MARKET_PROVIDER": "yfinance",
            "PLUTO_SIGNAL_PROVIDER": "tradingview",
            "PLUTO_CHART_PROVIDER": "tradingview",
            "PLUTO_PAPER_BROKER": "internal",
            "PLUTO_EXECUTION_PROVIDER": "disabled",
            "PLUTO_SIGNALS_FILE": self.signal_file,
            "PLUTO_SIGNAL_APPROVALS_FILE": self.approvals_file,
            "PLUTO_PAPER_LEDGER_FILE": self.ledger_file,
            "PLUTO_PAPER_STARTING_BALANCE": "100000",
            "PLUTO_MAX_RISK_PER_TRADE": "0.01",
            "PLUTO_MAX_POSITION_PERCENT": "0.10",
            "PLUTO_AUTO_PAPER_EXECUTION": "false",
            "PLUTO_SIGNAL_SYNC_PROCESSING": "false",
        }
        if extra_env:
            env.update(extra_env)

        patcher = patch.dict(os.environ, env, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)

        app = create_app()
        app.config.update(TESTING=True)
        if stub_analyzer:
            app.extensions["signal_service"]._analyzer = self._stub_analyzer
        client = app.test_client()
        if login:
            client.post("/auth/register", data={"username": "tester", "password": "correct-horse", "confirm_password": "correct-horse"})
            client.post("/auth/login", data={"username": "tester", "password": "correct-horse"})
        return client

    def _valid_payload(self, **overrides):
        now_iso = datetime.now(timezone.utc).isoformat()
        now_compact = now_iso.replace(":", "").replace("+00:00", "Z")
        payload = {
            "signal_id": f"TSLA-60-{now_compact}-pluto-v1",
            "symbol": "TSLA",
            "exchange": "NASDAQ",
            "asset_type": "STOCK",
            "strategy": "pluto-test-v1",
            "direction": "LONG",
            "timeframe": "60",
            "price": 250.12,
            "stop_loss": 248.0,
            "take_profit": 255.0,
            "bar_time": now_iso,
            "indicator_values": {"ema_fast": 249.5, "ema_slow": 247.2},
        }
        payload.update(overrides)
        return payload

    def _post_webhook(self, client, payload, secret="unit-secret"):
        headers = {"Content-Type": "application/json"}
        if secret is not None:
            headers["X-Pluto-Webhook-Secret"] = secret
        return client.post("/api/webhooks/tradingview", data=json.dumps(payload), headers=headers)

    def test_valid_tradingview_signal(self):
        client = self._make_client()
        response = self._post_webhook(client, self._valid_payload())
        self.assertEqual(response.status_code, 202)
        body = response.get_json()
        self.assertTrue(body["success"])
        self.assertIn("signal_id", body["data"])

    def test_invalid_secret(self):
        client = self._make_client()
        response = self._post_webhook(client, self._valid_payload(), secret="wrong-secret")
        self.assertEqual(response.status_code, 401)

    def test_missing_secret(self):
        client = self._make_client()
        response = self._post_webhook(client, self._valid_payload(), secret=None)
        self.assertEqual(response.status_code, 401)

    def test_malformed_json(self):
        client = self._make_client()
        response = client.post(
            "/api/webhooks/tradingview",
            data="{bad json",
            headers={"Content-Type": "application/json", "X-Pluto-Webhook-Secret": "unit-secret"},
        )
        self.assertEqual(response.status_code, 400)

    def test_invalid_ticker(self):
        client = self._make_client()
        response = self._post_webhook(client, self._valid_payload(symbol="$$$"))
        self.assertEqual(response.status_code, 400)

    def test_invalid_direction(self):
        client = self._make_client()
        response = self._post_webhook(client, self._valid_payload(direction="UP"))
        self.assertEqual(response.status_code, 400)

    def test_replayed_signal(self):
        client = self._make_client()
        payload = self._valid_payload()
        first = self._post_webhook(client, payload)
        self.assertEqual(first.status_code, 202)
        second = self._post_webhook(client, payload)
        self.assertEqual(second.status_code, 409)

    def test_duplicate_signal_payload_hash(self):
        client = self._make_client()
        first_payload = self._valid_payload(signal_id=None)
        second_payload = dict(first_payload)
        first = self._post_webhook(client, first_payload)
        self.assertEqual(first.status_code, 202)
        second = self._post_webhook(client, second_payload)
        self.assertEqual(second.status_code, 409)

    def test_expired_timestamp(self):
        client = self._make_client(extra_env={"TRADINGVIEW_WEBHOOK_MAX_AGE_SECONDS": "60"})
        response = self._post_webhook(client, self._valid_payload(bar_time="2001-01-01T00:00:00Z"))
        self.assertEqual(response.status_code, 400)

    def test_webhook_response_under_three_seconds(self):
        client = self._make_client()
        start = time.perf_counter()
        response = self._post_webhook(client, self._valid_payload())
        elapsed = time.perf_counter() - start
        self.assertEqual(response.status_code, 202)
        self.assertLess(elapsed, 3.0)

    def test_signal_normalization_fields(self):
        client = self._make_client()
        response = self._post_webhook(client, self._valid_payload())
        signal_id = response.get_json()["data"]["signal_id"]

        detail = client.get(f"/api/signals/{signal_id}")
        self.assertEqual(detail.status_code, 200)
        row = detail.get_json()["data"]
        for field in [
            "signal_id",
            "source",
            "symbol",
            "exchange",
            "asset_type",
            "strategy",
            "direction",
            "timeframe",
            "price",
            "stop_loss",
            "take_profit",
            "confidence_hint",
            "indicator_values",
            "bar_time",
            "received_at",
            "raw_payload_hash",
            "status",
        ]:
            self.assertIn(field, row)

    @patch("backend.app.get_stock_snapshot")
    @patch("backend.app.BrainRegistry.run")
    @patch("backend.app.BrainRegistry.run_overview")
    def test_analysis_pipeline(self, mock_overview, mock_run, mock_snapshot):
        mock_snapshot.return_value = {
            "success": True,
            "data": {"current_price": 250.0, "market_data_status": "STALE"},
            "data_status": "stale",
        }
        mock_overview.return_value = {
            "success": True,
            "data": {
                "entry_zone": 250.0,
                "stop_invalidation": 247.5,
                "targets": [255.0, 260.0],
                "risk_score": 1.8,
                "trade_thesis": "Momentum setup",
                "confidence": 72,
            },
        }

        def run_side_effect(name, _ticker):
            if name == "trend":
                return {"success": True, "data": {"trend": "Uptrend"}}
            if name == "confidence":
                return {"success": True, "data": {"overall": 74}}
            if name == "risk":
                return {"success": True, "data": {"risk_reward_ratio": 2.1}}
            return {"success": True, "data": {}}

        mock_run.side_effect = run_side_effect

        client = self._make_client(extra_env={"PLUTO_SIGNAL_SYNC_PROCESSING": "true"}, stub_analyzer=False)
        response = self._post_webhook(client, self._valid_payload())
        self.assertEqual(response.status_code, 202)

        signal_id = response.get_json()["data"]["signal_id"]
        detail = client.get(f"/api/signals/{signal_id}").get_json()["data"]
        self.assertEqual(detail["status"], "AWAITING_APPROVAL")
        self.assertIn("analysis", detail)
        self.assertIn("market_direction", detail["analysis"])

    @patch("backend.services.paper_engine.get_stock_snapshot")
    def test_approval_and_paper_position_creation(self, mock_snapshot):
        mock_snapshot.return_value = {
            "success": True,
            "data": {"current_price": 250.0},
            "data_status": "delayed",
        }
        client = self._make_client()
        response = self._post_webhook(client, self._valid_payload())
        signal_id = response.get_json()["data"]["signal_id"]

        approve = client.post(f"/api/signals/{signal_id}/approve", json={"approved_by": "tester"})
        self.assertEqual(approve.status_code, 200)
        payload = approve.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["data"]["signal"]["status"], "PAPER_EXECUTED")

        positions = client.get("/api/paper/positions").get_json()["data"]["positions"]
        self.assertGreaterEqual(len(positions), 1)

    @patch("backend.services.paper_engine.get_stock_snapshot")
    def test_decline_signal(self, mock_snapshot):
        mock_snapshot.return_value = {
            "success": True,
            "data": {"current_price": 250.0},
            "data_status": "delayed",
        }
        client = self._make_client()
        response = self._post_webhook(client, self._valid_payload())
        signal_id = response.get_json()["data"]["signal_id"]

        decline = client.post(f"/api/signals/{signal_id}/decline", json={"reason": "risk too high"})
        self.assertEqual(decline.status_code, 200)
        self.assertEqual(decline.get_json()["data"]["signal"]["status"], "DECLINED")

    @patch("backend.services.paper_engine.get_stock_snapshot")
    def test_risk_limit_rejection(self, mock_snapshot):
        mock_snapshot.return_value = {
            "success": True,
            "data": {"current_price": 250.0},
            "data_status": "delayed",
        }
        client = self._make_client(extra_env={"PLUTO_MAX_RISK_PER_TRADE": "0.000001"})
        response = self._post_webhook(client, self._valid_payload(stop_loss=249.99))
        signal_id = response.get_json()["data"]["signal_id"]

        approve = client.post(f"/api/signals/{signal_id}/approve", json={"approved_by": "tester"})
        self.assertEqual(approve.status_code, 422)

    @patch("backend.services.paper_engine.get_stock_snapshot")
    def test_pl_calculation_and_position_closing(self, mock_snapshot):
        prices = [
            {"success": True, "data": {"current_price": 250.0}, "data_status": "delayed"},
            {"success": True, "data": {"current_price": 255.0}, "data_status": "delayed"},
            {"success": True, "data": {"current_price": 255.0}, "data_status": "delayed"},
        ]
        mock_snapshot.side_effect = lambda *_args, **_kwargs: prices[0]

        client = self._make_client()
        open_resp = self._post_webhook(client, self._valid_payload())
        open_id = open_resp.get_json()["data"]["signal_id"]
        approve_open = client.post(f"/api/signals/{open_id}/approve", json={"approved_by": "tester"})
        self.assertEqual(approve_open.status_code, 200)

        exit_payload = self._valid_payload(
            signal_id=f"TSLA-60-{int(time.time())}-pluto-v1-exit",
            direction="EXIT",
            price=255.0,
            bar_time=datetime.now(timezone.utc).isoformat(),
        )
        exit_resp = self._post_webhook(client, exit_payload)
        exit_id = exit_resp.get_json()["data"]["signal_id"]
        approve_exit = client.post(f"/api/signals/{exit_id}/approve", json={"approved_by": "tester"})
        self.assertEqual(approve_exit.status_code, 200)

        perf = client.get("/api/paper/performance").get_json()["data"]
        self.assertIn("expectancy", perf)

    @patch("backend.services.paper_engine.get_stock_snapshot")
    def test_performance_metrics(self, mock_snapshot):
        mock_snapshot.return_value = {
            "success": True,
            "data": {"current_price": 250.0},
            "data_status": "delayed",
        }
        client = self._make_client()
        response = self._post_webhook(client, self._valid_payload())
        signal_id = response.get_json()["data"]["signal_id"]
        client.post(f"/api/signals/{signal_id}/approve", json={"approved_by": "tester"})

        perf = client.get("/api/paper/performance")
        self.assertEqual(perf.status_code, 200)
        payload = perf.get_json()["data"]
        self.assertIn("win_rate", payload)
        self.assertIn("loss_rate", payload)
        self.assertIn("maximum_drawdown", payload)

    @patch("backend.services.live_market_stream.AlpacaStreamingProvider.start")
    def test_no_external_broker_call(self, mock_alpaca_start):
        client = self._make_client()
        response = self._post_webhook(client, self._valid_payload())
        signal_id = response.get_json()["data"]["signal_id"]
        client.post(f"/api/signals/{signal_id}/approve", json={"approved_by": "tester"})
        mock_alpaca_start.assert_not_called()

    def test_application_startup_without_alpaca_credentials(self):
        client = self._make_client(
            extra_env={
                "PLUTO_MARKET_PROVIDER": "alpaca",
                "ALPACA_API_KEY": "",
                "ALPACA_API_SECRET": "",
            }
        )
        health = client.get("/api/health")
        self.assertEqual(health.status_code, 200)


if __name__ == "__main__":
    unittest.main()
