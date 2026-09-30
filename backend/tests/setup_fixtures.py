"""Synthetic bars for setup-detector tests: piecewise-linear closes through
anchor prices, small fixed bar ranges, controllable volume."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional, Sequence, Tuple

from setups.bars import Bars


def path(anchors: Sequence[Tuple[int, float]]) -> List[float]:
    """anchors: (bars_to_reach, price) legs, starting from the first price."""
    closes = [float(anchors[0][1])]
    for bars, price in anchors[1:]:
        start = closes[-1]
        for k in range(1, bars + 1):
            closes.append(start + (price - start) * k / bars)
    return closes


def make_bars(closes: Sequence[float], *, symbol: str = "TEST", timeframe: str = "1d", spread: float = 0.004,
              volumes: Optional[Sequence[float]] = None, start: Optional[datetime] = None, step: Optional[timedelta] = None) -> Bars:
    start = start or datetime(2025, 1, 2, 21, 0, tzinfo=timezone.utc)
    step = step or timedelta(days=1)
    rows = []
    previous = closes[0]
    for i, close in enumerate(closes):
        open_ = previous
        high = max(open_, close) * (1 + spread / 2)
        low = min(open_, close) * (1 - spread / 2)
        rows.append({"t": (start + step * i).isoformat(), "o": open_, "h": high, "l": low, "c": close,
                     "v": float(volumes[i]) if volumes is not None else 1_000_000.0})
        previous = close
    return Bars.from_rows(symbol, timeframe, rows)
