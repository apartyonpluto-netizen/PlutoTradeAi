"""Long/short accounting, partial fills, fees, buying power and the default simulated broker."""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from backend.services.paper_engine import InternalPaperEngine, SimulatedPaperBroker

from support import PriceFeed, ScriptedBroker, patched_prices


def signal(direction="LONG", symbol="TSLA", price=100.0, stop=95.0, target=110.0, signal_id="sig-1"):
    return {
        "signal_id": signal_id, "symbol": symbol, "direction": direction, "strategy": "test-v1", "price": price,
        "stop_loss": stop, "take_profit": target,
        "analysis": {"entry_zone": price, "stop_loss": stop, "primary_target": target},
    }


class SimulatedBrokerAccountingTests(unittest.TestCase):
    """Uses the DEFAULT SimulatedPaperBroker (persistent, price-driven) end to end."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = os.path.join(self.tmp.name, "ledger.json")
        self.feed = PriceFeed(TSLA=100.0)
        self.addCleanup(self.tmp.cleanup)

    def engine(self, fee=0.0):
        broker = SimulatedPaperBroker(state_path=os.path.join(self.tmp.name, "ledger.broker.json"), price_provider=lambda s: self.feed(s)["data"].get("current_price"), fee_per_share=fee)
        return InternalPaperEngine(ledger_path=self.ledger, broker=broker, autonomy_enabled=True, monitor_interval_seconds=30)

    def open_position(self, engine, **kwargs):
        with patched_prices(self.feed):
            ok, message, _ = engine.execute_approved_signal(signal(**kwargs), approved_by="t")
        self.assertTrue(ok, message)

    def test_long_target_fill_completes_trade_and_cancels_stop(self):
        engine = self.engine()
        self.open_position(engine)
        account = engine.account()
        self.assertEqual(account["cash"], 90000.0)
        lifecycle = engine.lifecycle_records()[0]
        self.assertTrue(lifecycle["protection"]["confirmed"])
        self.feed.set("TSLA", 110.0)
        with patched_prices(self.feed):
            engine.monitor_once()
            account = engine.account()
        lifecycle = engine.lifecycle_records()[0]
        self.assertEqual(lifecycle["state"], "CLOSED")
        self.assertEqual(lifecycle["exit"]["type"], "target")
        self.assertEqual(account["cash"], 101000.0)
        self.assertEqual(account["realized_pl"], 1000.0)
        self.assertEqual(account["equity"], 101000.0)  # closed positions must not count toward equity
        self.assertEqual(account["open_positions"], 0)

    def test_long_stop_fill_books_loss_at_market_price(self):
        engine = self.engine()
        self.open_position(engine)
        self.feed.set("TSLA", 94.0)
        with patched_prices(self.feed):
            engine.monitor_once()
            account = engine.account()
        lifecycle = engine.lifecycle_records()[0]
        self.assertEqual(lifecycle["exit"]["type"], "stop")
        self.assertEqual(account["realized_pl"], -600.0)
        self.assertEqual(account["cash"], 99400.0)
        trade = engine.trades()[0]
        self.assertEqual(trade["net_realized_pl"], -600.0)
        self.assertEqual(trade["avg_exit_price"], 94.0)

    def test_short_position_equity_and_target_fill(self):
        engine = self.engine()
        self.open_position(engine, direction="SHORT", stop=105.0, target=90.0)
        self.assertEqual(engine.account()["cash"], 110000.0)  # short proceeds are credited
        self.feed.set("TSLA", 95.0)
        with patched_prices(self.feed):
            account = engine.account()
        self.assertEqual(account["equity"], 100500.0)  # signed short quantity: cash - 100 * 95
        self.assertEqual(account["unrealized_pl"], 500.0)
        self.assertEqual(account["buying_power"], 100500.0)  # cash 110000 minus the 9500 needed to buy the short back
        self.feed.set("TSLA", 90.0)
        with patched_prices(self.feed):
            engine.monitor_once()
            account = engine.account()
        self.assertEqual(engine.lifecycle_records()[0]["exit"]["type"], "target")
        self.assertEqual(account["cash"], 101000.0)
        self.assertEqual(account["realized_pl"], 1000.0)
        self.assertEqual(account["equity"], 101000.0)

    def test_short_stop_fill_books_loss(self):
        engine = self.engine()
        self.open_position(engine, direction="SHORT", stop=105.0, target=90.0)
        self.feed.set("TSLA", 106.0)
        with patched_prices(self.feed):
            engine.monitor_once()
            account = engine.account()
        self.assertEqual(engine.lifecycle_records()[0]["exit"]["type"], "stop")
        self.assertEqual(account["realized_pl"], -600.0)
        self.assertEqual(account["cash"], 99400.0)

    def test_manual_exit_fills_at_market_and_cancels_protection(self):
        engine = self.engine()
        self.open_position(engine)
        self.feed.set("TSLA", 103.0)
        with patched_prices(self.feed):
            ok, message, _ = engine.execute_approved_signal(signal(direction="EXIT", price=103.0, signal_id="exit-1"), approved_by="t")
            account = engine.account()
        self.assertTrue(ok, message)
        lifecycle = engine.lifecycle_records()[0]
        self.assertEqual(lifecycle["state"], "CLOSED")
        self.assertEqual(lifecycle["exit"]["type"], "manual")
        self.assertEqual(account["realized_pl"], 300.0)
        self.assertEqual(account["cash"], 100300.0)
        broker_orders = engine._broker._load()["orders"].values()  # noqa: SLF001
        self.assertEqual([o for o in broker_orders if o["status"] in {"SUBMITTED", "WORKING", "PARTIALLY_FILLED", "OPEN"}], [])

    def test_fees_reduce_cash_and_net_pl(self):
        engine = self.engine(fee=0.01)
        self.open_position(engine)
        self.assertEqual(engine.account()["cash"], round(90000.0 - 1.0, 2))
        self.feed.set("TSLA", 110.0)
        with patched_prices(self.feed):
            engine.monitor_once()
            account = engine.account()
        trade = engine.trades()[0]
        self.assertEqual(trade["realized_pl"], 1000.0)
        self.assertEqual(trade["fees"], 2.0)
        self.assertEqual(trade["net_realized_pl"], 998.0)
        self.assertEqual(account["cash"], round(101000.0 - 2.0, 2))
        self.assertEqual(account["realized_pl"], 998.0)

    def test_cannot_buy_more_than_buying_power(self):
        with patch.dict(os.environ, {"PLUTO_MAX_POSITION_PERCENT": "1.0", "PLUTO_MAX_RISK_PER_TRADE": "0.2"}):
            engine = self.engine()
        self.open_position(engine, price=100.0, stop=99.0, target=130.0)  # up to 1000 shares = everything
        self.assertLessEqual(engine.account()["cash"], 100000.0)
        self.assertGreaterEqual(engine.account()["cash"], 0.0)
        self.feed.set("NVDA", 100.0)
        with patched_prices(self.feed):
            ok, message, _ = engine.execute_approved_signal(signal(symbol="NVDA", signal_id="sig-2", stop=99.0, target=130.0), approved_by="t")
        self.assertFalse(ok)
        self.assertIn("buying power", message)

    def test_nan_and_infinite_levels_are_rejected(self):
        engine = self.engine()
        for bad in (float("nan"), float("inf"), -5.0, 0.0):
            ok, message, _ = engine.execute_approved_signal(signal(stop=bad, signal_id=f"bad-{bad}"), approved_by="t")
            self.assertFalse(ok, message)
        ok, _message, _ = engine.execute_approved_signal({**signal(signal_id="bad-price"), "price": float("nan"), "analysis": {}}, approved_by="t")
        self.assertFalse(ok)

    def test_entry_without_a_stop_is_rejected_but_target_is_optional(self):
        engine = self.engine()
        ok, message, _ = engine.execute_approved_signal({"signal_id": "no-stop", "symbol": "TSLA", "direction": "LONG", "strategy": "x", "price": 100.0}, approved_by="t")
        self.assertFalse(ok)
        self.assertIn("stop", message)
        self.open_position(engine, target=None, signal_id="no-target")
        lifecycle = engine.lifecycle_records()[0]
        self.assertTrue(lifecycle["protection"]["confirmed"])  # stop alone is a complete protection when no target was set
        self.assertIsNone(engine._broker._load()["orders"].get("SIM-3"))  # noqa: SLF001 - entry + stop only, no target order

    def test_stop_and_target_direction_validation(self):
        engine = self.engine()
        ok, message, _ = engine.execute_approved_signal(signal(direction="SHORT", stop=95.0, target=90.0, signal_id="s1"), approved_by="t")
        self.assertFalse(ok)
        self.assertIn("Stop loss", message)
        ok, message, _ = engine.execute_approved_signal(signal(direction="LONG", stop=95.0, target=99.0, signal_id="s2"), approved_by="t")
        self.assertFalse(ok)
        self.assertIn("Target", message)


class PartialFillAccountingTests(unittest.TestCase):
    """The scripted broker requests 100 shares (10% of 100k at $100) and fills them as the test dictates."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.broker = ScriptedBroker()
        self.engine = InternalPaperEngine(ledger_path=os.path.join(self.tmp.name, "ledger.json"), broker=self.broker, autonomy_enabled=True, monitor_interval_seconds=30)
        self.feed = PriceFeed(TSLA=100.0)

    def open(self, direction="LONG", **kwargs):
        with patched_prices(self.feed):
            ok, message, _ = self.engine.execute_approved_signal(signal(direction=direction, **kwargs), approved_by="t")
        self.assertTrue(ok, message)

    def monitor(self):
        with patched_prices(self.feed):
            self.engine.monitor_once()

    def protection(self, symbol="TSLA"):
        return [o for o in self.broker.live(symbol) if o["role"] in {"STOP", "TARGET"}]

    def test_partial_entry_fills_at_different_prices_use_actual_fill_notional(self):
        self.open()
        self.broker.fill_entry("TSLA", filled_qty=40, avg_price=100.0)
        self.monitor()
        self.assertEqual(self.engine.account()["cash"], 100000.0 - 4000.0)
        self.assertEqual({o["requested_qty"] for o in self.protection()}, {40})
        self.broker.fill_entry("TSLA", filled_qty=100, avg_price=101.2)  # cumulative average of 40@100 and 60@102
        self.monitor()
        with patched_prices(self.feed):
            account = self.engine.account()
        self.assertEqual(account["cash"], round(100000.0 - 10120.0, 2))
        position = [row for row in self.engine.positions() if row["status"] == "OPEN"][0]
        self.assertEqual(position["entry_price"], 101.2)
        self.assertEqual(position["quantity"], 100)
        self.assertEqual({o["requested_qty"] for o in self.protection()}, {100})

    def test_partial_exits_at_different_prices_book_each_fill(self):
        self.open()
        self.broker.fill_entry("TSLA", filled_qty=100, avg_price=100.0)
        self.monitor()
        self.broker.fill_exit("TSLA", "STOP", filled_qty=40, avg_price=94.0, position_after=60)
        self.monitor()
        account = self.engine.account()
        self.assertEqual(account["realized_pl"], -240.0)  # booked at the partial, not deferred to the final close
        self.assertEqual(account["cash"], round(100000.0 - 10000.0 + 3760.0, 2))
        self.assertNotEqual(self.engine.lifecycle_records()[0]["state"], "CLOSED")
        self.assertEqual({o["requested_qty"] for o in self.protection()}, {60})  # protection resized to what is left
        self.broker.fill_exit("TSLA", "TARGET", filled_qty=60, avg_price=110.0, position_after=0)
        self.monitor()
        self.assertEqual(self.engine.lifecycle_records()[0]["state"], "CLOSED")
        trade = self.engine.trades()[0]
        self.assertEqual(trade["realized_pl"], 360.0)
        self.assertEqual(trade["avg_exit_price"], 103.6)  # (40*94 + 60*110) / 100
        account = self.engine.account()
        self.assertEqual(account["cash"], round(100000.0 - 10000.0 + 3760.0 + 6600.0, 2))
        self.assertEqual(account["realized_pl"], 360.0)
        self.assertEqual(self.protection(), [])

    def test_repeated_polls_never_double_book_a_fill(self):
        self.open()
        self.broker.fill_entry("TSLA", filled_qty=100, avg_price=100.0)
        self.monitor()
        self.broker.fill_exit("TSLA", "STOP", filled_qty=40, avg_price=94.0, position_after=60)
        for _ in range(4):
            self.monitor()
        self.assertEqual(self.engine.account()["realized_pl"], -240.0)
        self.assertEqual(self.engine.account()["cash"], round(100000.0 - 10000.0 + 3760.0, 2))

    def test_short_full_cover_with_fees(self):
        self.open(direction="SHORT", stop=105.0, target=90.0)
        self.broker.fill_entry("TSLA", filled_qty=100, avg_price=100.0)
        self.monitor()
        self.assertEqual(self.engine.account()["cash"], 110000.0)
        self.broker.fill_exit("TSLA", "TARGET", filled_qty=100, avg_price=92.0, position_after=0, fees=0.5)
        self.monitor()
        account = self.engine.account()
        self.assertEqual(account["realized_pl"], round(800.0 - 0.5, 2))
        self.assertEqual(account["cash"], round(110000.0 - 9200.0 - 0.5, 2))
        self.assertEqual(self.engine.trades()[0]["net_realized_pl"], 799.5)

    def test_short_equity_uses_signed_quantity(self):
        self.open(direction="SHORT", stop=105.0, target=90.0)
        self.broker.fill_entry("TSLA", filled_qty=100, avg_price=100.0)
        self.monitor()
        self.feed.set("TSLA", 104.0)
        with patched_prices(self.feed):
            account = self.engine.account()
        self.assertEqual(account["equity"], round(110000.0 - 10400.0, 2))
        self.assertEqual(account["unrealized_pl"], -400.0)

    def test_closed_position_is_excluded_from_equity(self):
        self.open()
        self.broker.fill_entry("TSLA", filled_qty=100, avg_price=100.0)
        self.monitor()
        self.broker.fill_exit("TSLA", "TARGET", filled_qty=100, avg_price=110.0, position_after=0)
        self.feed.set("TSLA", 500.0)  # a stale mark on a closed row must not leak into equity
        self.monitor()
        with patched_prices(self.feed):
            account = self.engine.account()
        self.assertEqual(account["equity"], 101000.0)
        self.assertEqual(account["open_positions"], 0)

    def test_interrupted_entry_submission_recovers_without_duplicate_order(self):
        real_submit = self.broker.submit_entry_order
        calls = {"n": 0}

        def flaky(payload):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("process died after persisting the intent")
            return real_submit(payload)

        self.broker.submit_entry_order = flaky
        with patched_prices(self.feed), self.assertRaises(RuntimeError):
            self.engine.execute_approved_signal(signal(), approved_by="t")
        lifecycle = self.engine.lifecycle_records()[0]
        self.assertIsNone(lifecycle["entry"]["order_id"])  # intent persisted before the broker call
        self.monitor()  # what a restart does
        lifecycle = self.engine.lifecycle_records()[0]
        self.assertIsNotNone(lifecycle["entry"]["order_id"])
        self.assertEqual(len([o for o in self.broker.orders.values() if o["role"] == "ENTRY"]), 1)

    def test_one_broken_trade_does_not_stop_monitoring_others(self):
        self.open()
        self.open(symbol="NVDA", signal_id="sig-2")
        original = self.broker.fetch_state

        def explode_for_tsla(lifecycle):
            if lifecycle["symbol"] == "TSLA":
                raise KeyError("boom")
            return original(lifecycle)

        self.broker.fetch_state = explode_for_tsla
        self.broker.fill_entry("NVDA", filled_qty=100, avg_price=100.0)
        self.monitor()
        by_symbol = {row["symbol"]: row for row in self.engine.lifecycle_records()}
        self.assertEqual(by_symbol["NVDA"]["state"], "ENTRY_FILLED")
        self.assertEqual(by_symbol["TSLA"]["monitoring"]["last_error_category"], "INTERNAL_ERROR")
        self.assertTrue(self.engine.monitor_status()["freeze_new_entries"])


if __name__ == "__main__":
    unittest.main()
