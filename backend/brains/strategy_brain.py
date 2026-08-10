from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Tuple

import pandas as pd
import yfinance as yf

from calibration_store import score_multiplier

SUPPORTED_STRATEGIES = [
    "Breakout",
    "Pullback",
    "Reversal",
    "VWAP Bounce",
    "Gap and Go",
    "Trend Continuation",
    "Support Bounce",
    "Resistance Rejection",
    "Momentum",
    "Mean Reversion",
    "Earnings Momentum",
    "Options Momentum",
]


@dataclass
class StrategyCandidate:
    name: str
    score: int
    recommendation: str
    timeframe: str
    risk_level: str
    expected_hold_time: str
    why_fit: str
    rejection_reason: str
    invalidation: str


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _insufficient_payload(ticker: str, reason: str) -> Dict[str, object]:
    return {
        "ticker": ticker.upper(),
        "status": "insufficient data",
        "insufficient_data": True,
        "reason": reason,
        "supported_strategies": SUPPORTED_STRATEGIES,
        "best_strategy": "WAIT",
        "strategy_confidence": 0,
        "recommendation": "WAIT",
        "ideal_timeframe": "unknown",
        "risk_level": "unknown",
        "expected_hold_time": "unknown",
        "why_this_strategy_fits": reason,
        "why_other_strategies_were_rejected": [],
        "why_ai_likes_it": reason,
        "risk_warning": "Data quality is not sufficient for recommendation.",
        "what_invalidates_trade": "Insufficient data invalidates all strategy outputs.",
        "why_support_matters": "Support identifies downside invalidation.",
        "why_resistance_matters": "Resistance identifies breakout acceptance/rejection.",
        "why_volume_matters": "Volume confirms or rejects move quality.",
        "why_trend_matters": "Trend helps avoid low-probability counter-trend setups.",
        "why_news_matters": "News can invalidate technical setups instantly.",
        "data_source": "Yahoo Finance (OHLCV)",
        "data_quality": "insufficient",
        "last_updated": _now_iso(),
        "live_or_delayed": "Delayed",
        "research_only": True,
        "disclaimer": "For research only. PlutoTrade AI does not auto-trade from strategy suggestions.",
        "generated_at": _now_iso(),
    }


def _fetch_ohlcv(ticker: str) -> Tuple[pd.DataFrame, pd.DataFrame]:
    normalized = ticker.strip().upper()
    if not normalized:
        raise ValueError("Ticker is required.")
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
    return daily, intraday


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


def _rsi(series: pd.Series, window: int = 14) -> float | None:
    if len(series) < window + 1:
        return None
    delta = series.diff()
    up = delta.clip(lower=0).rolling(window).mean()
    down = -delta.clip(upper=0).rolling(window).mean()
    rs = up / down.replace(0, pd.NA)
    value = (100 - (100 / (1 + rs))).iloc[-1]
    return None if pd.isna(value) else float(value)


def _fmt(value: float) -> str:
    return f"${value:.2f}"


