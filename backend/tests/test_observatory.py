"""Observatory data comes only from recorded events."""

from __future__ import annotations

import app as pluto_app
import auth
import observatory
from autonomy import event_journal as ej


def _chain(user_id, cid, ticker, types):
    for t in types:
        ej.emit(user_id, t, correlation_id=cid, ticker=ticker, source="test")


def test_empty_window_reports_nothing(user_id):
    data = observatory.build(user_id)
    assert data["event_count"] == 0 and data["observed_edges"] == [] and all(s["count"] == 0 for s in data["stages"])
    assert data["structural_edges"]  # the design is always shown, separately


def test_observed_transitions_are_counted_per_chain(user_id):
    _chain(user_id, "c-1", "AAPL", ["signal.evaluated", "plan.built", "order.entry_submitted", "order.entry_filled", "order.protection_confirmed_active"])
    _chain(user_id, "c-2", "AAPL", ["signal.evaluated", "plan.built"])
    _chain(user_id, "c-3", "XOM", ["signal.evaluated", "position.closed"])  # not a designed path
    data = observatory.build(user_id)
    edges = {(e["from"], e["to"]): e for e in data["observed_edges"]}
    assert edges[("signal", "plan")]["count"] == 2 and edges[("plan", "order")]["count"] == 1
    assert edges[("fill", "protection")]["structural"] is True
    assert edges[("signal", "exit")]["structural"] is False
    counts = {s["id"]: s["count"] for s in data["stages"]}
    assert counts["signal"] == 3 and counts["exit"] == 1 and data["chain_count"] == 3
    sectors = {s["sector"]: s for s in data["sectors"]}
    aapl = next(t for s in sectors.values() for t in s["tickers"] if t["ticker"] == "AAPL")
    assert aapl["chains"] == 2 and aapl["furthest_stage"] == "protection"
    ticker = observatory.build(user_id, level="ticker", ticker="aapl")["ticker"]
    assert [c["correlation_id"] for c in ticker["chains"]] and ticker["chains"][0]["stages"][0] == "signal"


def test_the_window_excludes_older_events(user_id, monkeypatch):
    _chain(user_id, "c-1", "AAPL", ["signal.evaluated", "plan.built"])
    monkeypatch.setattr(observatory, "_since", lambda hours: "2999-01-01T00:00:00+00:00")
    assert observatory.build(user_id)["event_count"] == 0


def test_routes(user_id):
    user = auth.register_user(f"obs-{user_id[:8]}", "TestPassword123!")
    auth.approve_user(user["id"])
    _chain(user["id"], "c-9", "MSFT", ["signal.evaluated", "plan.built"])
    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = user["id"]
        data = client.get("/api/observatory?window_hours=9999").get_json()["data"]
        assert data["window_hours"] == observatory.MAX_WINDOW_HOURS and data["event_count"] == 2
        assert client.get("/observatory").status_code == 200
