"""Approval/decline atomicity, idempotency, analysis races, restart recovery and input validation."""
from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.services.tradingview_signals import TradingViewSignalService

from support import PriceFeed, login, make_app, patched_prices, post_webhook, webhook_payload

SECRET = "s3cret"


def make_service(tmp, analyzer=None, **kwargs):
    return TradingViewSignalService(
        analyzer=analyzer or (lambda s: {"warnings": [], "data_freshness": "DELAYED", "entry_zone": s["price"]}),
        data_path=str(Path(tmp) / "signals.json"),
        approvals_path=str(Path(tmp) / "approvals.json"),
        webhook_secret=SECRET,
        **kwargs,
    )


def store(service, **overrides):
    ok, code, body = service.normalize_and_store({**webhook_payload(**overrides), "secret": SECRET})
    assert ok, body
    return body["data"]["signal_id"]


class StateMachineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.service = make_service(self.tmp.name)

    def ready(self, **overrides):
        signal_id = store(self.service, **overrides)
        self.service.process_signal(signal_id)
        self.assertEqual(self.service.get_signal(signal_id)["status"], "AWAITING_APPROVAL")
        return signal_id

    def test_approval_requires_finished_analysis(self):
        signal_id = store(self.service)
        outcome, _row, _msg = self.service.begin_approval(signal_id, "alice")
        self.assertEqual(outcome, "NOT_READY")
        self.assertEqual(self.service.get_signal(signal_id)["status"], "VALIDATED")

    def test_executed_signal_cannot_be_declined(self):
        signal_id = self.ready()
        self.assertEqual(self.service.begin_approval(signal_id, "alice")[0], "OK")
        self.service.finish_execution(signal_id, executed=True, message="ok", position={"trade_id": "T"})
        outcome, row, _msg = self.service.decline(signal_id, "bob", "too late")
        self.assertEqual(outcome, "CONFLICT")
        self.assertEqual(self.service.get_signal(signal_id)["status"], "PAPER_EXECUTED")
        self.assertEqual(row["status"], "PAPER_EXECUTED")

    def test_claimed_approval_cannot_be_declined_and_declined_cannot_be_approved(self):
        first = self.ready()
        self.service.begin_approval(first, "alice")
        self.assertEqual(self.service.decline(first, "bob", "race")[0], "CONFLICT")
        second = self.ready()
        self.assertEqual(self.service.decline(second, "bob", "no")[0], "OK")
        self.assertEqual(self.service.begin_approval(second, "alice")[0], "CONFLICT")
        self.assertEqual(self.service.get_signal(second)["status"], "DECLINED")

    def test_repeated_approve_and_decline_are_idempotent(self):
        signal_id = self.ready()
        self.assertEqual(self.service.begin_approval(signal_id, "a")[0], "OK")
        self.assertEqual(self.service.begin_approval(signal_id, "a")[0], "RESUME")
        self.service.finish_execution(signal_id, executed=True, message="ok", position={})
        self.assertEqual(self.service.begin_approval(signal_id, "a")[0], "ALREADY_EXECUTED")
        other = self.ready()
        self.assertEqual(self.service.decline(other, "a", "x")[0], "OK")
        self.assertEqual(self.service.decline(other, "a", "x")[0], "ALREADY_DECLINED")
        events = json.loads((Path(self.tmp.name) / "approvals.json").read_text())["events"]
        self.assertEqual([e["decision"] for e in events if e["signal_id"] == other], ["DECLINED"], "a repeat must not duplicate the audit event")

    def test_concurrent_approve_and_decline_never_both_win(self):
        for _ in range(25):
            signal_id = self.ready()
            barrier = threading.Barrier(2)
            results = {}

            def approve():
                barrier.wait()
                results["approve"] = self.service.begin_approval(signal_id, "alice")[0]

            def decline():
                barrier.wait()
                results["decline"] = self.service.decline(signal_id, "bob", "race")[0]

            threads = [threading.Thread(target=approve), threading.Thread(target=decline)]
            [t.start() for t in threads]
            [t.join() for t in threads]
            self.assertIn((results["approve"], results["decline"]), {("OK", "CONFLICT"), ("CONFLICT", "OK")}, results)
            self.assertEqual(self.service.get_signal(signal_id)["status"], "APPROVED" if results["approve"] == "OK" else "DECLINED")

    def test_many_concurrent_approvals_claim_exactly_once(self):
        signal_id = self.ready()
        barrier = threading.Barrier(8)
        outcomes = []

        def approve():
            barrier.wait()
            outcomes.append(self.service.begin_approval(signal_id, "alice")[0])

        threads = [threading.Thread(target=approve) for _ in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(outcomes.count("OK"), 1)
        events = json.loads((Path(self.tmp.name) / "approvals.json").read_text())["events"]
        self.assertEqual(len([e for e in events if e["decision"] == "APPROVED"]), 1)

    def test_analysis_result_never_overwrites_a_decline(self):
        started, release = threading.Event(), threading.Event()

        def slow_analyzer(signal):
            started.set()
            release.wait(5)
            return {"warnings": [], "data_freshness": "LIVE", "entry_zone": signal["price"]}

        service = make_service(self.tmp.name, analyzer=slow_analyzer)
        signal_id = store(service, signal_id="slow-1")
        worker = threading.Thread(target=service.process_signal, args=(signal_id,))
        worker.start()
        self.assertTrue(started.wait(5))
        self.assertEqual(service.get_signal(signal_id)["status"], "ANALYZING")
        self.assertEqual(service.decline(signal_id, "bob", "not interested")[0], "OK")
        release.set()
        worker.join(5)
        row = service.get_signal(signal_id)
        self.assertEqual(row["status"], "DECLINED")
        self.assertIsNone(row["analysis"])

    def test_analysis_failure_and_bad_output_are_recorded_not_raised(self):
        def boom(_signal):
            raise RuntimeError("provider down")

        failing = make_service(self.tmp.name, analyzer=boom)
        signal_id = store(failing, signal_id="boom-1")
        failing.process_signal(signal_id)
        self.assertEqual(failing.get_signal(signal_id)["status"], "FAILED")
        bad = make_service(self.tmp.name, analyzer=lambda s: ["not", "a", "dict"])
        signal_id = store(bad, signal_id="bad-1")
        bad.process_signal(signal_id)
        self.assertEqual(bad.get_signal(signal_id)["status"], "FAILED")

    def test_non_finite_analysis_output_is_persisted_safely(self):
        service = make_service(self.tmp.name, analyzer=lambda s: {"confidence_score": float("nan"), "entry_zone": float("inf"), "warnings": [], "data_freshness": "LIVE"})
        signal_id = store(service, signal_id="nan-1")
        service.process_signal(signal_id)
        row = service.get_signal(signal_id)
        self.assertEqual(row["status"], "AWAITING_APPROVAL")
        self.assertIsNone(row["analysis"]["confidence_score"])

    def test_stale_analysis_requires_explicit_acknowledgement(self):
        service = make_service(self.tmp.name, analyzer=lambda s: {"warnings": ["BACKEND_DATA_NOT_FRESH"], "data_freshness": "STALE", "entry_zone": s["price"]})
        signal_id = store(service, signal_id="stale-1")
        service.process_signal(signal_id)
        outcome, _row, message = service.begin_approval(signal_id, "alice")
        self.assertEqual(outcome, "STALE_DATA")
        self.assertIn("acknowledge_stale", message)
        self.assertEqual(service.get_signal(signal_id)["status"], "AWAITING_APPROVAL")
        self.assertEqual(service.begin_approval(signal_id, "alice", acknowledge_stale=True)[0], "OK")
        self.assertTrue(service.get_signal(signal_id)["approval"]["acknowledged_stale_data"])

    def test_signals_expire_if_not_approved_in_time(self):
        service = make_service(self.tmp.name, approval_max_age_seconds=60)
        signal_id = store(service, signal_id="old-1")
        service.process_signal(signal_id)
        service.update_signal(signal_id, lambda row: {**row, "received_at": (datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat()})
        outcome, _row, _msg = service.begin_approval(signal_id, "alice")
        self.assertEqual(outcome, "EXPIRED")
        self.assertEqual(service.get_signal(signal_id)["status"], "EXPIRED")
        self.assertEqual(service.begin_approval(signal_id, "alice")[0], "CONFLICT")

    def test_retryable_failure_returns_signal_to_the_queue_and_hard_failure_is_final(self):
        soft = self.ready()
        self.service.begin_approval(soft, "alice")
        self.service.finish_execution(soft, executed=False, message="frozen", retryable=True)
        self.assertEqual(self.service.get_signal(soft)["status"], "AWAITING_APPROVAL")
        self.assertEqual(self.service.begin_approval(soft, "alice")[0], "OK")
        hard = self.ready()
        self.service.begin_approval(hard, "alice")
        self.service.finish_execution(hard, executed=False, message="risk", retryable=False)
        self.assertEqual(self.service.get_signal(hard)["status"], "FAILED")
        self.assertEqual(self.service.begin_approval(hard, "alice")[0], "CONFLICT")

    def test_finish_execution_cannot_resurrect_a_declined_signal(self):
        signal_id = self.ready()
        self.service.decline(signal_id, "bob", "no")
        self.service.finish_execution(signal_id, executed=True, message="ok", position={})
        self.assertEqual(self.service.get_signal(signal_id)["status"], "DECLINED")


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_signal_accepted_but_never_analyzed_is_analyzed_after_restart(self):
        first = make_service(self.tmp.name)
        signal_id = store(first, signal_id="crash-1")  # process dies here: queued only in memory
        restarted = make_service(self.tmp.name)
        self.assertEqual(restarted.recover_pending(process_inline=True), [signal_id])
        self.assertEqual(restarted.get_signal(signal_id)["status"], "AWAITING_APPROVAL")

    def test_interrupted_analysis_is_released_and_retried(self):
        service = make_service(self.tmp.name, analysis_timeout_seconds=30)
        signal_id = store(service, signal_id="mid-1")
        service.update_signal(signal_id, lambda row: {**row, "status": "ANALYZING", "analysis_attempts": 1,
                                                       "analysis_started_at": (datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat()})
        service.recover_pending(process_inline=True)
        self.assertEqual(service.get_signal(signal_id)["status"], "AWAITING_APPROVAL")

    def test_live_analysis_is_not_stolen_and_repeated_crashes_give_up(self):
        service = make_service(self.tmp.name, analysis_timeout_seconds=300)
        live = store(service, signal_id="live-1")
        service.update_signal(live, lambda row: {**row, "status": "ANALYZING", "analysis_started_at": datetime.now(timezone.utc).isoformat()})
        self.assertEqual(service.recover_pending(), [])
        self.assertEqual(service.get_signal(live)["status"], "ANALYZING")
        loop = store(service, signal_id="loop-1")
        service.update_signal(loop, lambda row: {**row, "status": "ANALYZING", "analysis_attempts": 3, "analysis_started_at": "2001-01-01T00:00:00+00:00"})
        service.recover_pending()
        self.assertEqual(service.get_signal(loop)["status"], "FAILED")

    def test_worker_thread_processes_queue_and_survives_analyzer_errors(self):
        calls = []

        def analyzer(signal):
            calls.append(signal["signal_id"])
            if signal["signal_id"] == "bad-one":
                raise RuntimeError("boom")
            return {"warnings": [], "data_freshness": "LIVE", "entry_zone": signal["price"]}

        service = make_service(self.tmp.name, analyzer=analyzer)
        service.start_worker()
        self.addCleanup(service.stop_worker)
        bad, good = store(service, signal_id="bad-one"), store(service, signal_id="good-one")
        deadline = datetime.now() + timedelta(seconds=10)
        while datetime.now() < deadline and service.get_signal(good)["status"] != "AWAITING_APPROVAL":
            threading.Event().wait(0.05)
        self.assertEqual(service.get_signal(bad)["status"], "FAILED")
        self.assertEqual(service.get_signal(good)["status"], "AWAITING_APPROVAL")


class WebhookValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.service = make_service(self.tmp.name)

    def submit(self, **overrides):
        return self.service.normalize_and_store({**webhook_payload(), "secret": SECRET, **overrides})

    def test_bad_numbers_are_rejected_with_400(self):
        cases = [
            {"price": float("nan")}, {"price": float("inf")}, {"price": -1}, {"price": 0}, {"price": "abc"}, {"price": True}, {"price": [1]},
            {"stop_loss": float("nan")}, {"take_profit": float("-inf")}, {"stop_loss": 101.0}, {"take_profit": 99.0},
            {"direction": "SHORT", "stop_loss": 95.0, "take_profit": 90.0},
            {"confidence_hint": 101}, {"confidence_hint": "high"}, {"confidence_hint": float("nan")},
        ]
        for overrides in cases:
            ok, code, body = self.submit(signal_id=f"n-{abs(hash(str(overrides)))}", **overrides)
            self.assertFalse(ok, overrides)
            self.assertEqual(code, 400, (overrides, body))

    def test_malformed_field_types_are_rejected_not_crashed(self):
        for overrides in ({"symbol": {"a": 1}}, {"symbol": 123}, {"symbol": ["TSLA"]}, {"strategy": {"x": 1}}, {"strategy": 5}, {"timeframe": [60]},
                          {"timeframe": {"a": 1}}, {"direction": ["LONG"]}, {"signal_id": {"a": 1}}, {"signal_id": "has space"}, {"signal_id": "x" * 500},
                          {"bar_time": ["2026"]}, {"bar_time": "not a date"}, {"bar_time": 1e30}):
            ok, code, _body = self.submit(**overrides)
            self.assertFalse(ok, overrides)
            self.assertEqual(code, 400, overrides)

    def test_secret_handling(self):
        ok, code, _ = self.service.normalize_and_store({**webhook_payload(), "secret": "wrong-é"})
        self.assertEqual((ok, code), (False, 401))  # non-ASCII must be a clean 401, not a TypeError
        ok, code, _ = self.service.normalize_and_store(webhook_payload())
        self.assertEqual((ok, code), (False, 401))
        unconfigured = TradingViewSignalService(analyzer=lambda s: {}, data_path=str(Path(self.tmp.name) / "u.json"), approvals_path=str(Path(self.tmp.name) / "ua.json"), webhook_secret="")
        ok, code, _ = unconfigured.normalize_and_store({**webhook_payload(), "secret": "anything"})
        self.assertEqual((ok, code), (False, 503))

    def test_stored_secret_is_redacted_and_levels_are_numbers(self):
        signal_id = store(self.service, price="100.5", stop_loss="99", take_profit="105")
        row = self.service.get_signal(signal_id)
        self.assertEqual(row["raw_payload"]["secret"], "REDACTED")
        self.assertEqual((row["price"], row["stop_loss"], row["take_profit"]), (100.5, 99.0, 105.0))

    def test_stale_timestamps_replays_and_duplicates(self):
        ok, code, _ = self.submit(bar_time=(datetime.now(timezone.utc) - timedelta(hours=2)).isoformat())
        self.assertEqual((ok, code), (False, 400))
        ok, code, _ = self.submit(signal_id="dup-1")
        self.assertEqual((ok, code), (True, 202))
        ok, code, _ = self.submit(signal_id="dup-1")
        self.assertEqual((ok, code), (False, 409))

    def test_offset_and_epoch_timestamps_are_normalised_to_utc(self):
        now = datetime.now(timezone.utc)
        offset = now.astimezone(timezone(timedelta(hours=5))).isoformat()
        ok, code, body = self.submit(signal_id="offset-1", bar_time=offset)
        self.assertEqual((ok, code), (True, 202), body)
        self.assertTrue(self.service.get_signal("offset-1")["bar_time"].endswith("+00:00"))
        ok, code, _ = self.submit(signal_id="epoch-1", bar_time=int(now.timestamp()))
        self.assertEqual((ok, code), (True, 202))
        ok, code, _ = self.submit(signal_id="future-1", bar_time=(now + timedelta(hours=1)).isoformat())
        self.assertEqual((ok, code), (False, 400))  # far-future timestamps are as suspect as stale ones

    def test_naive_timestamps_are_treated_as_utc(self):
        naive = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        ok, code, _ = self.submit(signal_id="naive-1", bar_time=naive)
        self.assertEqual((ok, code), (True, 202))


class ApiBehaviourTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.feed = PriceFeed(TSLA=100.0)

    def client(self, env=None, signed_in=True):
        app, patcher = make_app(self.tmp.name, env)
        self.addCleanup(patcher.stop)
        self.app = app
        client = app.test_client()
        if signed_in:
            login(client)
        return client

    def new_signal(self, client, **overrides):
        response = post_webhook(client, webhook_payload(**overrides))
        self.assertEqual(response.status_code, 202, response.get_json())
        return response.get_json()["data"]["signal_id"]

    def test_state_changing_endpoints_require_authentication(self):
        client = self.client(signed_in=False)
        signal_id = self.new_signal(client)
        for path in (f"/api/signals/{signal_id}/approve", f"/api/signals/{signal_id}/decline", "/api/paper/alerts/x/dismiss", "/api/paper/alerts/x/resolve", "/api/paper/monitor/run"):
            response = client.post(path, json={})
            self.assertEqual(response.status_code, 401, path)
        self.assertEqual(client.get(f"/api/signals/{signal_id}").get_json()["data"]["status"], "AWAITING_APPROVAL")

    def test_approver_allow_list(self):
        client = self.client(env={"PLUTO_APPROVER_USERS": "boss"})
        signal_id = self.new_signal(client)
        self.assertEqual(client.post(f"/api/signals/{signal_id}/approve", json={}).status_code, 403)
        login(client, "boss")
        with patched_prices(self.feed):
            self.assertEqual(client.post(f"/api/signals/{signal_id}/approve", json={}).status_code, 200)

    def test_malformed_bodies_are_400_not_500(self):
        client = self.client()
        signal_id = self.new_signal(client)
        url = f"/api/signals/{signal_id}/approve"
        for body in ('["a"]', '"text"', "42", "null", "{bad json", '{"note": 5}', '{"note": "' + "x" * 600 + '"}', '{"acknowledge_stale": "yes"}'):
            response = client.post(url, data=body, headers={"Content-Type": "application/json"})
            self.assertEqual(response.status_code, 400, body)
        response = client.post(f"/api/signals/{signal_id}/decline", data='{"reason": ["x"]}', headers={"Content-Type": "application/json"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(client.get(f"/api/signals/{signal_id}").get_json()["data"]["status"], "AWAITING_APPROVAL")

    def test_webhook_rejects_nan_literals_oversized_and_non_object_bodies(self):
        client = self.client(signed_in=False)
        raw = '{"symbol":"TSLA","strategy":"pluto-test-v1","direction":"LONG","timeframe":"60","price":NaN,"bar_time":"%s"}' % datetime.now(timezone.utc).isoformat()
        self.assertEqual(post_webhook(client, {}, raw=raw).status_code, 400)
        self.assertEqual(post_webhook(client, {}, raw='["x"]').status_code, 400)
        self.assertEqual(post_webhook(client, {}, raw="null").status_code, 400)
        self.assertEqual(post_webhook(client, {}, raw="x" * (300 * 1024)).status_code, 413)
        self.assertEqual(post_webhook(client, webhook_payload(), secret="nope").status_code, 401)

    def test_unknown_signal_ids_are_404(self):
        client = self.client()
        for action in ("approve", "decline"):
            self.assertEqual(client.post(f"/api/signals/nope/{action}", json={}).status_code, 404)
        self.assertEqual(client.get("/api/signals?limit=abc").status_code, 200)
        self.assertEqual(client.get("/api/signals?limit=-5").status_code, 200)

    def test_approve_twice_executes_once(self):
        client = self.client()
        signal_id = self.new_signal(client)
        with patched_prices(self.feed):
            first = client.post(f"/api/signals/{signal_id}/approve", json={})
            second = client.post(f"/api/signals/{signal_id}/approve", json={})
        self.assertEqual((first.status_code, second.status_code), (200, 200))
        self.assertTrue(second.get_json()["data"]["idempotent"])
        self.assertEqual(len(client.get("/api/paper/trades").get_json()["data"]["trades"]), 1)
        with patched_prices(self.feed):
            self.assertEqual(client.get("/api/paper/account").get_json()["data"]["cash"], 90000.0)

    def test_decline_after_execution_is_conflict_and_changes_nothing(self):
        client = self.client()
        signal_id = self.new_signal(client)
        with patched_prices(self.feed):
            client.post(f"/api/signals/{signal_id}/approve", json={})
        response = client.post(f"/api/signals/{signal_id}/decline", json={"reason": "oops"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(client.get(f"/api/signals/{signal_id}").get_json()["data"]["status"], "PAPER_EXECUTED")
        again = client.post(f"/api/signals/{signal_id}/decline", json={"reason": "oops"})
        self.assertEqual(again.status_code, 409)

    def test_stale_market_data_blocks_entry_until_signal_matches_market(self):
        client = self.client()
        signal_id = self.new_signal(client)
        self.feed.set("TSLA", 130.0)  # market ran 30% away from the signal
        with patched_prices(self.feed):
            response = client.post(f"/api/signals/{signal_id}/approve", json={})
        self.assertEqual(response.status_code, 422)
        self.assertIn("stale", response.get_json()["error"])
        self.assertEqual(client.get("/api/paper/trades").get_json()["data"]["trades"], [])
        self.assertEqual(client.get(f"/api/signals/{signal_id}").get_json()["data"]["status"], "FAILED")

    def test_unavailable_market_price_is_retryable(self):
        client = self.client()
        signal_id = self.new_signal(client)
        self.feed.available = False
        with patched_prices(self.feed):
            response = client.post(f"/api/signals/{signal_id}/approve", json={})
        self.assertEqual(response.status_code, 422)
        self.assertTrue(response.get_json()["data"]["retryable"])
        self.assertEqual(client.get(f"/api/signals/{signal_id}").get_json()["data"]["status"], "AWAITING_APPROVAL")
        self.feed.available = True
        with patched_prices(self.feed):
            self.assertEqual(client.post(f"/api/signals/{signal_id}/approve", json={}).status_code, 200)

    def test_stale_analysis_flow_over_http(self):
        client = self.client()
        self.app.extensions["signal_service"]._analyzer = lambda s: {"warnings": ["x"], "data_freshness": "STALE", "entry_zone": s["price"], "stop_loss": s["stop_loss"], "primary_target": s["take_profit"]}
        signal_id = self.new_signal(client)
        with patched_prices(self.feed):
            blocked = client.post(f"/api/signals/{signal_id}/approve", json={})
            self.assertEqual(blocked.status_code, 409)
            self.assertEqual(blocked.get_json()["data"]["code"], "STALE_DATA")
            allowed = client.post(f"/api/signals/{signal_id}/approve", json={"acknowledge_stale": True})
        self.assertEqual(allowed.status_code, 200)

    def test_frozen_entries_release_the_approval_for_retry(self):
        client = self.client()
        engine = self.app.extensions["paper_engine"]
        with engine._lock:  # noqa: SLF001
            ledger = engine._load()  # noqa: SLF001
            engine._upsert_alert(ledger, "T1", "PROTECTION_FAILED", "broker uncertain", freeze=True)  # noqa: SLF001
            engine._save(ledger)  # noqa: SLF001
        signal_id = self.new_signal(client)
        with patched_prices(self.feed):
            response = client.post(f"/api/signals/{signal_id}/approve", json={})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(client.get(f"/api/signals/{signal_id}").get_json()["data"]["status"], "AWAITING_APPROVAL")
        alert = client.get("/api/paper/alerts").get_json()["data"]["alerts"][0]
        self.assertEqual(client.post(f"/api/paper/alerts/{alert['alert_id']}/resolve", json={"note": "fixed"}).status_code, 200)
        self.assertFalse(client.get("/api/paper/monitor").get_json()["data"]["freeze_new_entries"])
        with patched_prices(self.feed):
            self.assertEqual(client.post(f"/api/signals/{signal_id}/approve", json={}).status_code, 200)

    def test_registration_can_be_disabled(self):
        client = self.client(env={"PLUTO_ALLOW_REGISTRATION": "false"}, signed_in=False)
        response = client.post("/auth/register", data={"username": "mallory", "password": "correct-horse", "confirm_password": "correct-horse"})
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()


class MissionEndpointValidationTests(unittest.TestCase):
    def test_malformed_mission_bodies_are_400(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, patcher = make_app(tmp)
            self.addCleanup(patcher.stop)
            client = app.test_client()
            for body in ('["x"]', '"AAPL"', '{"ticker": {"a": 1}}', '{"ticker": 5}', '{"ticker": "AAPL", "assigned_scanners": "movers"}',
                         '{"ticker": "AAPL", "assigned_brains": [1, 2]}', '{"ticker": "AAPL", "monitoring_flags": {"a": 1}}'):
                for path in ("/api/missions/assign", "/api/watchlist"):
                    response = client.post(path, data=body, headers={"Content-Type": "application/json"})
                    self.assertEqual(response.status_code, 400, (path, body))
