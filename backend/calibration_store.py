from __future__ import annotations

import contextlib
import fcntl
import json
import os
from pathlib import Path
from typing import Any, Dict

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("PLUTO_DATA_DIR", str(BASE_DIR / "data"))).resolve()
CALIBRATION_FILE = DATA_DIR / "strategy_calibration.json"

# Below this many recorded trades, a strategy's measured win rate is treated
# as noise, not evidence - the raw hand-tuned score is left unadjusted rather
# than swung around by a handful of lucky/unlucky trades. Reused as-is for
# REAL trade counts too (see real_strategy_stats below) - the plan this
# implements deliberately calls for "the same MIN_TRADES_TO_TRUST", not a
# separate threshold for real vs backtested evidence.
MIN_TRADES_TO_TRUST = 15
MAX_SCORE_ADJUSTMENT = 0.25

# How many newly-closed trades (across ALL users - see calibration.py's
# real-outcomes recalibration, which is deployment-wide, matching this
# file's existing single-shared-calibration architecture, not per-user)
# trigger an automatic real-outcomes recalibration. Deliberately modest:
# unlike backtest calibration (network calls per ticker, run manually),
# this is a fast local-JSON aggregation - see
# record_closed_trade_for_recalibration_trigger's own docstring.
REAL_OUTCOMES_RECALIBRATION_TRADE_INTERVAL = 10


def write_calibration(payload: Dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CALIBRATION_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def get_calibration() -> Dict[str, Any]:
    if not CALIBRATION_FILE.exists():
        return {"status": "never_run", "generated_at": "", "strategy_stats": {}}
    try:
        data = json.loads(CALIBRATION_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"status": "never_run", "generated_at": "", "strategy_stats": {}}
    return data if isinstance(data, dict) else {"status": "never_run", "generated_at": "", "strategy_stats": {}}


@contextlib.contextmanager
def _locked():
    """Same exclusive-lock-around-read-modify-write discipline as
    research_log.py/closed_trades.py - added 2026-09-11 alongside the
    real-outcomes trade counter below, the first thing in this module that
    does a read-modify-write from potentially concurrent gunicorn workers
    (every trade-close site in app.py). get_calibration/write_calibration
    themselves stay unlocked, same as before - the backtest calibration
    thread is already serialized by calibration.py's own _calibration_lock,
    and a single overwrite there has always been last-write-wins by
    design (a status/progress file, not an append-only log)."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = CALIBRATION_FILE.with_suffix(CALIBRATION_FILE.suffix + ".lock")
    lock_path.touch(exist_ok=True)
    with open(lock_path, "r+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def record_closed_trade_for_recalibration_trigger() -> bool:
    """Call once per newly-closed trade, from any of app.py's closed-trade
    recording sites (any user). Returns True the moment the running count
    since the last real-outcomes recalibration reaches
    REAL_OUTCOMES_RECALIBRATION_TRADE_INTERVAL, resetting the counter back
    to 0 as part of the SAME locked read-modify-write - so two gunicorn
    workers closing a trade at nearly the same moment can't both "claim"
    the same trigger (one sees the reset counter, the other sees it cross
    the interval on its own next call instead). The caller
    (calibration.maybe_trigger_real_outcomes_recalibration) is responsible
    for actually kicking off a recalibration when this returns True - this
    function only owns the counter."""
    with _locked():
        calibration = get_calibration()
        count = int(calibration.get("closed_trades_since_last_real_recalibration", 0) or 0) + 1
        due = count >= REAL_OUTCOMES_RECALIBRATION_TRADE_INTERVAL
        calibration["closed_trades_since_last_real_recalibration"] = 0 if due else count
        write_calibration(calibration)
    return due


def _adjustment_from_avg_return_percent(avg_return_percent: float) -> float:
    return max(-MAX_SCORE_ADJUSTMENT, min(MAX_SCORE_ADJUSTMENT, avg_return_percent / 10.0))


def score_multiplier(strategy_name: str) -> float:
    """Multiplier applied to a strategy's raw hand-tuned score. Prefers a
    REAL-outcomes-derived multiplier (this account's own actual closed
    trades - see calibration.py's recalibrate_from_real_outcomes) over the
    backtested one when the real one has enough trade samples to trust;
    falls back to the backtested multiplier (see below) when it doesn't,
    exactly the behavior that existed before real-outcomes calibration was
    added - so a strategy with no real trade history yet is scored
    identically to how it always was, not silently reset to neutral.

    Either source is derived from its measured AVERAGE RETURN per trade -
    not win rate. A strategy that wins less than half the time but lets
    winners run bigger than its losses (e.g. a 49% win rate with a strong
    average return) is genuinely profitable and should be rewarded, not
    penalized for a coin-flip-adjacent win rate; win rate alone can't tell
    that difference, only expectancy can. 1.0 (no change) if neither
    source has enough trade samples yet. 0% average return leaves the
    score unchanged; further from 0% nudges it proportionally, capped so a
    small sample or a hot/cold streak can't swing the ranking wildly."""
    calibration = get_calibration()

    real_stats = calibration.get("real_strategy_stats", {}).get(strategy_name)
    if real_stats and real_stats.get("trusted"):
        return 1.0 + _adjustment_from_avg_return_percent(float(real_stats.get("avg_return_percent", 0.0)))

    if calibration.get("status") == "done":
        backtest_stats = calibration.get("strategy_stats", {}).get(strategy_name)
        if backtest_stats and backtest_stats.get("trusted"):
            return 1.0 + _adjustment_from_avg_return_percent(float(backtest_stats.get("avg_return_percent", 0.0)))

    return 1.0
