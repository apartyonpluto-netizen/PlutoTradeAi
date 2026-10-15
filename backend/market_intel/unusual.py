"""Unusual volume and price versus a trailing daily baseline from Alpaca.

The latest bar is compared with the previous 20 completed sessions.
A quiet tape is a present neutral vote. A failed or unconfigured Alpaca
read is a missing vote, not a guess.
"""

from __future__ import annotations

from typing import Any, Dict

from market_intel.support import cached, missing, observation

SIGNAL = "unusual_activity"
BASELINE_BARS = 20
VOLUME_SPIKE = 2.0
PRICE_FLOOR = 0.03
PRICE_SIGMAS = 2.0
FLAT_RETURN = 0.002


def signal(ticker: str) -> Dict[str, Any]:
    symbol = str(ticker or "").strip().upper()
    if not symbol:
        return missing(SIGNAL, "invalid_ticker")
    try:
        from integrations import alpaca_data
    except ImportError:  # pragma: no cover
        from backend.integrations import alpaca_data  # type: ignore
    if not alpaca_data.is_configured():
        return missing(SIGNAL, "alpaca_not_configured", "Alpaca market data is not configured")

    def produce() -> Dict[str, Any]:
        frame = alpaca_data.get_bars_single(symbol, period="3mo", interval="1d")
        return from_bars(frame)

    try:
        return cached(f"unusual:v1:{symbol}", 10 * 60, produce)
    except Exception:
        return missing(SIGNAL, "source_unavailable")


def from_bars(frame: Any) -> Dict[str, Any]:
    if frame is None or getattr(frame, "empty", True):
        return missing(SIGNAL, "insufficient_history", "no daily bars")
    if "Close" not in frame.columns or "Volume" not in frame.columns:
        return missing(SIGNAL, "insufficient_history", "bars are missing close or volume")
    usable = frame.dropna(subset=["Close", "Volume"])
    if len(usable) < BASELINE_BARS + 1:
        return missing(SIGNAL, "insufficient_history", "not enough daily bars for a trailing baseline")
    baseline = usable.iloc[-(BASELINE_BARS + 1):-1]
    latest = usable.iloc[-1]
    median_volume = float(baseline["Volume"].median())
    latest_volume = float(latest["Volume"])
    previous_close = float(baseline["Close"].iloc[-1])
    latest_close = float(latest["Close"])
    if previous_close <= 0 or median_volume < 0:
        return missing(SIGNAL, "insufficient_history", "baseline price is unusable")
    day_return = (latest_close / previous_close) - 1.0
    returns = baseline["Close"].pct_change().dropna()
    sigma = float(returns.std(ddof=0)) if len(returns) else 0.0
    if sigma != sigma:
        sigma = 0.0
    volume_ratio = (latest_volume / median_volume) if median_volume else 0.0
    price_bar = max(PRICE_FLOOR, PRICE_SIGMAS * sigma)
    volume_spike = median_volume > 0 and volume_ratio >= VOLUME_SPIKE
    price_spike = abs(day_return) >= price_bar
    if day_return >= FLAT_RETURN:
        direction = "bullish"
    elif day_return <= -FLAT_RETURN:
        direction = "bearish"
    else:
        direction = "neutral"
    if direction == "neutral" or not (volume_spike or price_spike):
        return observation(
            SIGNAL,
            direction="neutral",
            magnitude=0,
            detail=(
                f"volume {volume_ratio:.1f}x the {BASELINE_BARS}-day median and "
                f"price {day_return:+.1%} are inside the trailing baseline"
            ),
        )
    magnitude = 3 if volume_spike and price_spike else 2
    kind = "volume and price" if volume_spike and price_spike else "volume" if volume_spike else "price"
    return observation(
        SIGNAL,
        direction=direction,
        magnitude=magnitude,
        detail=f"unusual {kind}: volume {volume_ratio:.1f}x baseline, price {day_return:+.1%}",
    )
