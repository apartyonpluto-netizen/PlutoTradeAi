from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

import auth
import calibration
import calibration_store
from autonomy.closed_trades import record_closed_trade

"""calibration_store.py's score_multiplier has always applied ONE shared,
deployment-wide multiplier per strategy (not per-user) - derived, until
2026-09-11, only from backtest_engine's simulated trades. These tests
cover the real-outcomes extension: a second, preferred multiplier source
derived from this deployment's OWN real closed trades (across every
account, matching the existing shared-scope architecture), an automatic
recalibration trigger every REAL_OUTCOMES_RECALIBRATION_TRADE_INTERVAL
newly-closed trades, and the fix to start_calibration/_run_calibration_sync
so a backtest run no longer clobbers real-outcomes data living in the
same shared calibration file.

CALIBRATION_FILE is a single global file, not scoped by user_id like every
other store this codebase tests - _reset_calibration_file below resets it
before every test in this module so tests can't leak calibration state
into each other (or into other test files sharing the same session-wide
temp PLUTO_DATA_DIR). Each test also uses its own uuid-suffixed strategy
name so a real closed trade recorded by some OTHER test file's registered
user (list_all_users() sees every registered user for the whole session)
can never be mistaken for this test's own data."""


@pytest.fixture(autouse=True)
def _reset_calibration_file():
    calibration_store.write_calibration({"status": "never_run", "generated_at": "", "strategy_stats": {}})
    yield


def _registered_user(prefix: str) -> str:
    user = auth.register_user(f"{prefix}-{uuid.uuid4().hex[:10]}", "TestPassword123!")
    auth.approve_user(user["id"])
    return user["id"]


def _strategy_name() -> str:
    return f"CalTest-{uuid.uuid4().hex[:8]}"


def _closed_trade(strategy: str, net_realized_pnl: float | None, entry_price: float = 100.0, quantity: float = 10.0, pnl_status: str = "complete") -> dict:
    return {
        "ticker": "NVDA",
        "strategy": strategy,
        "net_realized_pnl": net_realized_pnl,
        "average_entry_price": entry_price,
        "exited_quantity": quantity,
        "filled_quantity": quantity,
        "pnl_status": pnl_status,
    }


# --- calibration_store.score_multiplier: real vs backtest preference -------


def test_no_calibration_data_at_all_returns_neutral_multiplier():
    assert calibration_store.score_multiplier("AnyStrategy") == 1.0


def test_backtest_only_multiplier_is_used_when_no_real_data_exists():
    calibration_store.write_calibration({
        "status": "done", "generated_at": "t",
        "strategy_stats": {"Momentum": {"avg_return_percent": 5.0, "trusted": True}},
    })
    assert calibration_store.score_multiplier("Momentum") == pytest.approx(1.25)  # capped at +25%


def test_untrusted_backtest_stats_leave_the_multiplier_neutral():
    calibration_store.write_calibration({
        "status": "done", "generated_at": "t",
        "strategy_stats": {"Momentum": {"avg_return_percent": 5.0, "trusted": False}},
    })
    assert calibration_store.score_multiplier("Momentum") == 1.0


def test_real_outcomes_multiplier_is_preferred_over_backtest_when_both_trusted():
    calibration_store.write_calibration({
        "status": "done", "generated_at": "t",
        "strategy_stats": {"Momentum": {"avg_return_percent": 5.0, "trusted": True}},
        "real_strategy_stats": {"Momentum": {"avg_return_percent": -2.0, "trusted": True}},
    })
    # Real data says -20% adjustment, backtest says +25% - real must win.
    assert calibration_store.score_multiplier("Momentum") == pytest.approx(0.8)


def test_untrusted_real_stats_fall_back_to_trusted_backtest_stats():
    calibration_store.write_calibration({
        "status": "done", "generated_at": "t",
        "strategy_stats": {"Momentum": {"avg_return_percent": 5.0, "trusted": True}},
        "real_strategy_stats": {"Momentum": {"avg_return_percent": -2.0, "trusted": False}},
    })
    assert calibration_store.score_multiplier("Momentum") == pytest.approx(1.25)


