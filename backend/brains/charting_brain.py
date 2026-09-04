from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Sequence
from zoneinfo import ZoneInfo

import pandas as pd

from integrations import alpaca_data


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _insufficient_payload(ticker: str, reason: str) -> Dict[str, object]:
    return {
        "ticker": ticker.upper(),
        "status": "insufficient data",
        "insufficient_data": True,
        "reason": reason,
        "research_only": True,
        "disclaimer": "For research only. Do not auto-trade from AI chart levels.",
        "generated_at": _now_iso(),
    }


def _round_level(value: float) -> float:
    if value >= 1000:
        return round(value, 1)
    if value >= 100:
        return round(value, 2)
    if value >= 10:
        return round(value, 3)
    return round(value, 4)


def _cluster_levels(levels: Sequence[float], tolerance_pct: float = 0.003) -> List[float]:
    sorted_levels = sorted(float(level) for level in levels if level and level > 0)
    if not sorted_levels:
        return []
    clusters: List[List[float]] = [[sorted_levels[0]]]
    for level in sorted_levels[1:]:
        cluster_mid = sum(clusters[-1]) / len(clusters[-1])
        tolerance = cluster_mid * tolerance_pct
        if abs(level - cluster_mid) <= tolerance:
            clusters[-1].append(level)
        else:
            clusters.append([level])
    return [_round_level(sum(cluster) / len(cluster)) for cluster in clusters if cluster]


def _extract_pivot_levels(series: pd.Series, window: int = 3, mode: str = "low") -> List[float]:
    values = series.astype(float)
    pivots: List[float] = []
    if len(values) < (window * 2 + 1):
        return pivots
    for idx in range(window, len(values) - window):
        segment = values.iloc[idx - window : idx + window + 1]
        center = float(values.iloc[idx])
        if mode == "low" and center <= float(segment.min()):
            pivots.append(center)
        if mode == "high" and center >= float(segment.max()):
            pivots.append(center)
    return pivots


def _extract_field(dataframe: pd.DataFrame, ticker: str, field_name: str) -> pd.Series:
    if dataframe.empty:
        return pd.Series(dtype=float)
    if not isinstance(dataframe.columns, pd.MultiIndex):
        return dataframe[field_name].astype(float) if field_name in dataframe.columns else pd.Series(dtype=float)
    if (ticker, field_name) in dataframe.columns:
        return dataframe[(ticker, field_name)].astype(float)
    if (field_name, ticker) in dataframe.columns:
        return dataframe[(field_name, ticker)].astype(float)
    for column in dataframe.columns:
        if isinstance(column, tuple) and field_name in column:
            return dataframe[column].astype(float)
    return pd.Series(dtype=float)


def _normalize_ohlcv(dataframe: pd.DataFrame, ticker: str) -> pd.DataFrame:
    columns = ["Open", "High", "Low", "Close", "Volume"]
    extracted = {name: _extract_field(dataframe, ticker, name) for name in columns}
    normalized = pd.DataFrame(extracted, index=dataframe.index)
    return normalized.dropna(subset=["Close"])


def _pick_nearest_above(levels: Sequence[float], reference: float) -> float | None:
    candidates = sorted(level for level in levels if level > reference)
    return _round_level(candidates[0]) if candidates else None


def _pick_nearest_below(levels: Sequence[float], reference: float) -> float | None:
    candidates = sorted((level for level in levels if level < reference), reverse=True)
    return _round_level(candidates[0]) if candidates else None


def _zone(center: float | None, width_pct: float = 0.004) -> Dict[str, float] | None:
    if center is None:
        return None
    return {
        "low": _round_level(center * (1 - width_pct)),
        "high": _round_level(center * (1 + width_pct)),
        "center": _round_level(center),
    }


# Trend/consolidation flag detection (2026-09-04) - a direct port of
# analytics.py's detect_early_trends, moved onto Alpaca daily bars instead
# of Yahoo (analytics.py's own fetch_price_history uses yf.Ticker(...) -
# the same source charting_brain.py itself already migrated away from, per
# the migration comment on build_chart_levels below). This is a PORT, not a
# refactor: analytics.py's own detect_early_trends is untouched and still
# used by its existing callers - the logic below is reproduced against the
# same `daily` OHLCV frame build_chart_levels already fetched, so this adds
# zero extra API calls.


def _is_strictly_rising(series: pd.Series) -> bool:
    values = series.dropna().tolist()
    return len(values) >= 3 and all(values[index] > values[index - 1] for index in range(1, len(values)))


