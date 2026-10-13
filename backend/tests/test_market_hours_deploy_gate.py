from __future__ import annotations

import importlib.util
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_NY = ZoneInfo("America/New_York")


def _gate():
    path = Path(__file__).resolve().parents[1] / "scripts" / "market_hours_deploy_gate.py"
    spec = importlib.util.spec_from_file_location("market_hours_deploy_gate", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_weekday_regular_session_blocks_and_the_close_does_not():
    gate = _gate()
    tuesday_open = datetime(2026, 10, 13, 9, 30, tzinfo=_NY)
    tuesday_mid = datetime(2026, 10, 13, 10, 0, tzinfo=_NY)
    tuesday_close = datetime(2026, 10, 13, 16, 0, tzinfo=_NY)
    saturday = datetime(2026, 10, 17, 12, 0, tzinfo=_NY)
    assert "regular US market hours" in gate.market_hours_block(tuesday_open)
    assert "regular US market hours" in gate.market_hours_block(tuesday_mid)
    assert gate.market_hours_block(tuesday_close) == ""
    assert gate.market_hours_block(saturday) == ""


def test_override_allows_a_hotfix_during_the_session(monkeypatch):
    gate = _gate()
    monkeypatch.setenv("PLUTO_ALLOW_MARKET_HOURS_DEPLOY", "1")
    moment = datetime(2026, 10, 13, 10, 0, tzinfo=_NY)
    assert gate.market_hours_block(moment) == ""
