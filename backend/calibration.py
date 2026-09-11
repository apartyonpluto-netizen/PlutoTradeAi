from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

if __package__:
    from .auth import list_all_users
    from .autonomy.closed_trades import list_closed_trades
    from .backtest_engine import run_ticker_backtest
    from .calibration_store import (
        MIN_TRADES_TO_TRUST,
        get_calibration,
        record_closed_trade_for_recalibration_trigger,
        score_multiplier,
        write_calibration,
    )
else:
    from auth import list_all_users
    from autonomy.closed_trades import list_closed_trades
    from backtest_engine import run_ticker_backtest
    from calibration_store import (
        MIN_TRADES_TO_TRUST,
        get_calibration,
        record_closed_trade_for_recalibration_trigger,
        score_multiplier,
        write_calibration,
    )

__all__ = [
    "get_calibration",
    "score_multiplier",
    "start_calibration",
    "recalibrate_from_real_outcomes",
    "maybe_trigger_real_outcomes_recalibration",
]

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

    # Merges onto whatever's already there (real_strategy_stats,
    # real_outcomes_generated_at, closed_trades_since_last_real_recalibration
    # - see recalibrate_from_real_outcomes/record_closed_trade_for_
    # recalibration_trigger below) rather than overwriting the whole file -
    # a backtest run must never silently wipe out real-outcomes data that
    # lives in the same shared calibration file.
    current = get_calibration()
    current.update(
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
    write_calibration(current)
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

    current = get_calibration()  # see _run_calibration_sync's own comment on why this merges, not overwrites
    current.update(
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
    write_calibration(current)
    thread = threading.Thread(
        target=_run_calibration_sync,
        args=(clean_tickers, lookback_months, hold_days, min_confidence),
        daemon=True,
    )
    thread.start()
    return {"status": "started", "ticker_count": len(clean_tickers)}


def _pnl_percent_for_closed_trade(trade: Dict[str, Any]) -> Optional[float]:
    """Real closed trades store a dollar net_realized_pnl, not a percent
    return (unlike backtest_engine's simulated trades, which compute
    pnl_percent directly) - derived here from cost basis (average entry
    price x exited/filled quantity) so it's comparable to the backtested
    avg_return_percent score_multiplier already knows how to use. Returns
    None (excluded from the average, not counted as 0%) for any trade
    missing the fields needed to compute this or not yet fully reconciled
    (pnl_status != "complete") - matches performance_report.py's own
    incomplete_pnl_count treatment of the same data."""
    if trade.get("pnl_status") != "complete":
        return None
    net_pnl = trade.get("net_realized_pnl")
    entry_price = trade.get("average_entry_price")
    quantity = trade.get("exited_quantity") or trade.get("filled_quantity")
    if net_pnl is None or not entry_price or not quantity:
        return None
    cost_basis = float(entry_price) * float(quantity)
    if cost_basis <= 0:
        return None
    return net_pnl / cost_basis * 100.0


def recalibrate_from_real_outcomes() -> Dict[str, Any]:
    """Computes a per-strategy real-outcomes multiplier input from THIS
    DEPLOYMENT'S OWN closed trades, across every user - matching this
    module's existing single-shared-calibration architecture (score_multiplier
    has always applied the same backtest-derived multiplier to every
    account; this extends that same shared scope to real data rather than
    introducing a new per-account split as a side effect of this change).
    Cheap (local JSON reads only, no network calls) - unlike backtest
    calibration, this runs synchronously; there is no need for the
    background-thread/lock machinery start_calibration uses for a
    multi-minute yfinance-bound run.

    Merges real_strategy_stats onto the existing calibration file (via
    get_calibration/write_calibration) rather than replacing it, so a
    concurrent or later backtest run doesn't erase this, and vice versa -
    see _run_calibration_sync's own comment for the same discipline
    applied there."""
    per_strategy: Dict[str, List[float]] = {}
    for user in list_all_users():
        for trade in list_closed_trades(user["id"]):
            strategy_name = trade.get("strategy")
            if not strategy_name:
                continue
            pnl_percent = _pnl_percent_for_closed_trade(trade)
            if pnl_percent is None:
                continue
            per_strategy.setdefault(strategy_name, []).append(pnl_percent)

    real_strategy_stats: Dict[str, Dict[str, Any]] = {}
    for strategy_name, returns in per_strategy.items():
        wins = [r for r in returns if r > 0]
        real_strategy_stats[strategy_name] = {
            "trade_count": len(returns),
            "win_count": len(wins),
            "win_rate_percent": round(len(wins) / len(returns) * 100, 1),
            "avg_return_percent": round(sum(returns) / len(returns), 2),
            "trusted": len(returns) >= MIN_TRADES_TO_TRUST,
        }

    current = get_calibration()
    current["real_strategy_stats"] = real_strategy_stats
    current["real_outcomes_generated_at"] = datetime.now(timezone.utc).isoformat()
    write_calibration(current)
    return {"real_strategy_stats": real_strategy_stats}


def maybe_trigger_real_outcomes_recalibration() -> None:
    """Call once after recording ANY newly-closed trade, for any user (see
    app.py's closed-trade recording sites). No-ops unless the running
    trade-count interval was just reached (see calibration_store.
    record_closed_trade_for_recalibration_trigger's own docstring on the
    counter/locking discipline) - and never lets a recalibration failure
    raise into the caller, which is always in the middle of real
    trade-close/reconciliation work when this runs. Matches
    research_log.py's own "never lets a logging failure affect the real
    scan" discipline for the same reason."""
    try:
        due = record_closed_trade_for_recalibration_trigger()
        if due:
            recalibrate_from_real_outcomes()
    except Exception:  # noqa: BLE001 - see docstring: must never affect the real trade-close path
        pass
