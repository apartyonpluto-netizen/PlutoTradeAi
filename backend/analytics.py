from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Sequence, Tuple

import pandas as pd
import yfinance as yf


def _safe_price(value: float) -> float:
    return round(float(value), 2)


def _safe_zone(low: float, high: float) -> str:
    return f"${_safe_price(low):.2f} - ${_safe_price(high):.2f}"


def _is_strictly_rising(series: pd.Series) -> bool:
    values = series.dropna().tolist()
    return len(values) >= 3 and all(values[index] > values[index - 1] for index in range(1, len(values)))


def _is_strictly_falling(series: pd.Series) -> bool:
    values = series.dropna().tolist()
    return len(values) >= 3 and all(values[index] < values[index - 1] for index in range(1, len(values)))


def _moving_average(series: pd.Series, window: int) -> float:
    if len(series.dropna()) < window:
        return float(series.dropna().mean()) if not series.dropna().empty else 0.0
    return float(series.tail(window).mean())


def _candle_reversal_signal(history: pd.DataFrame, support: float, resistance: float) -> bool:
    if len(history) < 2:
        return False

    latest = history.iloc[-1]
    previous = history.iloc[-2]
    body = abs(float(latest["Close"]) - float(latest["Open"]))
    range_size = max(0.0001, float(latest["High"]) - float(latest["Low"]))
    lower_wick = min(float(latest["Open"]), float(latest["Close"])) - float(latest["Low"])
    upper_wick = float(latest["High"]) - max(float(latest["Open"]), float(latest["Close"]))

    bullish_engulfing = (
        float(previous["Close"]) < float(previous["Open"])
        and float(latest["Close"]) > float(latest["Open"])
        and float(latest["Open"]) < float(previous["Close"])
        and float(latest["Close"]) > float(previous["Open"])
    )
    bearish_engulfing = (
        float(previous["Close"]) > float(previous["Open"])
        and float(latest["Close"]) < float(latest["Open"])
        and float(latest["Open"]) > float(previous["Close"])
        and float(latest["Close"]) < float(previous["Open"])
    )

    hammer_like = lower_wick > body * 1.8 and lower_wick > upper_wick * 1.2 and body / range_size < 0.5
    rejection_like = upper_wick > body * 1.8 and upper_wick > lower_wick * 1.2 and body / range_size < 0.5

    near_support = abs(float(latest["Close"]) - support) / support <= 0.015 if support else False
    near_resistance = abs(float(latest["Close"]) - resistance) / resistance <= 0.015 if resistance else False

    return (near_support and (bullish_engulfing or hammer_like)) or (
        near_resistance and (bearish_engulfing or rejection_like)
    )


def fetch_price_history(ticker: str, period: str = "3mo", interval: str = "1d") -> pd.DataFrame:
    history = yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=False)
    return history[["Open", "High", "Low", "Close", "Volume"]].dropna(how="all") if not history.empty else history


def _explain_setup(
    ticker: str,
    current: float,
    support: float,
    resistance: float,
    breakdown: float,
    breakout: float,
) -> str:
    return (
        f"Watch {current:.2f} on {ticker}. If support near {support:.2f} holds, bullish reversal probability improves. "
        f"Break below {breakdown:.2f} invalidates this setup. Break and hold above {breakout:.2f} confirms momentum continuation toward resistance near {resistance:.2f}."
    )


def build_reversal_map(ticker: str, history: pd.DataFrame, current_price: float | None = None) -> Dict[str, str]:
    if history.empty:
        raise ValueError(f"No historical data for {ticker}.")

    closes = history["Close"].dropna()
    highs = history["High"].dropna()
    lows = history["Low"].dropna()

    current = float(current_price) if current_price else float(closes.iloc[-1])
    support = float(lows.tail(20).min())
    resistance = float(highs.tail(20).max())
    breakout = resistance * 1.005
    breakdown = support * 0.995

    reversal_low = support * 1.0
    reversal_high = support * 1.02
    suggested_entry = support * 1.01
    suggested_stop = support * 0.987
    target_low = resistance * 1.01
    target_high = resistance * 1.05
    invalidation = breakdown * 0.998
    risk_pct = ((suggested_entry - suggested_stop) / suggested_entry * 100) if suggested_entry else 0

    return {
        "ticker": ticker.upper(),
        "current_price": _safe_price(current),
        "support": _safe_price(support),
        "resistance": _safe_price(resistance),
        "breakout_price": _safe_price(breakout),
        "breakdown_price": _safe_price(breakdown),
        "reversal_zone": _safe_zone(reversal_low, reversal_high),
        "target_zone": _safe_zone(target_low, target_high),
        "suggested_entry": _safe_price(suggested_entry),
        "suggested_stop": _safe_price(suggested_stop),
        "invalidation_level": _safe_price(invalidation),
        "risk_level": "Elevated" if risk_pct > 2.5 else "Moderate" if risk_pct > 1.5 else "Controlled",
        "setup_explanation": _explain_setup(
            ticker=ticker.upper(),
            current=current,
            support=support,
            resistance=resistance,
            breakdown=breakdown,
            breakout=breakout,
        ),
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }


