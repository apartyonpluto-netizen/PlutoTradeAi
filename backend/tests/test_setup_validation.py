"""Walk-forward validation: fills, costs, no look-ahead, status rules, and
recognition staying separate from permission to trade."""

from __future__ import annotations

import json
import time
from unittest.mock import patch

import pytest

import app as pluto_app
import auth
from setups import evidence, model, service, validation
from setups.bars import Bars
from setup_fixtures import make_bars, path


def _rows(ohlc):
    return Bars.from_rows("SIM", "1d", [{"t": f"2026-01-{i + 1:02d}T21:00:00+00:00", "o": o, "h": h, "l": l, "c": c, "v": 1e6}
                                        for i, (o, h, l, c) in enumerate(ohlc)])


def _detection(direction="long", confirmed_index=0, stop=95.0, target=110.0):
    return {"detector_id": "bull_flag", "version": "1.0.0", "direction": direction, "confirmed_index": confirmed_index,
            "levels": {"invalidation": stop, "target": target}, "regime": {"trend": "uptrend"}}


def test_entry_is_the_next_bar_open_and_the_stop_wins_a_shared_bar():
    bars = _rows([(100, 101, 99, 100), (101, 112, 94, 105), (105, 106, 104, 105)])
    trade = validation.simulate(bars, _detection(), max_hold=5, cost_bps=0)
    assert trade["entry"] == 101 and trade["exit_reason"] == "stop" and trade["exit"] == 95.0
    assert trade["gross_r"] == pytest.approx(-1.0)


def test_target_gap_and_costs():
    bars = _rows([(100, 101, 99, 100), (101, 103, 100, 102), (115, 116, 114, 115)])
    trade = validation.simulate(bars, _detection(), max_hold=5, cost_bps=5)
    assert trade["exit_reason"] == "target" and trade["exit"] == 115  # gapped through the target: filled at the open
    assert trade["net_r"] == pytest.approx(trade["gross_r"] - 2 * 5 / 10_000 * 101 / 6, abs=1e-3)


def test_short_trades_and_time_exits():
    bars = _rows([(100, 101, 99, 100), (99, 100, 98, 99), (98, 99, 97, 98), (97, 98, 96, 97)])
    trade = validation.simulate(bars, _detection("short", stop=103.0, target=80.0), max_hold=3, cost_bps=0)
    assert trade["exit_reason"] == "time" and trade["exit"] == 97 and trade["gross_r"] == pytest.approx((99 - 97) / 4)


def test_unresolved_trades_and_gaps_past_the_stop_are_not_counted():
    bars = _rows([(100, 101, 99, 100), (101, 102, 100, 101)])
    assert validation.simulate(bars, _detection(), max_hold=5, cost_bps=0) is None
    gapped = _rows([(100, 101, 99, 100), (94, 95, 93, 94), (94, 95, 93, 94)])
    assert "skipped" in validation.simulate(gapped, _detection(), max_hold=1, cost_bps=0)


def test_walk_forward_trades_only_after_confirmation():
    closes = path([(0, 100), (60, 100), (8, 125), (6, 122), (1, 123.4), (6, 135), (10, 130)])
    volumes = [1e6] * len(closes)
    volumes[75] = 3e6
    bars = make_bars(closes, volumes=volumes)
    trades = validation.walk_forward(bars, start=60, max_hold=10)
    flag = [t for t in trades if t["detector_id"] == "bull_flag"]
    assert flag, trades
    for trade in trades:
        assert trade["entry"] == pytest.approx(float(bars.o[trade["index"] + 1]), abs=1e-3)


def test_status_needs_out_of_sample_and_forward_evidence():
    good = evidence.summarize_r([0.5, -0.2, 0.8, 0.4] * 10)
    assert evidence.status_from({"n": 10, "mean_r": 1}, {"n": 0}) == model.RESEARCH
    assert evidence.status_from(good, {"n": 0}) == model.BACKTEST_SUPPORTED
    assert evidence.status_from(good, {"n": 25, "mean_r": 0.2}) == model.VALIDATED
    assert evidence.status_from(evidence.summarize_r([-0.5, 0.1] * 20), {"n": 0}) == model.BACKTEST_REJECTED


def test_a_server_backtest_feeds_the_lookup(tmp_path, monkeypatch):
    target = tmp_path / "setup_evidence_backtest.json"
    monkeypatch.setattr(evidence, "BACKTEST_FILE", target)
    trades = [{"detector_id": "bull_flag", "version": "1.0.0", "timeframe": "1d", "symbol": f"S{i % 5}", "net_r": 0.9 if i % 3 else -0.5,
               "position": 0.9, "exit_reason": "target", "regime": "uptrend"} for i in range(45)]
    target.write_text(json.dumps({"generated_at": "2099-01-01T00:00:00+00:00", "entries": validation.aggregate(trades)}))
    found = evidence.lookup("bull_flag", "1.0.0", "1d")
    assert found["out_of_sample"]["n"] == 45 and found["status"] == model.BACKTEST_SUPPORTED
    assert not evidence.execution_eligible("bull_flag", "1.0.0", "1d")  # no forward evidence yet
    assert evidence.lookup("bull_flag", "2.0.0", "1d")["status"] == model.RESEARCH  # evidence is per version


def test_one_validation_run_at_a_time(tmp_path, monkeypatch):
    monkeypatch.setattr(evidence, "DATA_DIR", tmp_path)
    monkeypatch.setattr(evidence, "BACKTEST_FILE", tmp_path / "research" / "setup_evidence_backtest.json")
    monkeypatch.setattr(validation, "BACKTEST_FILE", tmp_path / "research" / "setup_evidence_backtest.json")
    release = {"go": False}

    def slow_fetch(symbols, timeframe):
        while not release["go"]:
            time.sleep(0.01)
        return {"FLAT": make_bars(path([(0, 100), (300, 140)]), symbol="FLAT")}

    monkeypatch.setattr(service, "_fetch_history", slow_fetch)
    monkeypatch.setattr(validation, "run", lambda symbols, fetch, progress=None, **kw: (fetch(symbols, "1d"), {"trades": 0, "universe_size": 1})[1])
    first = service.start_validation(["FLAT"], reason="test")
    second = service.start_validation(["FLAT"], reason="test")
    assert first["started"] and not second["started"]
    release["go"] = True
    for _ in range(200):
        if service.validation_status()["state"] == "done":
            break
        time.sleep(0.01)
    assert service.validation_status()["state"] == "done"


def test_validation_admin_route_is_admin_only(user_id):
    user = auth.register_user(f"val-{user_id[:8]}", "TestPassword123!")
    auth.approve_user(user["id"])
    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = user["id"]
        with patch.object(pluto_app, "is_admin", return_value=False):
            assert client.post("/api/admin/setups/validation").status_code == 403
        with patch.object(pluto_app, "is_admin", return_value=True), \
             patch.object(service, "start_validation", return_value={"started": True}) as start:
            response = client.post("/api/admin/setups/validation")
        assert response.status_code == 200 and start.called