def test_trusted_real_stats_apply_even_when_backtest_never_ran():
    # A strategy can earn real trade history before any backtest has ever
    # been run - the real multiplier must not require status == "done".
    calibration_store.write_calibration({
        "status": "never_run", "generated_at": "", "strategy_stats": {},
        "real_strategy_stats": {"Momentum": {"avg_return_percent": 1.5, "trusted": True}},
    })
    assert calibration_store.score_multiplier("Momentum") == pytest.approx(1.15)


# --- calibration_store.record_closed_trade_for_recalibration_trigger -------


def test_the_counter_increments_and_is_not_due_before_the_interval():
    for _ in range(calibration_store.REAL_OUTCOMES_RECALIBRATION_TRADE_INTERVAL - 1):
        assert calibration_store.record_closed_trade_for_recalibration_trigger() is False


def test_the_counter_becomes_due_exactly_at_the_interval_and_then_resets():
    for _ in range(calibration_store.REAL_OUTCOMES_RECALIBRATION_TRADE_INTERVAL - 1):
        calibration_store.record_closed_trade_for_recalibration_trigger()
    assert calibration_store.record_closed_trade_for_recalibration_trigger() is True
    # Reset - the next call starts a fresh count, not immediately due again.
    assert calibration_store.record_closed_trade_for_recalibration_trigger() is False


# --- calibration.recalibrate_from_real_outcomes -----------------------------


def test_no_closed_trades_produces_an_empty_real_strategy_stats_without_crashing():
    result = calibration.recalibrate_from_real_outcomes()
    assert result["real_strategy_stats"] == {}
    assert calibration_store.get_calibration()["real_strategy_stats"] == {}


def test_real_trades_are_aggregated_by_strategy_across_users():
    strategy = _strategy_name()
    user_a = _registered_user("cal-a")
    user_b = _registered_user("cal-b")
    # +10% and -2% on a $1000 cost basis (100 x 10 shares).
    record_closed_trade(user_a, "t1", _closed_trade(strategy, 100.0))
    record_closed_trade(user_b, "t2", _closed_trade(strategy, -20.0))

    result = calibration.recalibrate_from_real_outcomes()
    stats = result["real_strategy_stats"][strategy]
    assert stats["trade_count"] == 2
    assert stats["win_count"] == 1
    assert stats["avg_return_percent"] == pytest.approx(4.0)  # (10% + -2%) / 2


def test_incomplete_pnl_trades_are_excluded_not_treated_as_zero():
    strategy = _strategy_name()
    user_a = _registered_user("cal-incomplete")
    record_closed_trade(user_a, "t1", _closed_trade(strategy, 100.0))
    record_closed_trade(user_a, "t2", _closed_trade(strategy, None, pnl_status="incomplete_missing_fill_price"))

    result = calibration.recalibrate_from_real_outcomes()
    assert result["real_strategy_stats"][strategy]["trade_count"] == 1


def test_a_strategy_below_the_trust_threshold_is_computed_but_flagged_untrusted():
    strategy = _strategy_name()
    user_a = _registered_user("cal-small")
    record_closed_trade(user_a, "t1", _closed_trade(strategy, 50.0))

    result = calibration.recalibrate_from_real_outcomes()
    assert result["real_strategy_stats"][strategy]["trusted"] is False


def test_a_strategy_at_the_trust_threshold_is_flagged_trusted():
    strategy = _strategy_name()
    user_a = _registered_user("cal-enough")
    for i in range(calibration_store.MIN_TRADES_TO_TRUST):
        record_closed_trade(user_a, f"t{i}", _closed_trade(strategy, 5.0))

    result = calibration.recalibrate_from_real_outcomes()
    assert result["real_strategy_stats"][strategy]["trusted"] is True


