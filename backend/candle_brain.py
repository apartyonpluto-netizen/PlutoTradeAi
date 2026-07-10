from __future__ import annotations

from typing import Dict, List

import yfinance as yf


CANDLE_PATTERNS = [
    "Hammer",
    "Doji",
    "Morning Star",
    "Evening Star",
    "Bullish Engulfing",
    "Bearish Engulfing",
    "Harami",
    "Piercing Pattern",
    "Dark Cloud Cover",
    "Three White Soldiers",
    "Three Black Crows",
    "Shooting Star",
    "Hanging Man",
    "Spinning Top",
]


def _confidence(is_match: bool, base: float = 0.72) -> float:
    return round(base if is_match else 0.12, 2)


def analyze_candles(ticker: str) -> Dict[str, object]:
    history = yf.Ticker(ticker).history(period="2mo", interval="1d", auto_adjust=False)
    if history.empty or len(history) < 5:
        raise ValueError(f"Not enough data for {ticker}.")

    recent = history.tail(5).copy()
    latest = recent.iloc[-1]
    prev = recent.iloc[-2]

    open_price = float(latest["Open"])
    close_price = float(latest["Close"])
    high = float(latest["High"])
    low = float(latest["Low"])
    prev_open = float(prev["Open"])
    prev_close = float(prev["Close"])

    body = abs(close_price - open_price)
    range_size = max(0.0001, high - low)
    upper_wick = high - max(open_price, close_price)
    lower_wick = min(open_price, close_price) - low
    doji_like = body / range_size < 0.12

    bullish_engulf = (
        prev_close < prev_open and close_price > open_price and open_price < prev_close and close_price > prev_open
    )
    bearish_engulf = (
        prev_close > prev_open and close_price < open_price and open_price > prev_close and close_price < prev_open
    )
    hammer = lower_wick > (body * 1.8) and upper_wick < body * 0.8
    shooting_star = upper_wick > (body * 1.8) and lower_wick < body * 0.8

    up_closes = recent["Close"].diff().fillna(0) > 0
    down_closes = recent["Close"].diff().fillna(0) < 0

    detections = {
        "Hammer": _confidence(hammer, 0.78),
        "Doji": _confidence(doji_like, 0.75),
        "Morning Star": _confidence(bool(down_closes.iloc[1] and doji_like and close_price > prev_open), 0.69),
        "Evening Star": _confidence(bool(up_closes.iloc[1] and doji_like and close_price < prev_open), 0.69),
        "Bullish Engulfing": _confidence(bullish_engulf, 0.82),
        "Bearish Engulfing": _confidence(bearish_engulf, 0.82),
        "Harami": _confidence(abs(close_price - open_price) < abs(prev_close - prev_open), 0.66),
        "Piercing Pattern": _confidence(
            bool(prev_close < prev_open and close_price > ((prev_open + prev_close) / 2) and close_price < prev_open),
            0.68,
        ),
        "Dark Cloud Cover": _confidence(
            bool(prev_close > prev_open and close_price < ((prev_open + prev_close) / 2) and close_price > prev_open),
            0.68,
        ),
        "Three White Soldiers": _confidence(bool((recent["Close"].tail(3).diff().dropna() > 0).all()), 0.67),
        "Three Black Crows": _confidence(bool((recent["Close"].tail(3).diff().dropna() < 0).all()), 0.67),
        "Shooting Star": _confidence(shooting_star, 0.77),
        "Hanging Man": _confidence(hammer and close_price < open_price, 0.71),
        "Spinning Top": _confidence(body / range_size <= 0.25 and upper_wick > body and lower_wick > body, 0.73),
    }

    ranked: List[Dict[str, object]] = [
        {"pattern": name, "confidence": score} for name, score in detections.items() if name in CANDLE_PATTERNS
    ]
    ranked.sort(key=lambda item: float(item["confidence"]), reverse=True)
    return {"ticker": ticker.upper(), "patterns": ranked, "last_updated": recent.index[-1].isoformat()}