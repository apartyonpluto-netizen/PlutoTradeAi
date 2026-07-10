from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Sequence
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf


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


def build_chart_levels(ticker: str, extended_hours: Dict[str, object] | None = None) -> Dict[str, object]:
    normalized = ticker.strip().upper()
    if not normalized:
        raise ValueError("Ticker is required.")
    extended_hours = extended_hours or {}

    try:
        daily = yf.download(
            tickers=normalized,
            period="9mo",
            interval="1d",
            auto_adjust=False,
            progress=False,
            threads=False,
        )
        intraday = yf.download(
            tickers=normalized,
            period="5d",
            interval="5m",
            auto_adjust=False,
            progress=False,
            threads=False,
            prepost=True,
        )
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
        "ai_chart_marks": ai_chart_marks,
        "levels_to_manually_mark": ai_chart_marks,
        "research_only": True,
        "disclaimer": "For research only. Do not auto-trade from AI chart levels.",
        "generated_at": _now_iso(),
    }