def build_strategy_intelligence(
    ticker: str,
    extended_hours: Dict[str, object] | None = None,
    daily: pd.DataFrame | None = None,
    intraday: pd.DataFrame | None = None,
    backtest_mode: bool = False,
) -> Dict[str, object]:
    """backtest_mode lets a backtest engine pass in a pre-sliced `daily` frame
    (data available as of some historical date) and skip the intraday fetch -
    Yahoo only retains 5-minute intraday history for ~60 days, so it can't be
    reconstructed for an arbitrary past date. VWAP/gap signals that depend on
    live intraday data degrade to a neutral WAIT bias rather than being faked."""
    normalized = ticker.strip().upper()
    if not normalized:
        raise ValueError("Ticker is required.")
    extended_hours = extended_hours or {}

    if daily is None or intraday is None:
        try:
            fetched_daily, fetched_intraday = _fetch_ohlcv(normalized)
        except Exception as error:
            return _insufficient_payload(normalized, f"Market data fetch failed: {error}")
        daily = fetched_daily if daily is None else daily
        intraday = fetched_intraday if intraday is None else intraday

    daily = _normalize_ohlcv(daily, normalized)
    intraday = _normalize_ohlcv(intraday, normalized)

    if daily.empty or len(daily) < 80:
        return _insufficient_payload(normalized, "Not enough daily OHLCV candles. Need at least ~80 daily bars.")
    if not backtest_mode and (intraday.empty or len(intraday) < 40):
        return _insufficient_payload(normalized, "Not enough intraday OHLCV candles. Need intraday data.")

    closes = daily["Close"].astype(float)
    highs = daily["High"].astype(float)
    lows = daily["Low"].astype(float)
    opens = daily["Open"].astype(float)
    volumes = daily["Volume"].astype(float)
    if intraday.empty:
        vwap = float(closes.iloc[-1])
    else:
        intraday_close = intraday["Close"].astype(float)
        intraday_volume = intraday["Volume"].astype(float)
        typical_price = (intraday["High"].astype(float) + intraday["Low"].astype(float) + intraday_close) / 3
        vwap = float((typical_price * intraday_volume).cumsum().iloc[-1] / intraday_volume.cumsum().iloc[-1])

    current_price = float(closes.iloc[-1])
    previous_close = float(closes.iloc[-2])
    today_open = float(opens.iloc[-1])
    today_high = float(highs.iloc[-1])
    today_low = float(lows.iloc[-1])
    ema9 = float(closes.ewm(span=9, adjust=False).mean().iloc[-1])
    ema20 = float(closes.ewm(span=20, adjust=False).mean().iloc[-1])
    ema50 = float(closes.ewm(span=50, adjust=False).mean().iloc[-1])
    ema200 = float(closes.ewm(span=200, adjust=False).mean().iloc[-1]) if len(closes) >= 200 else ema50
    high20 = float(highs.tail(20).max())
    low20 = float(lows.tail(20).min())
    avg_volume20 = float(volumes.tail(20).mean())
    today_volume = float(volumes.iloc[-1])
    relative_volume = (today_volume / avg_volume20) if avg_volume20 else 0.0
    gap_pct = ((today_open - previous_close) / previous_close * 100) if previous_close else 0.0
    day_change_pct = ((current_price - previous_close) / previous_close * 100) if previous_close else 0.0
    intraday_change_pct = ((current_price - today_open) / today_open * 100) if today_open else 0.0
    rsi14 = _rsi(closes, 14)

    extended_gap = float(extended_hours.get("gap_percent", 0.0) or 0.0)
    extended_trend = str(extended_hours.get("extended_hours_trend", "Neutral"))
    extended_strength = str(extended_hours.get("extended_hours_strength", "Weak"))
    extended_volume_ratio = float(extended_hours.get("extended_hours_volume_ratio", 0.0) or 0.0)

    in_uptrend = ema9 > ema20 > ema50 and current_price > ema20
    in_downtrend = ema9 < ema20 < ema50 and current_price < ema20
    near_support = abs(current_price - low20) / current_price <= 0.015 if current_price else False
    near_resistance = abs(current_price - high20) / current_price <= 0.015 if current_price else False
    near_vwap = bool(not intraday.empty and current_price and abs(current_price - vwap) / current_price <= 0.006)

    candidates: List[StrategyCandidate] = [
        StrategyCandidate(
            "Breakout",
            min(99, int(38 + relative_volume * 13 + (12 if current_price >= high20 * 0.995 else 0))),
            "CALL" if current_price >= high20 * 0.995 else "WAIT",
            "5m-1h",
            "moderate" if relative_volume >= 1.4 else "elevated",
            "30m-1d",
            f"Price is pressing/clearing 20-day high {_fmt(high20)} with RVOL {relative_volume:.2f}x.",
            f"Rejected when breakout cannot hold above {_fmt(high20)}.",
            f"Close below {_fmt(ema20)} after breakout.",
        ),
        StrategyCandidate(
            "Pullback",
            min(99, int(30 + (20 if in_uptrend else 0) + (16 if abs(current_price - ema20) / current_price <= 0.01 else 0))),
            "CALL" if in_uptrend else "WAIT",
            "15m-4h",
            "moderate",
            "2h-3d",
            f"Uptrend remains intact and pullback is near EMA20 {_fmt(ema20)}.",
            f"Rejected when EMA20 slope weakens or closes below EMA50 {_fmt(ema50)}.",
            f"Close below {_fmt(ema50)}.",
        ),
        StrategyCandidate(
            "Reversal",
            min(99, int(24 + (18 if rsi14 is not None and rsi14 < 35 else 0) + (14 if day_change_pct < -2 else 0))),
            "CALL" if rsi14 is not None and rsi14 < 35 else "WAIT",
            "5m-30m",
            "high",
            "15m-1d",
            f"Potential exhaustion setup: RSI14={rsi14:.1f} with downside extension." if rsi14 is not None else "Potential exhaustion setup.",
            "Rejected without reclaim and higher-low confirmation.",
            f"Break below {_fmt(min(today_low, low20))}.",
        ),
        StrategyCandidate(
            "VWAP Bounce",
            min(99, int(24 + (18 if near_vwap else 0) + (12 if in_uptrend else 0))),
            "CALL" if near_vwap and in_uptrend else "WAIT",
            "1m-15m",
            "moderate",
            "15m-6h",
            f"Price/VWAP interaction near {_fmt(vwap)} supports a bounce setup.",
            "Rejected on repeated VWAP rejection with sell pressure.",
            f"Fails below VWAP support {_fmt(vwap * 0.995)}.",
        ),
        StrategyCandidate(
            "Gap and Go",
            min(99, int(20 + max(0, (gap_pct + extended_gap) * 8) + (12 if relative_volume >= 1.8 else 0))),
            "CALL" if gap_pct > 1 and intraday_change_pct > 0 else "WAIT",
            "1m-15m",
            "high",
            "15m-1d",
            f"Overnight/opening gap profile ({gap_pct:+.2f}% / extended {extended_gap:+.2f}%) with momentum follow-through.",
            "Rejected when opening range loses structure or volume fades.",
            f"Break of opening range low {_fmt(today_low)}.",
        ),
        StrategyCandidate(
            "Trend Continuation",
            min(99, int(32 + (18 if in_uptrend else 0) + max(0, int(day_change_pct * 4)))),
            "CALL" if in_uptrend else "WAIT",
            "15m-1d",
            "moderate" if in_uptrend else "elevated",
            "1d-5d",
            f"EMA stack supports continuation (EMA9 {_fmt(ema9)} > EMA20 {_fmt(ema20)} > EMA50 {_fmt(ema50)}).",
            "Rejected when trend structure breaks (EMA9 below EMA20).",
            f"Close below {_fmt(ema20)}.",
        ),
        StrategyCandidate(
            "Support Bounce",
            min(99, int(24 + (20 if near_support else 0))),
            "CALL" if near_support else "WAIT",
            "5m-1h",
            "moderate",
            "30m-2d",
            f"Price is testing major support near {_fmt(low20)}.",
            "Rejected when support breaks with heavy volume.",
            f"Close below {_fmt(low20)}.",
        ),
        StrategyCandidate(
            "Resistance Rejection",
            min(99, int(24 + (20 if near_resistance else 0) + (10 if intraday_change_pct < 0 else 0))),
            "PUT" if near_resistance and intraday_change_pct < 0 else "WAIT",
            "5m-30m",
            "moderate",
            "30m-1d",
            f"Price is near resistance {_fmt(high20)} and showing rejection risk.",
            "Rejected when resistance breaks and holds.",
            f"Break and hold above {_fmt(high20)}.",
        ),
        StrategyCandidate(
            "Momentum",
            min(99, int(26 + abs(day_change_pct) * 7 + relative_volume * 8 + (8 if extended_strength == "Strong" else 0))),
            "CALL" if day_change_pct > 1 else "PUT" if day_change_pct < -1 else "WAIT",
            "1m-1h",
            "high",
            "15m-1d",
            f"Momentum profile: {day_change_pct:+.2f}% day move, RVOL {relative_volume:.2f}x, extended trend {extended_trend}.",
            "Rejected in choppy low-volume sessions.",
            f"Momentum loss back through EMA20 {_fmt(ema20)}.",
        ),
        StrategyCandidate(
            "Mean Reversion",
            min(99, int(24 + (14 if rsi14 is not None and (rsi14 > 68 or rsi14 < 32) else 0))),
            "PUT" if rsi14 is not None and rsi14 > 68 else "CALL" if rsi14 is not None and rsi14 < 32 else "WAIT",
            "5m-1h",
            "high",
            "30m-2d",
            "Price appears stretched from mean and may revert.",
            "Rejected in high-conviction trend expansion.",
            f"Trend extension beyond {_fmt(high20 if current_price > ema20 else low20)}.",
        ),
        StrategyCandidate(
            "Earnings Momentum",
            min(99, int(18 + abs(gap_pct) * 9 + (12 if relative_volume >= 2.0 else 0))),
            "CALL" if gap_pct > 0.8 and relative_volume >= 1.8 else "PUT" if gap_pct < -0.8 and relative_volume >= 1.8 else "WAIT",
            "1m-30m",
            "high",
            "15m-1d",
            "Gap + volume profile resembles post-catalyst momentum behavior.",
            "Rejected when gap fades below VWAP/opening structure.",
            f"Break below opening low {_fmt(today_low)}.",
        ),
        StrategyCandidate(
            "Options Momentum",
            min(99, int(22 + abs(day_change_pct) * 6 + relative_volume * 9 + extended_volume_ratio * 12)),
            "CALL" if day_change_pct > 1 else "PUT" if day_change_pct < -1 else "WAIT",
            "5m-1h",
            "high",
            "1h-3d",
            f"Volatility + RVOL profile supports options momentum setup ({day_change_pct:+.2f}% / RVOL {relative_volume:.2f}x).",
            "Rejected in low-liquidity/choppy tape.",
            f"Loss of momentum back through EMA20 {_fmt(ema20)}.",
        ),
    ]

    # Nudge each candidate's hand-tuned score by how that named strategy has
    # actually performed in the walk-forward backtest (see calibration.py) -
    # a no-op until an admin runs calibration, and a no-op per-strategy until
    # enough trades have been recorded to trust the number.
    for candidate in candidates:
        candidate.score = max(0, min(99, round(candidate.score * score_multiplier(candidate.name))))

    ranked = sorted(candidates, key=lambda item: item.score, reverse=True)
    winner = ranked[0]
    rejected = [
        {"strategy": item.name, "reason": item.rejection_reason, "score": item.score}
        for item in ranked[1:]
    ]
    rationale = {
        "why_ai_likes_it": winner.why_fit,
        "risk_warning": f"Risk is {winner.risk_level}. Invalidates on: {winner.invalidation}",
        "what_invalidates_trade": winner.invalidation,
        "why_support_matters": f"Support near {_fmt(low20)} defines downside risk and stop planning.",
        "why_resistance_matters": f"Resistance near {_fmt(high20)} confirms acceptance or rejection.",
        "why_volume_matters": f"RVOL {relative_volume:.2f}x {'supports' if relative_volume >= 1 else 'does not support'} continuation quality.",
        "why_trend_matters": f"EMA structure {'aligns' if in_uptrend or in_downtrend else 'is mixed for'} directional probability.",
        "why_news_matters": "Catalysts can reprice options and invalidate technical setups quickly.",
    }

    return {
        "ticker": normalized,
        "status": "ok",
        "insufficient_data": False,
        "best_strategy": winner.name,
        "strategy_confidence": winner.score,
        "recommendation": winner.recommendation,
        "ideal_timeframe": winner.timeframe,
        "risk_level": winner.risk_level,
        "expected_hold_time": winner.expected_hold_time,
        "why_this_strategy_fits": winner.why_fit,
        "why_other_strategies_were_rejected": rejected,
        **rationale,
        "data_source": "Yahoo Finance (OHLCV + intraday pre/post)",
        "data_quality": "good" if relative_volume > 0 else "partial",
        "last_updated": _now_iso(),
        "live_or_delayed": "Delayed",
        "market_context": {
            "current_price": round(current_price, 2),
            "day_change_percent": round(day_change_pct, 2),
            "gap_percent": round(gap_pct, 2),
            "extended_gap_percent": round(extended_gap, 2),
            "extended_trend": extended_trend,
            "relative_volume": round(relative_volume, 2),
            "ema_9": round(ema9, 2),
            "ema_20": round(ema20, 2),
            "ema_50": round(ema50, 2),
            "ema_200": round(ema200, 2),
            "range_high_20d": round(high20, 2),
            "range_low_20d": round(low20, 2),
            "vwap": round(vwap, 2),
            "rsi_14": None if rsi14 is None else round(rsi14, 2),
        },
        "strategies_evaluated": [
            {
                "strategy": item.name,
                "score": item.score,
                "recommendation": item.recommendation,
                "timeframe": item.timeframe,
                "risk_level": item.risk_level,
            }
            for item in ranked
        ],
        "supported_strategies": SUPPORTED_STRATEGIES,
        "research_only": True,
        "disclaimer": "For research only. PlutoTrade AI does not auto-trade from strategy suggestions.",
        "generated_at": _now_iso(),
        # Backward-compatible aliases
        "recommended_strategy": winner.name,
        "best_timeframe": winner.timeframe,
        "bias": winner.recommendation,
        "when_to_enter": winner.why_fit,
        "when_to_avoid": "Rejected strategy reasons are included for alternatives.",
        "invalidation_rule": winner.invalidation,
    }

