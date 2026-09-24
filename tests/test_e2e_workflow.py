"""Full workflow through the HTTP API with the default simulated broker and mocked market data:
webhook -> analysis -> approval -> entry -> protection -> exit -> performance, including restarts."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.services.tradingview_signals import TradingViewSignalService

from support import PriceFeed, login, make_app, patched_prices, post_webhook, webhook_payload


class EndToEndWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.feed = PriceFeed(TSLA=100.0, NVDA=200.0)

    def boot(self):
        """(Re)start the whole application against the same on-disk state."""
        with patched_prices(self.feed):  # startup reconciliation runs a monitor pass and reads prices
            app, patcher = make_app(self.tmp.name)
        self.addCleanup(patcher.stop)
        client = app.test_client()
        login(client)
        return app, client

    def send(self, client, **overrides):
        response = post_webhook(client, webhook_payload(**overrides))
        self.assertEqual(response.status_code, 202, response.get_json())
        return response.get_json()["data"]["signal_id"]

    def approve(self, client, signal_id):
        with patched_prices(self.feed):
            return client.post(f"/api/signals/{signal_id}/approve", json={"note": "e2e"})

    def get(self, client, path):
        with patched_prices(self.feed):
            return client.get(path).get_json()["data"]

    def run_monitor(self, client):
        with patched_prices(self.feed):
            response = client.post("/api/paper/monitor/run")
        self.assertEqual(response.status_code, 200)

    def test_long_trade_survives_restart_and_closes_on_target(self):
        _app, client = self.boot()
        signal_id = self.send(client)
        self.assertEqual(client.get(f"/api/signals/{signal_id}").get_json()["data"]["status"], "AWAITING_APPROVAL")

        response = self.approve(client, signal_id)
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()["data"]["signal"]["status"], "PAPER_EXECUTED")
        trade = self.get(client, "/api/paper/trades")["trades"][0]
        self.assertEqual(trade["status"], "OPEN")
        self.assertTrue(trade["stop_order_id"] and trade["target_order_id"], "protection must be live after entry")
        account = self.get(client, "/api/paper/account")
        self.assertEqual((account["cash"], account["open_positions"]), (90000.0, 1))

        # --- restart: brand-new app, engine and broker objects over the same files ---------
        _app2, client2 = self.boot()
        self.assertEqual(self.get(client2, "/api/paper/account")["cash"], 90000.0)
        positions = self.get(client2, "/api/paper/positions")["positions"]
        self.assertEqual([(p["symbol"], p["quantity"], p["status"]) for p in positions], [("TSLA", 100, "OPEN")])
        self.assertEqual(client2.get(f"/api/signals/{signal_id}").get_json()["data"]["status"], "PAPER_EXECUTED")
        self.assertFalse(self.get(client2, "/api/paper/monitor")["freeze_new_entries"])
        self.assertEqual(self.approve(client2, signal_id).get_json()["data"]["idempotent"], True)  # no duplicate entry after restart
        self.assertEqual(len(self.get(client2, "/api/paper/trades")["trades"]), 1)

        # --- price reaches the target: the restarted app's monitor must complete the exit ----
        self.feed.set("TSLA", 110.0)
        self.run_monitor(client2)
        trade = self.get(client2, "/api/paper/trades")["trades"][0]
        self.assertEqual((trade["status"], trade["exit_type"], trade["avg_exit_price"], trade["net_realized_pl"]), ("CLOSED", "target", 110.0, 1000.0))
        account = self.get(client2, "/api/paper/account")
        self.assertEqual((account["cash"], account["equity"], account["open_positions"], account["realized_pl"]), (101000.0, 101000.0, 0, 1000.0))
        performance = self.get(client2, "/api/paper/performance")
        self.assertEqual((performance["trades_total"], performance["wins"], performance["losses"], performance["expectancy"]), (1, 1, 0, 1000.0))

        # --- a second restart after the close must not resurrect or double count anything ---
        _app3, client3 = self.boot()
        self.assertEqual(self.get(client3, "/api/paper/account")["cash"], 101000.0)
        self.assertEqual(len(self.get(client3, "/api/paper/trades")["trades"]), 1)

    def test_short_trade_stopped_out_then_manual_exit_of_second_trade(self):
        _app, client = self.boot()
        short_id = self.send(client, direction="SHORT", stop_loss=105.0, take_profit=90.0)
        self.assertEqual(self.approve(client, short_id).status_code, 200)
        self.feed.set("TSLA", 106.0)
        self.run_monitor(client)
        trade = self.get(client, "/api/paper/trades")["trades"][0]
        self.assertEqual((trade["exit_type"], trade["net_realized_pl"]), ("stop", -600.0))

        long_id = self.send(client, symbol="NVDA", price=200.0, stop_loss=190.0, take_profit=230.0, signal_id="NVDA-1-long")
        self.assertEqual(self.approve(client, long_id).status_code, 200)
        self.feed.set("NVDA", 210.0)
        exit_id = self.send(client, symbol="NVDA", direction="EXIT", price=210.0, signal_id="NVDA-1-exit", stop_loss=None, take_profit=None)
        response = self.approve(client, exit_id)
        self.assertEqual(response.status_code, 200, response.get_json())
        nvda = [t for t in self.get(client, "/api/paper/trades")["trades"] if t["symbol"] == "NVDA"][0]
        self.assertEqual((nvda["status"], nvda["exit_type"]), ("CLOSED", "manual"))
        self.assertGreater(nvda["net_realized_pl"], 0)
        performance = self.get(client, "/api/paper/performance")
        self.assertEqual((performance["trades_total"], performance["wins"], performance["losses"]), (2, 1, 1))
        account = self.get(client, "/api/paper/account")
        self.assertEqual(account["open_positions"], 0)
        self.assertEqual(account["equity"], round(account["starting_balance"] + account["realized_pl"], 2))

    def test_declined_signal_never_reaches_the_ledger(self):
        _app, client = self.boot()
        signal_id = self.send(client)
        self.assertEqual(client.post(f"/api/signals/{signal_id}/decline", json={"reason": "no"}).status_code, 200)
        self.assertEqual(self.approve(client, signal_id).status_code, 409)
        self.assertEqual(self.get(client, "/api/paper/trades")["trades"], [])
        self.assertEqual(self.get(client, "/api/paper/account")["cash"], 100000.0)

    def test_restart_recovers_unanalyzed_and_interrupted_approval_signals(self):
        signals = Path(self.tmp.name) / "signals.json"
        seed = TradingViewSignalService(analyzer=lambda s: {}, data_path=str(signals), approvals_path=str(Path(self.tmp.name) / "approvals.json"), webhook_secret="unit-secret")
        ok, _code, body = seed.normalize_and_store({**webhook_payload(signal_id="never-analyzed"), "secret": "unit-secret"})  # process dies before analysis
        self.assertTrue(ok, body)
        ok, _code, body = seed.normalize_and_store({**webhook_payload(signal_id="approved-no-exec", price=101.0, stop_loss=96.0, take_profit=111.0), "secret": "unit-secret"})
        seed.process_signal("approved-no-exec")
        seed.update_signal("approved-no-exec", lambda row: {**row, "analysis": {"entry_zone": 101.0, "data_freshness": "DELAYED", "warnings": []}})
        seed.begin_approval("approved-no-exec", "alice")  # process dies between approval and execution
        seed.update_signal("approved-no-exec", lambda row: {**row, "approval": {**row["approval"], "timestamp": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()}})
        ledger_before = json.loads(signals.read_text())
        self.assertEqual({r["signal_id"]: r["status"] for r in ledger_before["signals"]}, {"never-analyzed": "VALIDATED", "approved-no-exec": "APPROVED"})

        _app, client = self.boot()
        statuses = {r["signal_id"]: r["status"] for r in client.get("/api/signals").get_json()["data"]["signals"]}
        self.assertEqual(statuses["never-analyzed"], "AWAITING_APPROVAL")
        self.assertEqual(statuses["approved-no-exec"], "AWAITING_APPROVAL")  # nothing was executed, a human must decide again
        self.assertEqual(self.get(client, "/api/paper/trades")["trades"], [])

    def test_restart_after_execution_but_before_outcome_recorded_settles_as_executed(self):
        _app, client = self.boot()
        signal_id = self.send(client)
        self.assertEqual(self.approve(client, signal_id).status_code, 200)
        signals = Path(self.tmp.name) / "signals.json"
        data = json.loads(signals.read_text())
        row = data["signals"][0]  # simulate the crash: lifecycle exists but the signal still says APPROVED
        row["status"], row["approval"]["timestamp"] = "APPROVED", (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        signals.write_text(json.dumps(data))
        _app2, client2 = self.boot()
        self.assertEqual(client2.get(f"/api/signals/{signal_id}").get_json()["data"]["status"], "PAPER_EXECUTED")
        self.assertEqual(len(self.get(client2, "/api/paper/trades")["trades"]), 1)

    def test_orphaned_broker_position_freezes_new_entries_after_restart(self):
        _app, client = self.boot()
        signal_id = self.send(client)
        self.assertEqual(self.approve(client, signal_id).status_code, 200)
        ledger_path = Path(self.tmp.name) / "paper_ledger.json"
        ledger = json.loads(ledger_path.read_text())
        ledger["lifecycles"] = []  # ledger lost its lifecycle record but the broker still holds the shares
        ledger_path.write_text(json.dumps(ledger))
        _app2, client2 = self.boot()
        monitor = self.get(client2, "/api/paper/monitor")
        self.assertTrue(monitor["freeze_new_entries"])
        alerts = self.get(client2, "/api/paper/alerts")["alerts"]
        self.assertTrue(any("ORPHAN" in a["alert_key"] for a in alerts))


if __name__ == "__main__":
    unittest.main()
