from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import pandas as pd
import yfinance as yf

try:
    from .brains.strategy_brain import _normalize_ohlcv, build_strategy_intelligence
except ImportError:
    from brains.strategy_brain import _normalize_ohlcv, build_strategy_intelligence

MIN_WARMUP_BARS = 80  # matches strategy_brain's own "not enough daily candles" floor
MAX_LOOKBACK_MONTHS = 24
MAX_HOLD_DAYS = 20
MAX_TICKERS_PER_RUN = 5


def _fetch_daily_history(ticker: str, lookback_months: int) -> pd.DataFrame:
    end = datetime.now(timezone.utc).date()
    # Buffer beyond the requested window so the first backtest day already has
    # a full EMA200/RSI warmup window behind it, not just MIN_WARMUP_BARS.
    start = end - timedelta(days=lookback_months * 31 + 320)
    raw = yf.download(
        tickers=ticker.upper(),
        start=start.isoformat(),
        end=(end + timedelta(days=1)).isoformat(),
        interval="1d",
        auto_adjust=False,
        progress=False,
        threads=False,
    )
    return raw


def _summarize(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not trades:
        return {
            "trade_count": 0,
            "win_count": 0,
            "loss_count": 0,
            "win_rate_percent": 0.0,
            "avg_return_percent": 0.0,
            "total_return_percent": 0.0,
            "profit_factor": 0.0,
            "max_drawdown_percent": 0.0,
            "equity_curve": [],
        }

    wins = [t for t in trades if t["pnl_percent"] > 0]
    losses = [t for t in trades if t["pnl_percent"] <= 0]
    gross_profit = sum(t["pnl_percent"] for t in wins)
    gross_loss = abs(sum(t["pnl_percent"] for t in losses))

    equity_curve: List[float] = []
    running = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for trade in trades:
        running += trade["pnl_percent"]
        peak = max(peak, running)
        max_drawdown = max(max_drawdown, peak - running)
        equity_curve.append(round(running, 2))

    return {
        "trade_count": len(trades),
        "win_count": len(wins),
        "loss_count": len(losses),
        "win_rate_percent": round(len(wins) / len(trades) * 100, 1),
        "avg_return_percent": round(sum(t["pnl_percent"] for t in trades) / len(trades), 2),
        "total_return_percent": round(sum(t["pnl_percent"] for t in trades), 2),
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss else (float("inf") if gross_profit > 0 else 0.0),
        "max_drawdown_percent": round(max_drawdown, 2),
        "equity_curve": equity_curve,
    }


def run_ticker_backtest(
    ticker: str,
    lookback_months: int = 6,
    hold_days: int = 5,
    min_confidence: int = 55,
) -> Dict[str, Any]:
    """Walks forward day-by-day over historical daily bars, recomputing the
    live strategy's confidence/recommendation using only data available as of
    that day (backtest_mode=True - intraday/VWAP-dependent candidates degrade
    to WAIT since Yahoo doesn't retain 5-minute history beyond ~60 days, so
    they can't be reconstructed for older dates). Opens a simulated position
    the next trading day whenever the same CALL + confidence threshold the
    live autonomous scan uses would have fired, holds for a fixed number of
    trading days, and records the outcome."""
    normalized = ticker.strip().upper()
    raw = _fetch_daily_history(normalized, lookback_months)
    daily = _normalize_ohlcv(raw, normalized)

    minimum_bars_needed = MIN_WARMUP_BARS + hold_days + 5
    if daily.empty or len(daily) < minimum_bars_needed:
        return {
            "ticker": normalized,
            "error": f"Not enough historical daily data ({len(daily)} bars) for a {lookback_months}-month backtest.",
            "trades": [],
            **_summarize([]),
        }

    cutoff_date = daily.index[-1] - pd.Timedelta(days=lookback_months * 31)
    start_i = max(int(daily.index.searchsorted(cutoff_date)), MIN_WARMUP_BARS)

    empty_intraday = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
    trades: List[Dict[str, Any]] = []
    open_trade: Dict[str, Any] | None = None
    n = len(daily)

    for i in range(start_i, n):
        if open_trade is not None:
            days_held = i - open_trade["entry_index"]
            if days_held >= hold_days:
                exit_price = float(daily["Close"].iloc[i])
                pnl_percent = (exit_price - open_trade["entry_price"]) / open_trade["entry_price"] * 100
                trades.append(
                    {
                        **{k: v for k, v in open_trade.items() if k != "entry_index"},
                        "exit_date": daily.index[i].date().isoformat(),
                        "exit_price": round(exit_price, 2),
                        "pnl_percent": round(pnl_percent, 2),
                        "still_open": False,
                    }
                )
                open_trade = None
            continue

        if i >= n - 1:
            break  # no next trading day left to enter a new position on

        sliced = daily.iloc[: i + 1]
        result = build_strategy_intelligence(normalized, daily=sliced, intraday=empty_intraday, backtest_mode=True)
        if result.get("insufficient_data"):
            continue
        if result.get("recommendation") != "CALL" or int(result.get("strategy_confidence", 0)) < min_confidence:
            continue

        entry_index = i + 1
        open_trade = {
            "ticker": normalized,
            "entry_date": daily.index[entry_index].date().isoformat(),
            "entry_price": round(float(daily["Open"].iloc[entry_index]), 2),
            "entry_index": entry_index,
            "confidence": int(result["strategy_confidence"]),
            "strategy": result["best_strategy"],
        }

    if open_trade is not None:
        exit_price = float(daily["Close"].iloc[-1])
        pnl_percent = (exit_price - open_trade["entry_price"]) / open_trade["entry_price"] * 100
        trades.append(
            {
                **{k: v for k, v in open_trade.items() if k != "entry_index"},
                "exit_date": daily.index[-1].date().isoformat(),
                "exit_price": round(exit_price, 2),
                "pnl_percent": round(pnl_percent, 2),
                "still_open": True,
            }
        )

    return {"ticker": normalized, "error": "", "trades": trades, **_summarize(trades)}


def run_backtest(
    tickers: List[str],
    lookback_months: int = 6,
    hold_days: int = 5,
    min_confidence: int = 55,
) -> Dict[str, Any]:
    lookback_months = max(1, min(int(lookback_months), MAX_LOOKBACK_MONTHS))
    hold_days = max(1, min(int(hold_days), MAX_HOLD_DAYS))
    min_confidence = max(0, min(int(min_confidence), 99))
    clean_tickers = [t.strip().upper() for t in tickers if t.strip()][:MAX_TICKERS_PER_RUN]
    if not clean_tickers:
        raise ValueError("At least one ticker is required.")

    per_ticker = [run_ticker_backtest(ticker, lookback_months, hold_days, min_confidence) for ticker in clean_tickers]
    all_trades = [trade for result in per_ticker for trade in result["trades"]]
    all_trades.sort(key=lambda trade: trade["entry_date"])

    return {
        "tickers": clean_tickers,
        "lookback_months": lookback_months,
        "hold_days": hold_days,
        "min_confidence": min_confidence,
        "per_ticker": per_ticker,
        "combined": _summarize(all_trades),
        "trades": all_trades,
        "methodology_note": (
            "Daily-bar strategies only (Breakout, Pullback, Reversal, Trend Continuation, "
            "Support Bounce, Resistance Rejection, Mean Reversion, Momentum) - VWAP/gap/extended-hours "
            "signals are excluded because Yahoo Finance doesn't retain 5-minute intraday history "
            "beyond ~60 days, so they can't be reconstructed for older dates. Entries fill at the next "
            "trading day's open after a signal fires; exits are a fixed hold period, not a stop/target."
        ),
    }
