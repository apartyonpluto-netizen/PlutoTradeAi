from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Dict, List

if __package__:
    from .backtest_engine import run_ticker_backtest
    from .calibration_store import MIN_TRADES_TO_TRUST, get_calibration, score_multiplier, write_calibration
else:
    from backtest_engine import run_ticker_backtest
    from calibration_store import MIN_TRADES_TO_TRUST, get_calibration, score_multiplier, write_calibration

__all__ = ["get_calibration", "score_multiplier", "start_calibration"]

_calibration_lock = threading.Lock()


def _run_calibration_sync(tickers: List[str], lookback_months: int, hold_days: int, min_confidence: int) -> None:
    per_strategy: Dict[str, List[Dict[str, Any]]] = {}
    errors: List[str] = []

    for ticker in tickers:
        try:
            result = run_ticker_backtest(ticker, lookback_months=lookback_months, hold_days=hold_days, min_confidence=min_confidence)
        except Exception as error:  # noqa: BLE001 - one bad ticker shouldn't kill the whole calibration run
            errors.append(f"{ticker}: {error}")
            continue
        if result.get("error"):
            errors.append(f"{ticker}: {result['error']}")
            continue
        for trade in result["trades"]:
            per_strategy.setdefault(trade["strategy"], []).append(trade)

    strategy_stats: Dict[str, Dict[str, Any]] = {}
    for strategy_name, trades in per_strategy.items():
        wins = [t for t in trades if t["pnl_percent"] > 0]
        strategy_stats[strategy_name] = {
            "trade_count": len(trades),
            "win_count": len(wins),
            "win_rate_percent": round(len(wins) / len(trades) * 100, 1) if trades else 0.0,
            "avg_return_percent": round(sum(t["pnl_percent"] for t in trades) / len(trades), 2) if trades else 0.0,
            "trusted": len(trades) >= MIN_TRADES_TO_TRUST,
        }

    write_calibration(
        {
            "status": "done",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "tickers": [t.strip().upper() for t in tickers],
            "lookback_months": lookback_months,
            "hold_days": hold_days,
            "min_confidence": min_confidence,
            "strategy_stats": strategy_stats,
            "errors": errors,
        }
    )
    _calibration_lock.release()


def start_calibration(tickers: List[str], lookback_months: int = 6, hold_days: int = 5, min_confidence: int = 55) -> Dict[str, Any]:
    """Kicks off calibration in a background thread and returns immediately -
    a run across even a modest ticker list can take well past a typical
    gunicorn worker timeout if done inline on the request. Status/results are
    tracked in the calibration file itself so they're visible across restarts
    and (for the common single-worker deployment this app uses) across
    requests without needing shared in-memory state."""
    if not _calibration_lock.acquire(blocking=False):
        return {"status": "already_running", "message": "A calibration run is already in progress."}

    clean_tickers = [t.strip().upper() for t in tickers if t.strip()]
    if not clean_tickers:
        _calibration_lock.release()
        raise ValueError("At least one ticker is required.")

    write_calibration(
        {
            "status": "running",
            "generated_at": "",
            "tickers": clean_tickers,
            "lookback_months": lookback_months,
            "hold_days": hold_days,
            "min_confidence": min_confidence,
            "strategy_stats": {},
            "errors": [],
        }
    )
    thread = threading.Thread(
        target=_run_calibration_sync,
        args=(clean_tickers, lookback_months, hold_days, min_confidence),
        daemon=True,
    )
    thread.start()
    return {"status": "started", "ticker_count": len(clean_tickers)}
