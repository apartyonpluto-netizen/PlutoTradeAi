import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from backend.services.paper_engine import InternalPaperEngine


class ScenarioBroker:
    def __init__(self):
        self.orders = {}
        self.positions = {}
        self.next_id = 1
        self.submit_entry_calls = 0
        self.cancel_uncertain_order_ids = set()
        self.force_invalid_state = False

    def submit_entry_order(self, payload):
        self.submit_entry_calls += 1
        existing = self._find_by_client_order_id(payload["client_order_id"])
        if existing:
            return dict(existing)
        order = self._new_order(
            symbol=payload["symbol"],
            client_order_id=payload["client_order_id"],
            role="ENTRY",
            side="BUY" if payload.get("direction") == "LONG" else "SELL",
            requested_qty=int(payload["quantity"]),
            price=float(payload["price"]),
            status="SUBMITTED",
        )
        self.orders[order["order_id"]] = order
        self.positions.setdefault(order["symbol"], {"quantity": 0, "avg_price": None})
        return dict(order)

    def submit_exit_order(self, payload):
        existing = self._find_by_client_order_id(payload["client_order_id"])
        if existing:
            return dict(existing)
        order = self._new_order(
            symbol=payload["symbol"],
            client_order_id=payload["client_order_id"],
            role=payload.get("role", "EXIT"),
            side=payload["side"],
            requested_qty=int(payload["quantity"]),
            price=float(payload["price"]),
            status="SUBMITTED",
            order_type=payload.get("order_type", "LIMIT"),
        )
        self.orders[order["order_id"]] = order
        return dict(order)

    def cancel_order(self, order_id):
        order = self.orders[order_id]
        if order_id in self.cancel_uncertain_order_ids:
            order["status"] = "WORKING"
            return dict(order)
        if order["status"] not in {"FILLED", "CANCELLED", "REJECTED", "EXPIRED"}:
            order["status"] = "CANCELLED"
        return dict(order)

    def fetch_state(self, lifecycle):
        if self.force_invalid_state:
            return {"entry_order": None}
        symbol = lifecycle["symbol"]
        entry = self.orders.get(lifecycle.get("entry", {}).get("order_id"))
        stop = self.orders.get(lifecycle.get("protection", {}).get("stop", {}).get("order_id"))
        target = self.orders.get(lifecycle.get("protection", {}).get("target", {}).get("order_id"))
        manual = self.orders.get(lifecycle.get("manual_exit", {}).get("order_id"))
        position = self.positions.get(symbol, {"quantity": 0, "avg_price": None})
        open_orders = [dict(row) for row in self.orders.values() if row["symbol"] == symbol and row["status"] in {"SUBMITTED", "WORKING", "PARTIALLY_FILLED", "OPEN"}]
        terminal = [dict(row) for row in self.orders.values() if row["symbol"] == symbol and row["status"] in {"FILLED", "CANCELLED", "REJECTED", "EXPIRED"}]
        protection = {"stop": dict(stop) if stop else None, "target": dict(target) if target else None}
        if manual:
            protection["manual_exit"] = dict(manual)
        return {
            "entry_order": dict(entry) if entry else None,
            "position_qty": position["quantity"],
            "position_avg_price": position["avg_price"],
            "open_orders": open_orders,
            "recent_terminal_orders": terminal[-10:],
            "protection": protection,
        }

    def set_entry_state(self, symbol, *, filled_qty, requested_qty, status, avg_fill_price):
        order = self._find_role(symbol, "ENTRY")
        order["filled_qty"] = filled_qty
        order["requested_qty"] = requested_qty
        order["remaining_qty"] = max(0, requested_qty - filled_qty)
        order["status"] = status
        order["avg_fill_price"] = avg_fill_price
        self.positions[symbol] = {"quantity": filled_qty, "avg_price": avg_fill_price if filled_qty else None}

    def fill_exit(self, symbol, role, *, filled_qty, avg_fill_price, remaining_position_qty, partial=False):
        order = self._find_role(symbol, role)
        order["filled_qty"] = filled_qty
        order["remaining_qty"] = max(0, order["requested_qty"] - filled_qty)
        order["avg_fill_price"] = avg_fill_price
        order["status"] = "PARTIALLY_FILLED" if partial else "FILLED"
        self.positions[symbol] = {"quantity": remaining_position_qty, "avg_price": self.positions.get(symbol, {}).get("avg_price")}

    def set_position_absent(self, symbol):
        self.positions[symbol] = {"quantity": 0, "avg_price": None}

    def protection_orders(self, symbol):
        return [row for row in self.orders.values() if row["symbol"] == symbol and row["role"] in {"STOP", "TARGET"}]

    def live_protection_orders(self, symbol):
        return [row for row in self.protection_orders(symbol) if row["status"] in {"SUBMITTED", "WORKING", "PARTIALLY_FILLED", "OPEN"}]

    def _find_role(self, symbol, role):
        matches = [row for row in self.orders.values() if row["symbol"] == symbol and row["role"] == role]
        matches.sort(key=lambda row: row["order_id"])
        return matches[-1]

    def _find_by_client_order_id(self, client_order_id):
        for row in self.orders.values():
            if row["client_order_id"] == client_order_id:
                return row
        return None

    def _new_order(self, *, symbol, client_order_id, role, side, requested_qty, price, status, order_type="LIMIT"):
        order_id = f"B-{self.next_id}"
        self.next_id += 1
        return {
            "order_id": order_id,
            "client_order_id": client_order_id,
            "symbol": symbol,
            "role": role,
            "side": side,
            "order_type": order_type,
            "status": status,
            "requested_qty": requested_qty,
            "filled_qty": 0,
            "remaining_qty": requested_qty,
            "avg_fill_price": None,
            "price": price,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }


class OrderLifecycleMonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.ledger_path = os.path.join(self.tmpdir.name, "paper_ledger.json")
        self.broker = ScenarioBroker()
        self.engine = InternalPaperEngine(
            ledger_path=self.ledger_path,
            broker=self.broker,
            autonomy_enabled=True,
            monitor_interval_seconds=30,
        )

    def tearDown(self):
        self.tmpdir.cleanup()

    def _signal(self, **overrides):
        payload = {
            "signal_id": "sig-1",
            "symbol": "TSLA",
            "direction": "LONG",
            "strategy": "pluto-test-v1",
            "price": 1000.0,
            "take_profit": 1100.0,
            "stop_loss": 950.0,
            "analysis": {
                "entry_zone": 1000.0,
                "primary_target": 1100.0,
                "stop_loss": 950.0,
            },
        }
        payload.update(overrides)
        return payload

    def _lifecycle(self):
        return self.engine.lifecycle_records()[0]

    def _trade(self):
        return self.engine.trades()[0]

    def test_submitted_unfilled_order_remains_monitored(self):
        ok, _message, _payload = self.engine.execute_approved_signal(self._signal(), approved_by="tester")
        self.assertTrue(ok)
        lifecycle = self._lifecycle()
        self.assertEqual(lifecycle["state"], "ENTRY_SUBMITTED")
        self.engine.monitor_once()
        lifecycle = self._lifecycle()
        self.assertEqual(lifecycle["state"], "ENTRY_SUBMITTED")
        self.assertEqual(lifecycle["monitoring"]["attempt_count"], 2)

    def test_zero_fill_cancellation_becomes_terminal_without_position(self):
        self.engine.execute_approved_signal(self._signal(), approved_by="tester")
        self.broker.set_entry_state("TSLA", filled_qty=0, requested_qty=10, status="CANCELLED", avg_fill_price=None)
        self.engine.monitor_once()
        lifecycle = self._lifecycle()
        self.assertEqual(lifecycle["state"], "CANCELLED")
        self.assertEqual([row for row in self.engine.positions() if row["status"] == "OPEN"], [])

    def test_partial_fill_receives_protection_for_four_shares(self):
        self.engine.execute_approved_signal(self._signal(), approved_by="tester")
        self.broker.set_entry_state("TSLA", filled_qty=4, requested_qty=10, status="PARTIALLY_FILLED", avg_fill_price=1000.0)
        self.engine.monitor_once()
        lifecycle = self._lifecycle()
        self.assertEqual(lifecycle["state"], "ENTRY_PARTIALLY_FILLED")
        self.assertTrue(lifecycle["protection"]["confirmed"])
        live_orders = self.broker.live_protection_orders("TSLA")
        self.assertEqual(len(live_orders), 2)
        self.assertEqual({row["requested_qty"] for row in live_orders}, {4})

    def test_remaining_fill_increases_protection_to_ten(self):
        self.test_partial_fill_receives_protection_for_four_shares()
        self.broker.set_entry_state("TSLA", filled_qty=10, requested_qty=10, status="FILLED", avg_fill_price=1000.0)
        self.engine.monitor_once()
        live_orders = self.broker.live_protection_orders("TSLA")
        self.assertEqual(len(live_orders), 2)
        self.assertEqual({row["requested_qty"] for row in live_orders}, {10})
        lifecycle = self._lifecycle()
        self.assertEqual(lifecycle["state"], "ENTRY_FILLED")

    def test_protection_resizing_leaves_no_duplicate_live_exit_orders(self):
        self.test_remaining_fill_increases_protection_to_ten()
        all_protection = self.broker.protection_orders("TSLA")
        live_orders = self.broker.live_protection_orders("TSLA")
        cancelled = [row for row in all_protection if row["status"] == "CANCELLED"]
        self.assertEqual(len(live_orders), 2)
        self.assertGreaterEqual(len(cancelled), 2)

    def test_stop_fill_detected_target_cancelled_trade_closed(self):
        self.test_remaining_fill_increases_protection_to_ten()
        self.broker.fill_exit("TSLA", "STOP", filled_qty=10, avg_fill_price=940.0, remaining_position_qty=0)
        self.engine.monitor_once()
        lifecycle = self._lifecycle()
        self.assertEqual(lifecycle["state"], "CLOSED")
        self.assertEqual(lifecycle["exit"]["type"], "stop")
        target_order = self.broker._find_role("TSLA", "TARGET")
        self.assertIn(target_order["status"], {"CANCELLED", "EXPIRED", "REJECTED"})

    def test_target_fill_detected_stop_cancelled_trade_closed(self):
        self.test_remaining_fill_increases_protection_to_ten()
        self.broker.fill_exit("TSLA", "TARGET", filled_qty=10, avg_fill_price=1110.0, remaining_position_qty=0)
        self.engine.monitor_once()
        lifecycle = self._lifecycle()
        self.assertEqual(lifecycle["state"], "CLOSED")
        self.assertEqual(lifecycle["exit"]["type"], "target")
        stop_order = self.broker._find_role("TSLA", "STOP")
        self.assertIn(stop_order["status"], {"CANCELLED", "EXPIRED", "REJECTED"})

    def test_partial_exit_preserves_and_protects_remaining_position(self):
        self.test_remaining_fill_increases_protection_to_ten()
        self.broker.fill_exit("TSLA", "STOP", filled_qty=4, avg_fill_price=940.0, remaining_position_qty=6, partial=True)
        self.engine.monitor_once()
        live_orders = self.broker.live_protection_orders("TSLA")
        self.assertEqual(len(live_orders), 2)
        self.assertEqual({row["requested_qty"] for row in live_orders}, {6})
        lifecycle = self._lifecycle()
        self.assertNotEqual(lifecycle["state"], "CLOSED")

    def test_failed_sibling_cancellation_stays_tracked_and_retried(self):
        self.test_remaining_fill_increases_protection_to_ten()
        target = self.broker._find_role("TSLA", "TARGET")
        self.broker.cancel_uncertain_order_ids.add(target["order_id"])
        self.broker.fill_exit("TSLA", "STOP", filled_qty=10, avg_fill_price=940.0, remaining_position_qty=0)
        self.engine.monitor_once()
        lifecycle = self._lifecycle()
        self.assertEqual(lifecycle["state"], "MANUAL_LINK_IN_PROGRESS")
        self.assertTrue(self.engine.monitor_status()["freeze_new_entries"])
        self.engine.monitor_once()
        lifecycle = self._lifecycle()
        self.assertEqual(lifecycle["state"], "MANUAL_LINK_IN_PROGRESS")

    def test_position_absence_without_exit_evidence_does_not_close_trade(self):
        self.test_remaining_fill_increases_protection_to_ten()
        self.broker.set_position_absent("TSLA")
        self.engine.monitor_once()
        lifecycle = self._lifecycle()
        self.assertEqual(lifecycle["state"], "MANUAL_LINK_IN_PROGRESS")
        self.assertNotEqual(self._trade()["status"], "CLOSED")

    def test_realized_pl_and_exit_details_are_recorded(self):
        self.test_target_fill_detected_stop_cancelled_trade_closed()
        trade = self._trade()
        self.assertEqual(trade["status"], "CLOSED")
        self.assertEqual(trade["exit_type"], "target")
        self.assertEqual(trade["avg_exit_price"], 1110.0)
        self.assertGreater(trade["net_realized_pl"], 0)
        self.assertIn("close_order", trade["broker_evidence"])

    def test_closed_trade_logging_is_idempotent_across_restart(self):
        self.test_realized_pl_and_exit_details_are_recorded()
        before = self.engine.trades()
        restarted = InternalPaperEngine(
            ledger_path=self.ledger_path,
            broker=self.broker,
            autonomy_enabled=True,
            monitor_interval_seconds=30,
        )
        after = restarted.trades()
        self.assertEqual(len(before), len(after))
        self.assertEqual(before[0]["trade_id"], after[0]["trade_id"])

    def test_monitoring_continues_after_autonomy_off(self):
        ok, _message, lifecycle = self.engine.execute_approved_signal(self._signal(), approved_by="tester")
        self.assertTrue(ok)
        off_engine = InternalPaperEngine(
            ledger_path=self.ledger_path,
            broker=self.broker,
            autonomy_enabled=False,
            monitor_interval_seconds=30,
        )
        self.broker.set_entry_state("TSLA", filled_qty=10, requested_qty=10, status="FILLED", avg_fill_price=1000.0)
        off_engine.monitor_once()
        updated = off_engine.lifecycle_records()[0]
        self.assertTrue(updated["protection"]["confirmed"])
        ok, message, _payload = off_engine.execute_approved_signal(self._signal(signal_id="sig-2", symbol="NVDA"), approved_by="tester")
        self.assertFalse(ok)
        self.assertIn("Autonomy is OFF", message)

    def test_continuous_broker_failure_starts_stuck_timer_and_freezes_entries(self):
        self.engine.execute_approved_signal(self._signal(), approved_by="tester")
        self.broker.force_invalid_state = True
        self.engine.monitor_once()
        lifecycle = self._lifecycle()
        self.assertIsNotNone(lifecycle["monitoring"]["first_failed_at"])
        self.assertTrue(self.engine.monitor_status()["freeze_new_entries"])

    def test_dismissing_alert_does_not_remove_freeze(self):
        self.test_continuous_broker_failure_starts_stuck_timer_and_freezes_entries()
        alert = self.engine.alerts()[0]
        self.assertTrue(self.engine.dismiss_alert(alert["alert_id"]))
        self.assertTrue(self.engine.monitor_status()["freeze_new_entries"])

    def test_malformed_broker_response_fails_closed(self):
        self.engine.execute_approved_signal(self._signal(), approved_by="tester")
        self.broker.force_invalid_state = True
        self.engine.monitor_once()
        lifecycle = self._lifecycle()
        self.assertIn(lifecycle["state"], {"UNKNOWN_SUBMISSION_STATE", "PROTECTION_FAILED"})

    def test_monitor_heartbeat_becomes_unhealthy_after_missed_runs(self):
        data = json.load(open(self.ledger_path, encoding="utf-8"))
        data["monitor"]["last_completed_at"] = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
        with open(self.ledger_path, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        status = self.engine.monitor_status()
        self.assertTrue(status["unhealthy"])
        self.assertTrue(self.engine.monitor_status()["freeze_new_entries"])

    def test_fast_monitor_never_submits_new_entry(self):
        self.engine.monitor_once()
        self.assertEqual(self.broker.submit_entry_calls, 0)


if __name__ == "__main__":
    unittest.main()