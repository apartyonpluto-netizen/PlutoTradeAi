"""Walk-forward of one user strategy on daily bars.

Uses the same trade summary as the built-in backtest engine
(backtest_engine._summarize) and the same entry timing: a signal on the
close, a fill at the next session's open. Exits differ when the user set
a stop or target. Those exits are labeled. A fixed hold with no stop uses
the built-in engine's hold rule.

This never calls the live strategy brain and never submits an order.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from backtest_engine import MAX_LOOKBACK_MONTHS, _summarize

from .model import ENTRY_RULES, MAX_TICKERS

WARMUP_BARS = 80
BACKTEST_LABEL = "Backtest"


def _finite(summary: Dict[str, Any]) -> Dict[str, Any]:
    profit_factor = summary.get("profit_factor")
    if profit_factor == float("inf"):
        summary = {**summary, "profit_factor": None, "profit_factor_note": "No losing trades in this backtest."}
    return summary


def _day(value: Any) -> str:
    if hasattr(value, "date"):
        try:
            return value.date().isoformat()
        except (TypeError, ValueError):
            pass
    text = str(value)
    return text[:10]


def prepare_bars(frame: pd.DataFrame) -> pd.DataFrame:
    missing = [name for name in ("Open", "High", "Low", "Close", "Volume") if name not in frame.columns]
    if missing:
        raise ValueError("Daily bars are missing Open, High, Low, Close, or Volume.")
    cleaned = frame.loc[:, ["Open", "High", "Low", "Close", "Volume"]].apply(pd.to_numeric, errors="coerce")
    return cleaned.dropna(subset=["Open", "High", "Low", "Close"])


def _indicators(frame: pd.DataFrame) -> pd.DataFrame:
    close = frame["Close"].astype(float)
    high = frame["High"].astype(float)
    low = frame["Low"].astype(float)
    open_ = frame["Open"].astype(float)
    volume = frame["Volume"].astype(float)
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rsi = 100 - (100 / (1 + gain / loss.replace(0, pd.NA)))
    return pd.DataFrame(
        {
            "close": close,
            "open": open_,
            "high": high,
            "low": low,
            "rsi": rsi,
            "ema9": close.ewm(span=9, adjust=False).mean(),
            "ema20": close.ewm(span=20, adjust=False).mean(),
            "ema50": close.ewm(span=50, adjust=False).mean(),
            "high20": high.rolling(20).max(),
            "low20": low.rolling(20).min(),
            "prior_high20": high.shift(1).rolling(20).max(),
            "prior_low20": low.shift(1).rolling(20).min(),
            "relative_volume": volume / volume.rolling(20).mean().replace(0, pd.NA),
            "day_change": (close / close.shift(1) - 1) * 100,
            "gap": (open_ / close.shift(1) - 1) * 100,
        },
        index=frame.index,
    )


def _rule_true(row: pd.Series, rule: Dict[str, Any]) -> bool:
    kind = rule["kind"]
    value = rule.get("value")
    checks = {
        "rsi_below": lambda: row["rsi"] < value,
        "rsi_above": lambda: row["rsi"] > value,
        "price_above_ema20": lambda: row["close"] > row["ema20"],
        "price_below_ema20": lambda: row["close"] < row["ema20"],
        "price_above_ema50": lambda: row["close"] > row["ema50"],
        "price_below_ema50": lambda: row["close"] < row["ema50"],
        "ema_stack_up": lambda: row["ema9"] > row["ema20"] > row["ema50"],
        "ema_stack_down": lambda: row["ema9"] < row["ema20"] < row["ema50"],
        "near_20d_high": lambda: abs(row["close"] - row["high20"]) / row["close"] <= 0.015,
        "near_20d_low": lambda: abs(row["close"] - row["low20"]) / row["close"] <= 0.015,
        "breakout_20d": lambda: row["close"] > row["prior_high20"],
        "breakdown_20d": lambda: row["close"] < row["prior_low20"],
        "relative_volume_above": lambda: row["relative_volume"] > value,
        "day_change_above": lambda: row["day_change"] > value,
        "day_change_below": lambda: row["day_change"] < value,
        "gap_above": lambda: row["gap"] > value,
        "gap_below": lambda: row["gap"] < value,
    }
    check = checks.get(kind)
    if check is None or kind not in ENTRY_RULES:
        return False
    try:
        result = check()
    except (TypeError, ValueError, ZeroDivisionError):
        return False
    if result is None or (isinstance(result, float) and pd.isna(result)):
        return False
    return bool(result)


def signal_series(frame: pd.DataFrame, rules: List[Dict[str, Any]]) -> pd.Series:
    indicators = _indicators(frame)
    flags = []
    for _, row in indicators.iterrows():
        flags.append(all(_rule_true(row, rule) for rule in rules))
    return pd.Series(flags, index=frame.index)


def _prices(entry: float, direction: str, stop_percent: Optional[float], target: Dict[str, Any]) -> tuple:
    stop_price = None
    target_price = None
    if stop_percent:
        distance = entry * (float(stop_percent) / 100.0)
        stop_price = entry - distance if direction == "long" else entry + distance
    kind = (target or {}).get("type")
    if kind == "percent" and target.get("value"):
        distance = entry * (float(target["value"]) / 100.0)
        target_price = entry + distance if direction == "long" else entry - distance
    elif kind == "r_multiple" and stop_price is not None and target.get("value"):
        risk = abs(entry - stop_price)
        target_price = entry + risk * float(target["value"]) if direction == "long" else entry - risk * float(target["value"])
    return stop_price, target_price


def _hit(direction: str, stop_price: Optional[float], target_price: Optional[float], high: float, low: float, open_: float) -> Optional[tuple]:
    """Returns (exit_price, reason) or None. Stop is assumed first when both
    sit inside the same bar, matching the setup library's backtest."""
    if direction == "long":
        if stop_price is not None and open_ <= stop_price:
            return open_, "stop"
        stopped = stop_price is not None and low <= stop_price
        targeted = target_price is not None and high >= target_price
        if stopped:
            return stop_price, "stop"
        if targeted:
            return target_price, "target"
        return None
    if stop_price is not None and open_ >= stop_price:
        return open_, "stop"
    stopped = stop_price is not None and high >= stop_price
    targeted = target_price is not None and low <= target_price
    if stopped:
        return stop_price, "stop"
    if targeted:
        return target_price, "target"
    return None


