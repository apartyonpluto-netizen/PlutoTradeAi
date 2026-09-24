"""Persistence safety: corrupted records are never replaced by an empty account, writes are atomic,
and concurrent processes cannot lose each other's updates."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from backend.services.paper_engine import InternalPaperEngine, SimulatedPaperBroker
from backend.services.storage import CrossProcessRLock, StorageCorruptionError, atomic_write_json, read_json_strict
from backend.services.tradingview_signals import TradingViewSignalService

from support import login, make_app, post_webhook, webhook_payload

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class CorruptionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ledger = Path(self.tmp.name) / "ledger.json"

    def engine(self):
        return InternalPaperEngine(ledger_path=str(self.ledger), autonomy_enabled=True, monitor_interval_seconds=30)

    def test_corrupted_ledger_is_preserved_not_replaced_by_empty_account(self):
        good = self.engine()
        cash_before = good.account()["cash"]
        self.assertEqual(cash_before, 100000.0)
        garbage = '{"account": {"cash": 4321.0, "starting_b'  # torn write
        self.ledger.write_text(garbage)
        broken = self.engine()
        self.assertEqual(self.ledger.read_text(), garbage, "corrupted ledger must be left untouched")
        self.assertFalse(broken.storage_health()["ok"])
        with self.assertRaises(StorageCorruptionError):
            broken.account()
        with self.assertRaises(StorageCorruptionError):
            broken.execute_approved_signal({"symbol": "TSLA", "direction": "LONG", "price": 100.0, "signal_id": "x"}, approved_by="t")
        self.assertEqual(self.ledger.read_text(), garbage)
        self.assertTrue(list(self.ledger.parent.glob("ledger.json.corrupt-*")), "a forensic copy must be kept")

    def test_empty_and_nan_ledger_files_count_as_corrupted(self):
        self.engine()
        for content in ("", "   \n", '{"account": {"cash": NaN}}', "[1, 2]"):
            self.ledger.write_text(content)
            with self.assertRaises(StorageCorruptionError, msg=repr(content)):
                self.engine().account()

    def test_ledger_deleted_while_running_is_refused_not_recreated(self):
        engine = self.engine()
        engine.account()
        self.ledger.unlink()
        with self.assertRaises(StorageCorruptionError):
            engine.account()
        self.assertFalse(self.ledger.exists())

    def test_corrupted_signals_file_is_not_overwritten(self):
        path = Path(self.tmp.name) / "signals.json"
        path.write_text('{"signals": [{"signal_id": "keep-me"')
        service = TradingViewSignalService(analyzer=lambda s: {}, data_path=str(path), approvals_path=str(Path(self.tmp.name) / "a.json"), webhook_secret="s")
        with self.assertRaises(StorageCorruptionError):
            service.list_signals()
        with self.assertRaises(StorageCorruptionError):
            service.normalize_and_store({**webhook_payload(), "secret": "s"})
        self.assertIn("keep-me", path.read_text())

    def test_corrupted_broker_state_is_refused(self):
        path = Path(self.tmp.name) / "broker.json"
        SimulatedPaperBroker(state_path=path)
        path.write_text("not json")
        with self.assertRaises(StorageCorruptionError):
            SimulatedPaperBroker(state_path=path).list_positions()
        self.assertEqual(path.read_text(), "not json")

    def test_api_reports_503_and_health_degraded_for_corrupted_ledger(self):
        app, patcher = make_app(self.tmp.name)
        self.addCleanup(patcher.stop)
        client = app.test_client()
        self.ledger = Path(self.tmp.name) / "paper_ledger.json"
        self.ledger.write_text("{corrupt")
        response = client.get("/api/paper/account")
        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.get_json()["success"])
        self.assertEqual(self.ledger.read_text(), "{corrupt")
        health = client.get("/api/health").get_json()["data"]
        self.assertEqual(health["status"], "degraded")

    def test_api_webhook_returns_503_when_signal_store_is_corrupted(self):
        app, patcher = make_app(self.tmp.name)
        self.addCleanup(patcher.stop)
        (Path(self.tmp.name) / "signals.json").write_text("garbage")
        response = post_webhook(app.test_client(), webhook_payload())
        self.assertEqual(response.status_code, 503)
        self.assertEqual((Path(self.tmp.name) / "signals.json").read_text(), "garbage")


class AtomicWriteTests(unittest.TestCase):
    def test_write_is_atomic_and_leaves_no_temp_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            atomic_write_json(path, {"a": 1})
            atomic_write_json(path, {"a": 2})
            self.assertEqual(json.loads(path.read_text()), {"a": 2})
            self.assertEqual([p.name for p in Path(tmp).iterdir()], ["state.json"])

    def test_non_finite_numbers_cannot_be_persisted_and_old_state_survives(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            atomic_write_json(path, {"cash": 100.0})
            for bad in (float("nan"), float("inf")):
                with self.assertRaises(ValueError):
                    atomic_write_json(path, {"cash": bad})
            self.assertEqual(json.loads(path.read_text()), {"cash": 100.0})
            self.assertEqual([p.name for p in Path(tmp).iterdir()], ["state.json"])

    def test_read_strict_returns_default_only_for_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(read_json_strict(Path(tmp) / "missing.json", lambda: {"x": 1}), {"x": 1})


COUNTER_SCRIPT = """
import json, sys
from pathlib import Path
sys.path.insert(0, {root!r})
from backend.services.storage import CrossProcessRLock, atomic_write_json
path = Path({path!r}); lock = CrossProcessRLock(path.with_name('counter.lock'))
for _ in range({loops}):
    with lock:
        value = json.loads(path.read_text())['n']
        atomic_write_json(path, {{'n': value + 1}})