def _is_strictly_falling(series: pd.Series) -> bool:
    values = series.dropna().tolist()
    return len(values) >= 3 and all(values[index] < values[index - 1] for index in range(1, len(values)))


def _moving_average(series: pd.Series, window: int) -> float:
    dropped = series.dropna()
    if len(dropped) < window:
        return float(dropped.mean()) if not dropped.empty else 0.0
    return float(dropped.tail(window).mean())


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


def detect_trend_flags(daily: pd.DataFrame, support: float, resistance: float) -> Dict[str, object]:
    """Alpaca-sourced trend/consolidation classification - see the module
    comment above for why this is a port, not new analysis. `support`/
    `resistance` are meant to be the NEAREST actionable levels to current
    price (build_chart_levels passes breakdown_level/breakout_level, the
    same role analytics.py's build_reversal_map's simple support/resistance
    played for the original), not the full major/minor level lists.
    Surfaced on the dashboard as plain trend labels (e.g. "higher highs,
    higher lows - uptrend intact"), not literal drawn chart shapes - see
    static/js/app.js's bindMarketOverviewChart for how these booleans get
    turned into that label text."""
    if daily.empty:
        return {}

    recent = daily.tail(40)
    if len(recent) < 2:
        return {}
    latest = recent.iloc[-1]
    prev = recent.iloc[-2]
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
    breakout_forming = higher_lows and latest_close < resistance and resistance > 0 and (resistance - latest_close) / resistance <= 0.015
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
    # Consolidation: neither trending up nor down, and volume has dried up -
    # no equivalent flag existed in the original analytics.py version, but
    # this is exactly what the user asked to see called out.
    consolidating = not (higher_highs or lower_lows or trend_continuation) and volume_compression

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
        "consolidating": consolidating,
        "spinning_top": body / range_size <= 0.25 and upper_wick > body and lower_wick > body,
    }