def _pnl(direction: str, entry: float, exit_price: float) -> float:
    if direction == "long":
        return (exit_price - entry) / entry * 100
    return (entry - exit_price) / entry * 100


def _simulate(
    frame: pd.DataFrame,
    entry_index: int,
    direction: str,
    stop_percent: Optional[float],
    target: Dict[str, Any],
    max_hold_days: int,
) -> Optional[Dict[str, Any]]:
    entry = float(frame["Open"].iloc[entry_index])
    if entry <= 0:
        return None
    stop_price, target_price = _prices(entry, direction, stop_percent, target)
    deadline = entry_index + max_hold_days
    last = len(frame) - 1
    exit_index = min(deadline, last)
    reason = "hold"
    exit_price = float(frame["Close"].iloc[exit_index])
    still_open = deadline > last and stop_percent is None and (target or {}).get("type") in (None, "none")
    for index in range(entry_index, exit_index + 1):
        hit = _hit(
            direction,
            stop_price,
            target_price,
            float(frame["High"].iloc[index]),
            float(frame["Low"].iloc[index]),
            float(frame["Open"].iloc[index]),
        )
        if hit is not None:
            exit_price, reason = hit
            exit_index = index
            still_open = False
            break
        if index == deadline:
            exit_price = float(frame["Close"].iloc[index])
            reason = "hold"
            exit_index = index
            still_open = False
            break
    else:
        if deadline > last:
            reason = "still_open" if still_open or stop_percent or (target or {}).get("type") not in (None, "none") else "hold"
            still_open = True
            exit_price = float(frame["Close"].iloc[last])
            exit_index = last
    return {
        "entry_index": entry_index,
        "exit_index": exit_index,
        "entry_price": round(entry, 4),
        "exit_price": round(float(exit_price), 4),
        "pnl_percent": round(_pnl(direction, entry, float(exit_price)), 2),
        "exit_reason": reason,
        "still_open": still_open,
        "direction": direction,
    }


