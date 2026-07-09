from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _round(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 2)


def _safe_pct(numerator: float, denominator: float) -> float:
    if not denominator:
        return 0.0
    return (numerator / denominator) * 100


def _series_or_empty(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(dtype=float)
    return frame[column].astype(float).dropna()


def _insufficient_payload(ticker: str, reason: str) -> Dict[str, object]:
    return {
        "ticker": ticker,
        "status": "insufficient data",
        "insufficient_data": True,
        "reason": reason,
        "research_only": True,
        "generated_at": _now_iso(),
    }


def build_extended_hours_intelligence(ticker: str) -> Dict[str, object]:
    normalized = (ticker or "").strip().upper()
    if not normalized:
        raise ValueError("Ticker is required.")

    try:
        intraday = yf.download(
            tickers=normalized,
            period="2d",
            interval="5m",
            auto_adjust=False,
            progress=False,
            threads=False,
            prepost=True,
        )
        daily = yf.download(
            tickers=normalized,
            period="5d",
            interval="1d",
            auto_adjust=False,
            progress=False,
            threads=False,
        )
    except Exception as error:
        return _insufficient_payload(normalized, f"Market data fetch failed: {error}")

    if intraday.empty or daily.empty:
        return _insufficient_payload(normalized, "Extended-hours OHLCV data unavailable.")

    if isinstance(intraday.columns, pd.MultiIndex):
        if normalized in intraday.columns.get_level_values(0):
            intraday = intraday.xs(normalized, axis=1, level=0, drop_level=True)
        elif normalized in intraday.columns.get_level_values(-1):
            intraday = intraday.xs(normalized, axis=1, level=-1, drop_level=True)
    if isinstance(daily.columns, pd.MultiIndex):
        if normalized in daily.columns.get_level_values(0):
            daily = daily.xs(normalized, axis=1, level=0, drop_level=True)
        elif normalized in daily.columns.get_level_values(-1):
            daily = daily.xs(normalized, axis=1, level=-1, drop_level=True)

    if intraday.index.tz is None:
        intraday.index = intraday.index.tz_localize("UTC")
    et_index = intraday.index.tz_convert(ZoneInfo("America/New_York"))
    premarket_mask = et_index.time < datetime.strptime("09:30", "%H:%M").time()
    after_hours_mask = et_index.time >= datetime.strptime("16:00", "%H:%M").time()
    premarket = intraday.loc[premarket_mask]
    after_hours = intraday.loc[after_hours_mask]

    close_series = _series_or_empty(daily, "Close")
    prev_close = float(close_series.iloc[-2]) if len(close_series) >= 2 else float(close_series.iloc[-1])
    pre_close = _series_or_empty(premarket, "Close")
    pre_volume = _series_or_empty(premarket, "Volume")
    ah_close = _series_or_empty(after_hours, "Close")
    ah_volume = _series_or_empty(after_hours, "Volume")

    pre_price = float(pre_close.iloc[-1]) if not pre_close.empty else None
    ah_price = float(ah_close.iloc[-1]) if not ah_close.empty else None
    reference_price = pre_price if pre_price is not None else ah_price if ah_price is not None else prev_close
    gap_pct = _safe_pct(reference_price - prev_close, prev_close)
    gap_direction = "UP" if gap_pct > 0 else "DOWN" if gap_pct < 0 else "FLAT"

    pre_high = float(_series_or_empty(premarket, "High").max()) if not premarket.empty else None
    pre_low = float(_series_or_empty(premarket, "Low").min()) if not premarket.empty else None
    ah_high = float(_series_or_empty(after_hours, "High").max()) if not after_hours.empty else None
    ah_low = float(_series_or_empty(after_hours, "Low").min()) if not after_hours.empty else None

    pre_start = float(pre_close.iloc[0]) if len(pre_close) else None
    ah_start = float(ah_close.iloc[0]) if len(ah_close) else None
    pre_trend_pct = _safe_pct((pre_price or 0) - (pre_start or 0), pre_start or 0) if pre_start else 0.0
    ah_trend_pct = _safe_pct((ah_price or 0) - (ah_start or 0), ah_start or 0) if ah_start else 0.0
    combined_trend = pre_trend_pct if pre_close.size else ah_trend_pct

    avg_daily_volume = float(_series_or_empty(daily, "Volume").tail(20).mean()) if "Volume" in daily.columns else 0.0
    extended_volume = float(pre_volume.sum()) + float(ah_volume.sum())
    volume_ratio = (extended_volume / avg_daily_volume) if avg_daily_volume else 0.0
    volatility_pct = _safe_pct(((pre_high or ah_high or reference_price) - (pre_low or ah_low or reference_price)), reference_price)

    trend_label = "Bullish" if combined_trend > 0.5 else "Bearish" if combined_trend < -0.5 else "Neutral"
    strength_label = "Strong" if volume_ratio >= 0.35 else "Moderate" if volume_ratio >= 0.18 else "Weak"
    volatility_label = "High" if volatility_pct >= 2.2 else "Moderate" if volatility_pct >= 1.0 else "Low"

    explanations = [
        "Premarket high may become today's resistance.",
        "Premarket low may become today's support.",
        "Strong premarket volume increases continuation probability.",
        "Weak extended-hours volume increases fake-breakout risk.",
        "Large overnight gaps increase opening volatility.",
    ]

    return {
        "ticker": normalized,
        "status": "ok",
        "insufficient_data": False,
        "premarket_price": _round(pre_price),
        "premarket_percent": _round(_safe_pct((pre_price or prev_close) - prev_close, prev_close) if pre_price is not None else 0.0),
        "premarket_volume": int(pre_volume.sum()) if not pre_volume.empty else 0,
        "premarket_high": _round(pre_high),
        "premarket_low": _round(pre_low),
        "after_hours_price": _round(ah_price),
        "after_hours_percent": _round(_safe_pct((ah_price or prev_close) - prev_close, prev_close) if ah_price is not None else 0.0),
        "after_hours_volume": int(ah_volume.sum()) if not ah_volume.empty else 0,
        "after_hours_high": _round(ah_high),
        "after_hours_low": _round(ah_low),
        "gap_percent": _round(gap_pct),
        "gap_direction": gap_direction,
        "previous_close": _round(prev_close),
        "extended_hours_trend": trend_label,
        "extended_hours_strength": strength_label,
        "extended_hours_volatility": volatility_label,
        "extended_hours_trend_percent": _round(combined_trend),
        "extended_hours_volume_ratio": _round(volume_ratio),
        "explanations": explanations,
        "feeds": {
            "market_scanner": True,
            "upcoming_opportunities": True,
            "mission_queue": True,
            "trade_thesis": True,
            "options_suggestions": True,
            "strategy_brain": True,
            "charting_brain": True,
            "reversal_brain": True,
        },
        "research_only": True,
        "disclaimer": "For research only. Extended-hours analytics are not execution commands.",
        "generated_at": _now_iso(),
    }