def build_chart_levels(ticker: str, extended_hours: Dict[str, object] | None = None) -> Dict[str, object]:
    normalized = ticker.strip().upper()
    if not normalized:
        raise ValueError("Ticker is required.")
    extended_hours = extended_hours or {}

    try:
        # Alpaca Market Data API, not yfinance - see
        # integrations/alpaca_data.py's own module docstring for why (Yahoo
        # Finance actively rate-limiting this app's requests, found live
        # 2026-08-28) and exactly what this migration covers.
        daily = alpaca_data.get_bars_single(normalized, period="9mo", interval="1d")
        intraday = alpaca_data.get_bars_single(normalized, period="5d", interval="5m")
    except Exception as error:
        return _insufficient_payload(normalized, f"Market data fetch failed: {error}")
    daily = _normalize_ohlcv(daily, normalized)
    intraday = _normalize_ohlcv(intraday, normalized)

    if daily.empty or len(daily) < 80:
        return _insufficient_payload(normalized, "Not enough daily OHLCV candles for chart-level detection.")
    if intraday.empty or len(intraday) < 40:
        return _insufficient_payload(normalized, "Not enough intraday OHLCV candles for VWAP/premarket analysis.")

    highs = daily["High"].astype(float)
    lows = daily["Low"].astype(float)
    closes = daily["Close"].astype(float)
    current_price = float(closes.iloc[-1])
    ema9 = float(closes.ewm(span=9, adjust=False).mean().iloc[-1])
    ema20 = float(closes.ewm(span=20, adjust=False).mean().iloc[-1])
    ema50 = float(closes.ewm(span=50, adjust=False).mean().iloc[-1])
    ema200 = float(closes.ewm(span=200, adjust=False).mean().iloc[-1]) if len(closes) >= 200 else ema50

    major_support = _cluster_levels(_extract_pivot_levels(lows, window=4, mode="low")[-12:], 0.004)[-4:]
    major_resistance = _cluster_levels(_extract_pivot_levels(highs, window=4, mode="high")[-12:], 0.004)[-4:]
    minor_support = [x for x in _cluster_levels(_extract_pivot_levels(lows, window=2, mode="low")[-18:], 0.003) if x not in major_support][
        -4:
    ]
    minor_resistance = [
        x for x in _cluster_levels(_extract_pivot_levels(highs, window=2, mode="high")[-18:], 0.003) if x not in major_resistance
    ][-4:]

    if not major_support:
        major_support = [_round_level(float(lows.tail(20).min()))]
    if not major_resistance:
        major_resistance = [_round_level(float(highs.tail(20).max()))]

    breakout_level = _pick_nearest_above(major_resistance + minor_resistance, current_price) or max(major_resistance)
    breakdown_level = _pick_nearest_below(major_support + minor_support, current_price) or min(major_support)
    trend_flags = detect_trend_flags(daily, support=float(breakdown_level), resistance=float(breakout_level))

    if intraday.index.tz is None:
        intraday.index = intraday.index.tz_localize("UTC")
    et_index = intraday.index.tz_convert(ZoneInfo("America/New_York"))
    premarket_mask = et_index.time < datetime.strptime("09:30", "%H:%M").time()
    after_hours_mask = et_index.time >= datetime.strptime("16:00", "%H:%M").time()
    premarket = intraday.loc[premarket_mask]
    after_hours = intraday.loc[after_hours_mask]

    premarket_high = _round_level(float(premarket["High"].max())) if not premarket.empty else extended_hours.get("premarket_high")
    premarket_low = _round_level(float(premarket["Low"].min())) if not premarket.empty else extended_hours.get("premarket_low")
    after_hours_high = (
        _round_level(float(after_hours["High"].max())) if not after_hours.empty else extended_hours.get("after_hours_high")
    )
    after_hours_low = _round_level(float(after_hours["Low"].min())) if not after_hours.empty else extended_hours.get("after_hours_low")

    intraday_close = intraday["Close"].astype(float)
    intraday_volume = intraday["Volume"].astype(float)
    typical_price = (intraday["High"].astype(float) + intraday["Low"].astype(float) + intraday_close) / 3
    vwap = float((typical_price * intraday_volume).cumsum().iloc[-1] / intraday_volume.cumsum().iloc[-1])

    previous_day = daily.iloc[-2]
    previous_high = _round_level(float(previous_day["High"]))
    previous_low = _round_level(float(previous_day["Low"]))
    reversal_zone = {"low": _round_level(min(ema20, ema50)), "high": _round_level(max(ema20, ema50))}
    invalidation_level = f"Below {_round_level(float(breakdown_level))} / Above {_round_level(float(breakout_level))}"

    trendline_points: List[Dict[str, object]] = []
    for idx, level in enumerate(_extract_pivot_levels(lows.tail(90), window=3, mode="low")[-3:], start=1):
        trendline_points.append({"type": "support_trendline", "point": idx, "price": _round_level(level)})
    for idx, level in enumerate(_extract_pivot_levels(highs.tail(90), window=3, mode="high")[-3:], start=1):
        trendline_points.append({"type": "resistance_trendline", "point": idx, "price": _round_level(level)})

    ai_chart_marks = [
        {"label": "Mark Support", "value": _round_level(float(breakdown_level))},
        {"label": "Mark Resistance", "value": _round_level(float(breakout_level))},
        {"label": "Watch Breakout", "value": _round_level(float(breakout_level))},
        {"label": "Watch Breakdown", "value": _round_level(float(breakdown_level))},
        {"label": "Reversal Zone", "value": f"{reversal_zone['low']}–{reversal_zone['high']}"},
    ]

    return {
        "ticker": normalized,
        "status": "ok",
        "insufficient_data": False,
        "major_support_levels": major_support,
        "major_resistance_levels": major_resistance,
        "minor_support_levels": minor_support,
        "minor_resistance_levels": minor_resistance,
        "supply_zones": [_zone(level, width_pct=0.0045) for level in major_resistance[-2:]],
        "demand_zones": [_zone(level, width_pct=0.0045) for level in major_support[-2:]],
        "trendline_points": trendline_points,
        "ema_9": _round_level(ema9),
        "ema_20": _round_level(ema20),
        "ema_50": _round_level(ema50),
        "ema_200": _round_level(ema200),
        "vwap": _round_level(vwap),
        "premarket_high": premarket_high,
        "premarket_low": premarket_low,
        "after_hours_high": after_hours_high,
        "after_hours_low": after_hours_low,
        "liquidity_sweep_zones": {
            "above_previous_high": _zone(previous_high, width_pct=0.002),
            "below_previous_low": _zone(previous_low, width_pct=0.002),
        },
        "breakout_level": _round_level(float(breakout_level)),
        "breakdown_level": _round_level(float(breakdown_level)),
        "reversal_zone": reversal_zone,
        "invalidation_level": invalidation_level,
        "trend_flags": trend_flags,
        "ai_chart_marks": ai_chart_marks,
        "levels_to_manually_mark": ai_chart_marks,
        "research_only": True,
        "disclaimer": "For research only. Do not auto-trade from AI chart levels.",
        "generated_at": _now_iso(),
    }