"""

SIGNAL_SCRIPT = """
import sys
sys.path.insert(0, {root!r})
from datetime import datetime, timezone
from backend.services.tradingview_signals import TradingViewSignalService
svc = TradingViewSignalService(analyzer=lambda s: {{}}, data_path={signals!r}, approvals_path={approvals!r}, webhook_secret='s')
ok, code, body = svc.normalize_and_store({{'secret': 's', 'signal_id': 'proc-{index}', 'symbol': 'TSLA', 'strategy': 'proc-test', 'direction': 'LONG',
    'timeframe': '60', 'price': 100 + {index}, 'bar_time': datetime.now(timezone.utc).isoformat()}})
assert ok, body
"""


ENGINE_SCRIPT = """
import sys
sys.path.insert(0, {root!r})
from unittest.mock import patch
def snap(symbol, *a, **k):
    return {{"success": True, "data": {{"current_price": 100.0, "market_data_status": "DELAYED"}}, "data_status": "delayed"}}
with patch("backend.services.paper_engine.get_stock_snapshot", side_effect=snap):
    from backend.services.paper_engine import InternalPaperEngine
    engine = InternalPaperEngine(ledger_path={ledger!r}, autonomy_enabled=True, monitor_interval_seconds=30)
    ok, message, _ = engine.execute_approved_signal({{"signal_id": "p-{symbol}", "symbol": "{symbol}", "direction": "LONG", "strategy": "proc-test",
        "price": 100.0, "stop_loss": 95.0, "take_profit": 110.0}}, approved_by="proc")
    assert ok, message
"""


class MultiProcessTests(unittest.TestCase):
    def _run_parallel(self, scripts):
        procs = [subprocess.Popen([sys.executable, "-c", script], cwd=PROJECT_ROOT, stderr=subprocess.PIPE) for script in scripts]
        for proc in procs:
            _out, err = proc.communicate(timeout=120)
            self.assertEqual(proc.returncode, 0, err.decode())

    def test_concurrent_processes_do_not_lose_counter_updates(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "counter.json"
            path.write_text('{"n": 0}')
            script = COUNTER_SCRIPT.format(root=str(PROJECT_ROOT), path=str(path), loops=25)
            self._run_parallel([script] * 4)
            self.assertEqual(json.loads(path.read_text())["n"], 100)

    def test_concurrent_webhook_processes_do_not_lose_signals(self):
        with tempfile.TemporaryDirectory() as tmp:
            signals, approvals = str(Path(tmp) / "signals.json"), str(Path(tmp) / "approvals.json")
            scripts = [SIGNAL_SCRIPT.format(root=str(PROJECT_ROOT), signals=signals, approvals=approvals, index=i) for i in range(8)]
            self._run_parallel(scripts)
            stored = json.loads(Path(signals).read_text())["signals"]
            self.assertEqual(sorted(row["signal_id"] for row in stored), sorted(f"proc-{i}" for i in range(8)))

    def test_concurrent_processes_sharing_one_ledger_and_broker_lose_no_trades(self):
        symbols = ["AAA", "BBB", "CCC", "DDD"]
        with tempfile.TemporaryDirectory() as tmp:
            ledger = str(Path(tmp) / "ledger.json")
            InternalPaperEngine(ledger_path=ledger)  # create the empty ledger once
            self._run_parallel([ENGINE_SCRIPT.format(root=str(PROJECT_ROOT), ledger=ledger, symbol=sym) for sym in symbols])
            data = json.loads(Path(ledger).read_text())
            self.assertEqual(sorted(row["symbol"] for row in data["lifecycles"]), symbols)
            self.assertEqual(sorted(row["symbol"] for row in data["positions"] if row["status"] == "OPEN"), symbols)
            self.assertEqual(data["account"]["cash"], 100000.0 - 4 * 10000.0)  # no entry lost, none booked twice
            broker = json.loads((Path(tmp) / "ledger.broker.json").read_text())
            self.assertEqual(len([o for o in broker["orders"].values() if o["role"] == "ENTRY"]), 4)
            self.assertEqual(len([o for o in broker["orders"].values() if o["status"] == "SUBMITTED"]), 8)  # one live stop + target each

    def test_lock_is_reentrant_within_a_thread(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = CrossProcessRLock(Path(tmp) / "x.lock")
            with lock:
                with lock:
                    pass


if __name__ == "__main__":
    unittest.main()