def test_real_outcomes_recalibration_does_not_clobber_backtest_stats():
    calibration_store.write_calibration({
        "status": "done", "generated_at": "t",
        "strategy_stats": {"BacktestOnly": {"avg_return_percent": 5.0, "trusted": True}},
    })
    calibration.recalibrate_from_real_outcomes()
    stored = calibration_store.get_calibration()
    assert stored["strategy_stats"]["BacktestOnly"]["avg_return_percent"] == 5.0


def test_starting_a_backtest_preserves_existing_real_outcomes_stats_in_the_running_write(monkeypatch):
    # Regression test for the exact bug found while wiring this up:
    # start_calibration used to overwrite the whole calibration file with
    # a fresh dict literal for its "running" status write, silently
    # deleting real_strategy_stats the moment anyone started a backtest -
    # before the backtest itself even ran. threading.Thread is faked out
    # so this never spawns a real thread or touches the network.
    calibration_store.write_calibration({
        "status": "never_run", "generated_at": "", "strategy_stats": {},
        "real_strategy_stats": {"RealOnly": {"avg_return_percent": 2.0, "trusted": True}},
    })
    monkeypatch.setattr(calibration.threading, "Thread", lambda *a, **k: MagicMock())
    result = calibration.start_calibration(["AAPL"])
    assert result["status"] == "started"
    stored = calibration_store.get_calibration()
    assert stored["status"] == "running"
    assert stored["real_strategy_stats"]["RealOnly"]["avg_return_percent"] == 2.0
    # Thread was faked out (never really started), so nothing will ever
    # release the lock start_calibration acquired - release it ourselves
    # so later tests in this file aren't blocked by "already_running".
    calibration._calibration_lock.release()


def test_finishing_a_backtest_preserves_existing_real_outcomes_stats_in_the_done_write(monkeypatch):
    # Same regression, for _run_calibration_sync's own "done" write -
    # tested by calling it directly (synchronously, no thread) rather than
    # through start_calibration, so this never touches the network either.
    calibration_store.write_calibration({
        "status": "never_run", "generated_at": "", "strategy_stats": {},
        "real_strategy_stats": {"RealOnly": {"avg_return_percent": 2.0, "trusted": True}},
    })
    monkeypatch.setattr(calibration, "run_ticker_backtest", lambda *a, **k: {"trades": []})
    assert calibration._calibration_lock.acquire(blocking=False)
    calibration._run_calibration_sync(["AAPL"], 6, 5, 55)  # releases the lock itself when done
    stored = calibration_store.get_calibration()
    assert stored["status"] == "done"
    assert stored["real_strategy_stats"]["RealOnly"]["avg_return_percent"] == 2.0


# --- calibration.maybe_trigger_real_outcomes_recalibration ------------------


def test_maybe_trigger_does_nothing_before_the_interval_is_reached():
    for _ in range(calibration_store.REAL_OUTCOMES_RECALIBRATION_TRADE_INTERVAL - 1):
        calibration.maybe_trigger_real_outcomes_recalibration()
    assert "real_strategy_stats" not in calibration_store.get_calibration()


def test_maybe_trigger_recalibrates_once_the_interval_is_reached():
    strategy = _strategy_name()
    user_a = _registered_user("cal-trigger")
    record_closed_trade(user_a, "t1", _closed_trade(strategy, 50.0))
    for _ in range(calibration_store.REAL_OUTCOMES_RECALIBRATION_TRADE_INTERVAL):
        calibration.maybe_trigger_real_outcomes_recalibration()
    stored = calibration_store.get_calibration()
    assert strategy in stored["real_strategy_stats"]


def test_maybe_trigger_never_raises_even_if_the_underlying_recalibration_fails(monkeypatch):
    def _boom():
        raise RuntimeError("disk full")
    monkeypatch.setattr(calibration, "recalibrate_from_real_outcomes", _boom)
    for _ in range(calibration_store.REAL_OUTCOMES_RECALIBRATION_TRADE_INTERVAL):
        calibration.maybe_trigger_real_outcomes_recalibration()  # must not raise