def detect_early_trends(history: pd.DataFrame, support: float, resistance: float) -> Dict[str, bool]:
    if history.empty:
        raise ValueError("No history to evaluate trends.")

    recent = history.tail(40)
    latest = recent.iloc[-1]
    prev = recent.iloc[-2] if len(recent) > 1 else latest
    avg_volume = float(recent["Volume"].tail(20).mean()) if "Volume" in recent else 0.0
    latest_volume = float(latest["Volume"]) if "Volume" in recent else 0.0

    volume_expansion = avg_volume > 0 and latest_volume >= avg_volume * 1.5
    volume_compression = avg_volume > 0 and latest_volume <= avg_volume * 0.7
    unusual_volume = avg_volume > 0 and latest_volume >= avg_volume * 2.2

    higher_lows = _is_strictly_rising(recent["Low"].tail(4))
    lower_highs = _is_strictly_falling(recent["High"].tail(4))
    higher_highs = _is_strictly_rising(recent["High"].tail(4))
    lower_lows = _is_strictly_falling(recent["Low"].tail(4))

    latest_close = float(latest["Close"])
    prev_close = float(prev["Close"])
    latest_open = float(latest["Open"])
    latest_high = float(latest["High"])
    latest_low = float(latest["Low"])

    trend_continuation = higher_highs and higher_lows and latest_close > prev_close
    trend_reversal = (higher_lows and latest_close > latest_open and prev_close < latest_open) or (
        lower_highs and latest_close < latest_open and prev_close > latest_open
    )
    breakout_forming = higher_lows and latest_close < resistance and (resistance - latest_close) / resistance <= 0.015
    failed_breakout = prev_close > resistance and latest_close < resistance
    failed_breakdown = prev_close < support and latest_close > support
    candle_reversal = _candle_reversal_signal(recent, support=support, resistance=resistance)

    body = abs(latest_close - latest_open)
    range_size = max(0.0001, latest_high - latest_low)
    upper_wick = latest_high - max(latest_open, latest_close)
    lower_wick = min(latest_open, latest_close) - latest_low

    bull_flag = trend_continuation and volume_compression and latest_close > _moving_average(recent["Close"], 20)
    bear_flag = lower_lows and volume_compression and latest_close < _moving_average(recent["Close"], 20)
    gap_up = latest_low > float(prev["High"])
    gap_down = latest_high < float(prev["Low"])
    institutional_buying = volume_expansion and latest_close > latest_open and latest_close >= latest_high - (range_size * 0.2)
    sector_momentum = latest_close > _moving_average(recent["Close"], 8) > _moving_average(recent["Close"], 21)
    relative_strength = latest_close > _moving_average(recent["Close"], 14)

    return {
        "volume_expansion": volume_expansion,
        "volume_compression": volume_compression,
        "higher_highs": higher_highs,
        "higher_lows": higher_lows,
        "lower_highs": lower_highs,
        "lower_lows": lower_lows,
        "bull_flag": bull_flag,
        "bear_flag": bear_flag,
        "failed_breakout": failed_breakout,
        "failed_breakdown": failed_breakdown,
        "trend_continuation": trend_continuation,
        "trend_reversal": trend_reversal or candle_reversal,
        "institutional_buying": institutional_buying,
        "sector_momentum": sector_momentum,
        "relative_strength": relative_strength,
        "gap_up": gap_up,
        "gap_down": gap_down,
        "unusual_volume": unusual_volume,
        "breakout_forming": breakout_forming,
        "candle_reversal_near_support_resistance": candle_reversal,
        "spinning_top": body / range_size <= 0.25 and upper_wick > body and lower_wick > body,
        "latest_close": _safe_price(latest_close),
        "latest_high": _safe_price(latest_high),
        "latest_low": _safe_price(latest_low),
    }


def build_reversal_and_trend_payload(
    tickers: Sequence[str], scanner_rows: Sequence[Dict[str, str]] | None = None
) -> Tuple[List[Dict[str, str]], List[Dict[str, str]], List[str]]:
    scanner_price_lookup = {
        row["ticker"].upper(): float(row["price"]) for row in (scanner_rows or []) if row.get("ticker")
    }
    reversal_map_rows: List[Dict[str, str]] = []
    trend_rows: List[Dict[str, str]] = []
    errors: List[str] = []

    for ticker in [ticker.upper().strip() for ticker in tickers if ticker]:
        try:
            history = fetch_price_history(ticker=ticker, period="3mo", interval="1d")
            reversal_row = build_reversal_map(
                ticker=ticker,
                history=history,
                current_price=scanner_price_lookup.get(ticker),
            )
            trend_signals = detect_early_trends(
                history=history,
                support=float(reversal_row["support"]),
                resistance=float(reversal_row["resistance"]),
            )

            reversal_map_rows.append(reversal_row)
            trend_rows.append({"ticker": ticker, **trend_signals, "last_updated": reversal_row["last_updated"]})
        except Exception as error:
            errors.append(f"{ticker}: {error}")

    return reversal_map_rows, trend_rows, errors
