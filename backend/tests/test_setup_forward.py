"""Forward evidence: recorded the day a setup confirms, scored only later."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from setups import evidence, forward, service
from setup_fixtures import make_bars, path

FLAG = path([(0, 100), (60, 100), (8, 125), (6, 122), (1, 123.4)])


@pytest.fixture
def files(tmp_path, monkeypatch):
    monkeypatch.setattr(forward, "LOG_FILE", tmp_path / "log.json")
    monkeypatch.setattr(evidence, "FORWARD_FILE", tmp_path / "forward.json")
    return tmp_path


def _bars(closes):
    volumes = [1e6] * len(closes)
    volumes[len(FLAG) - 1] = 3e6
    return make_bars(closes, symbol="FLAG", volumes=volumes)


def test_a_same_day_confirmation_is_recorded_once_and_resolved_later(files):
    first = forward.run({"FLAG": _bars(FLAG)}, completed=lambda b: b)
    assert first["added"] >= 1 and first["resolved"] == 0
    again = forward.run({"FLAG": _bars(FLAG)}, completed=lambda b: b)
    assert again["added"] == 0  # never recorded twice
    later = FLAG + path([(0, FLAG[-1]), (6, 152)])[1:]  # price runs through the flag's target
    done = forward.run({"FLAG": _bars(later)}, completed=lambda b: b)
    assert done["resolved"] >= 1
    stats = evidence.lookup("bull_flag", "1.0.0", "1d")["forward"]
    assert stats["n"] >= 1


def test_older_confirmations_are_never_backfilled(files):
    later = FLAG + [FLAG[-1]] * 2
    result = forward.run({"FLAG": _bars(later)}, completed=lambda b: b)
    assert result["added"] == 0


def test_a_forming_bar_is_not_used(files):
    bars = _bars(FLAG)
    result = forward.run({"FLAG": bars}, completed=lambda b: b.upto(len(b) - 2))
    assert result["added"] == 0  # the breakout bar is still forming


def test_due_once_per_weekday_after_the_close(files):
    assert not service.forward_tracking_due(datetime(2026, 10, 3, 21, 30, tzinfo=timezone.utc))  # Saturday
    assert not service.forward_tracking_due(datetime(2026, 9, 30, 19, 0, tzinfo=timezone.utc))
    assert service.forward_tracking_due(datetime(2026, 9, 30, 21, 5, tzinfo=timezone.utc))
    forward.run({}, completed=lambda b: b, now="2026-09-30T21:05:00+00:00")
    assert not service.forward_tracking_due(datetime(2026, 9, 30, 21, 30, tzinfo=timezone.utc))