def run_on_frame(
    strategy: Dict[str, Any],
    frame: pd.DataFrame,
    *,
    ticker: str,
    lookback_months: int,
) -> Dict[str, Any]:
    rules = strategy.get("entry_rules") or []
    if not rules:
        return {"ticker": ticker, "error": "Add at least one entry rule before running a backtest.", "trades": []}
    direction = strategy.get("direction")
    if direction not in {"long", "short"}:
        return {
            "ticker": ticker,
            "error": "Set direction to long or short before a backtest. Both is kept as a description and is not simulated as two books.",
            "trades": [],
        }
    daily = prepare_bars(frame)
    needed = WARMUP_BARS + int(strategy.get("max_hold_days") or 5) + 2
    if len(daily) < needed:
        return {
            "ticker": ticker,
            "error": f"Not enough daily bars ({len(daily)}) for this backtest. {needed} are required.",
            "trades": [],
        }
    cutoff = daily.index[-1] - pd.Timedelta(days=lookback_months * 31)
    start = max(int(daily.index.searchsorted(cutoff)), WARMUP_BARS)
    signals = signal_series(daily, rules)
    trades: List[Dict[str, Any]] = []
    index = start
    last_signal = len(daily) - 1
    while index < last_signal:
        if not bool(signals.iloc[index]):
            index += 1
            continue
        simulated = _simulate(
            daily,
            index + 1,
            direction,
            strategy.get("stop_percent"),
            strategy.get("target") or {"type": "none"},
            int(strategy.get("max_hold_days") or 5),
        )
        if simulated is None:
            index += 1
            continue
        trades.append(
            {
                "ticker": ticker,
                "entry_date": _day(daily.index[simulated["entry_index"]]),
                "exit_date": _day(daily.index[simulated["exit_index"]]),
                "entry_price": simulated["entry_price"],
                "exit_price": simulated["exit_price"],
                "pnl_percent": simulated["pnl_percent"],
                "exit_reason": simulated["exit_reason"],
                "still_open": simulated["still_open"],
                "direction": direction,
                "strategy": strategy.get("name"),
            }
        )
        index = simulated["exit_index"]
    return {"ticker": ticker, "error": "", "trades": trades, **_finite(_summarize(trades))}


def methodology_note(strategy: Dict[str, Any]) -> str:
    target = (strategy.get("target") or {}).get("type")
    if strategy.get("stop_percent") or target not in (None, "none"):
        exit_rule = (
            "Exits use the stop and target on daily highs and lows. "
            "If both are inside the same bar, the stop is filled first. "
            "A gap through the stop fills at the open."
        )
    else:
        exit_rule = (
            "No stop or target is set, so the exit is a fixed hold, the same rule as the built-in backtest engine. "
            "The live scan exits with a stop and a target, which this run does not."
        )
    instrument = ""
    if strategy.get("instruments") in {"options", "both"}:
        instrument = " Option prices are not simulated; the bars are the underlying stock."
    timeframe = ""
    if strategy.get("timeframe") == "intraday":
        timeframe = " The strategy is marked intraday, but this run only has daily bars, so it does not reconstruct intraday entries or VWAP."
    return (
        f"{BACKTEST_LABEL}. Signals use only bars available that day. "
        "The fill is the next session's open. "
        + exit_rule
        + instrument
        + timeframe
        + " A backtest is not a probability and it is not a closed trade."
    )


def run_strategy_backtest(
    strategy: Dict[str, Any],
    frames: Dict[str, pd.DataFrame],
    *,
    lookback_months: int = 6,
) -> Dict[str, Any]:
    lookback_months = max(1, min(int(lookback_months), MAX_LOOKBACK_MONTHS))
    tickers = [ticker for ticker in frames if ticker][:MAX_TICKERS]
    if not tickers:
        raise ValueError("Add at least one ticker before running a backtest.")
    per_ticker = [
        run_on_frame(strategy, frames[ticker], ticker=ticker, lookback_months=lookback_months) for ticker in tickers
    ]
    trades = [trade for result in per_ticker for trade in result["trades"]]
    trades.sort(key=lambda trade: (trade["entry_date"], trade["ticker"]))
    errors = [f"{result['ticker']}: {result['error']}" for result in per_ticker if result.get("error")]
    return {
        "label": BACKTEST_LABEL,
        "is_backtest": True,
        "probability": None,
        "probability_note": (
            "A backtest is not a probability. Probability is shown only from "
            "30 or more reconciled closed trades of this strategy name."
        ),
        "methodology_note": methodology_note(strategy),
        "tickers": tickers,
        "lookback_months": lookback_months,
        "hold_days": int(strategy.get("max_hold_days") or 5),
        "per_ticker": [{key: value for key, value in result.items() if key != "trades"} | {"trade_count": len(result["trades"])} for result in per_ticker],
        "combined": _finite(_summarize(trades)),
        "trades": trades[:300],
        "trade_count_truncated": len(trades) > 300,
        "errors": errors,
    }


def load_daily_bars(ticker: str, lookback_months: int) -> pd.DataFrame:
    """Network fetch used only when a person runs a backtest. Tests pass bars in instead."""
    from backtest_engine import _fetch_daily_history, _normalize_ohlcv

    raw = _fetch_daily_history(ticker, lookback_months)
    return _normalize_ohlcv(raw, ticker)
