"""Setup discovery API and Pattern Brain rows (bars mocked - no network)."""

from __future__ import annotations

from unittest.mock import patch

import app as pluto_app
import auth
import pattern_brain
from setups import service
from setup_fixtures import make_bars, path

FLAG = path([(0, 100), (60, 100), (8, 125), (6, 122), (1, 123.4)])


def _fake_fetch(symbols, timeframe):
    if timeframe != "1d":
        return {}
    volumes = [1e6] * len(FLAG)
    volumes[-1] = 3e6
    return {s.upper(): make_bars(FLAG, symbol=s.upper(), volumes=volumes) for s in symbols if s.upper() != "NODATA"}


def _client(user_id):
    user = auth.register_user(f"setups-{user_id[:8]}", "TestPassword123!")
    auth.approve_user(user["id"])
    client = pluto_app.app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = user["id"]
    return client


def test_pattern_brain_rows_carry_setups_and_no_invented_confidence():
    with patch.object(service, "fetch_bars", side_effect=_fake_fetch):
        row = pattern_brain.analyze_patterns("FLAG")
    assert row["ticker"] == "FLAG" and row["opportunities"]
    assert all("confidence" not in p for p in row["patterns"])
    opportunity = row["opportunities"][0]
    assert opportunity["chart"]["bars"] and opportunity["chart"]["points"]
    assert "primary_detection" not in opportunity


def test_scan_route_returns_opportunities_and_a_no_trade_summary(user_id):
    client = _client(user_id)
    with patch.object(service, "fetch_bars", side_effect=_fake_fetch):
        response = client.get("/api/setups/scan?symbols=FLAG,NODATA&timeframes=1d")
    assert response.status_code == 200
    data = response.get_json()["data"]
    by_symbol = {r["symbol"]: r for r in data["results"]}
    assert by_symbol["FLAG"]["opportunities"][0]["setup"]["id"] == "bull_flag"
    assert by_symbol["FLAG"]["summary"].startswith("no qualifying trade")  # nothing validated yet
    assert data["qualifying"] == []
    assert by_symbol["NODATA"]["summary"] == "no data"
    assert client.get("/api/setups/scan").status_code == 400


def test_coverage_route_lists_rules_status_and_unsupported_names(user_id):
    client = _client(user_id)
    data = client.get("/api/setups/coverage").get_json()["data"]
    assert len(data["detectors"]) >= 30
    flag = next(d for d in data["detectors"] if d["id"] == "bull_flag")
    assert flag["validation"]["1d"] == "research" and flag["confirmation"]
    assert any(p["name"] == "King's crown" for p in data["not_supported"])


def test_setup_routes_require_login():
    client = pluto_app.app.test_client()
    assert client.get("/api/setups/coverage").status_code in (302, 401)
