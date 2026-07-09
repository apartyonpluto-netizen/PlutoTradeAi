from __future__ import annotations

from typing import Dict, List

import yfinance as yf


PATTERN_SET = [
    "Bull Flag",
    "Bear Flag",
    "Cup and Handle",
    "Head & Shoulders",
    "Inverse Head & Shoulders",
    "Double Top",
    "Double Bottom",
    "Ascending Triangle",
    "Descending Triangle",
    "Pennant",
    "Rectangle",
    "Channel",
    "Wedge",
]


def _confidence(is_match: bool, strong: float = 0.8, weak: float = 0.18) -> float:
    return round(strong if is_match else weak, 2)


def analyze_patterns(ticker: str) -> Dict[str, object]:
    history = yf.Ticker(ticker).history(period="6mo", interval="1d", auto_adjust=False)
    if history.empty or len(history) < 30:
        raise ValueError(f"Not enough data for {ticker}.")

    recent = history.tail(40).copy()
    closes = recent["Close"]
    highs = recent["High"]
    lows = recent["Low"]
    avg_close = float(closes.mean())

    upper = float(highs.tail(20).max())
    lower = float(lows.tail(20).min())
    spread_ratio = (upper - lower) / avg_close if avg_close else 0
    slope = float(closes.tail(10).iloc[-1] - closes.tail(10).iloc[0]) / max(1.0, avg_close)

    rising_lows = lows.tail(5).is_monotonic_increasing
    falling_highs = highs.tail(5).is_monotonic_decreasing
    rising_highs = highs.tail(5).is_monotonic_increasing
    falling_lows = lows.tail(5).is_monotonic_decreasing

    detections = {
        "Bull Flag": _confidence(slope > 0.04 and rising_lows and falling_highs, 0.74),
        "Bear Flag": _confidence(slope < -0.04 and falling_lows and rising_highs, 0.74),
        "Cup and Handle": _confidence(spread_ratio > 0.12 and slope > 0.03, 0.62),
        "Head & Shoulders": _confidence(falling_highs and spread_ratio > 0.09, 0.58),
        "Inverse Head & Shoulders": _confidence(rising_lows and spread_ratio > 0.09, 0.58),
        "Double Top": _confidence(abs(float(highs.tail(2).iloc[0] - highs.tail(2).iloc[1])) / max(1.0, upper) < 0.01, 0.67),
        "Double Bottom": _confidence(abs(float(lows.tail(2).iloc[0] - lows.tail(2).iloc[1])) / max(1.0, lower) < 0.01, 0.67),
        "Ascending Triangle": _confidence(rising_lows and abs(float(highs.tail(5).max() - highs.tail(5).min())) / max(1.0, upper) < 0.012, 0.7),
        "Descending Triangle": _confidence(falling_highs and abs(float(lows.tail(5).max() - lows.tail(5).min())) / max(1.0, lower) < 0.012, 0.7),
        "Pennant": _confidence(spread_ratio < 0.06 and (rising_lows or falling_highs), 0.55),
        "Rectangle": _confidence(spread_ratio < 0.05, 0.63),
        "Channel": _confidence(rising_lows and rising_highs or (falling_lows and falling_highs), 0.64),
        "Wedge": _confidence(rising_lows and falling_highs, 0.6),
    }

    ranked: List[Dict[str, object]] = [{"pattern": name, "confidence": score} for name, score in detections.items()]
    ranked.sort(key=lambda item: float(item["confidence"]), reverse=True)
    return {"ticker": ticker.upper(), "patterns": ranked, "last_updated": recent.index[-1].isoformat()}
