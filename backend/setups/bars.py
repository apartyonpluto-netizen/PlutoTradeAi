"""OHLCV bars and causal indicators. Every indicator value at bar i uses only
bars 0..i, so computing on a prefix gives exactly the prefix of the full
computation - the basis of the engine's no-future-data guarantee."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time as dtime
from typing import Any, Callable, Dict, Iterable, List

import numpy as np

try:
    from zoneinfo import ZoneInfo

    _NEW_YORK = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover - tzdata missing
    _NEW_YORK = None

INTRADAY_TIMEFRAMES = ("1m", "5m", "10m", "15m", "30m", "1h")
REGULAR_OPEN = dtime(9, 30)
REGULAR_CLOSE = dtime(16, 0)


@dataclass
class Bars:
    symbol: str
    timeframe: str
    t: List[str]
    o: Any
    h: Any
    l: Any
    c: Any
    v: Any
    _memo: Dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self) -> None:
        self.symbol = str(self.symbol).upper()
        self.t = [str(value) for value in self.t]
        for name in ("o", "h", "l", "c", "v"):
            array = np.asarray(getattr(self, name), dtype=float)
            if len(array) != len(self.t):
                raise ValueError(f"{name} has {len(array)} values for {len(self.t)} timestamps")
            setattr(self, name, array)

    def __len__(self) -> int:
        return len(self.t)

    @property
    def intraday(self) -> bool:
        return self.timeframe in INTRADAY_TIMEFRAMES

    def upto(self, index: int) -> "Bars":
        """Bars 0..index inclusive - what was known at the close of bar `index`."""
        end = max(0, min(int(index) + 1, len(self)))
        return Bars(self.symbol, self.timeframe, self.t[:end], self.o[:end], self.h[:end], self.l[:end], self.c[:end], self.v[:end])

    def memo(self, key: str, compute: Callable[[], Any]) -> Any:
        if key not in self._memo:
            self._memo[key] = compute()
        return self._memo[key]

    @classmethod
    def from_rows(cls, symbol: str, timeframe: str, rows: Iterable[Dict[str, Any]]) -> "Bars":
        rows = list(rows)
        return cls(
            symbol, timeframe, [row["t"] for row in rows],
            [row["o"] for row in rows], [row["h"] for row in rows], [row["l"] for row in rows],
            [row["c"] for row in rows], [row.get("v", 0) or 0 for row in rows],
        )

    @classmethod
    def from_frame(cls, symbol: str, timeframe: str, frame: Any) -> "Bars":
        """From the Open/High/Low/Close/Volume DataFrame alpaca_data returns."""
        frame = frame.dropna(subset=["Open", "High", "Low", "Close"])
        return cls(
            symbol, timeframe, [ts.isoformat() for ts in frame.index],
            frame["Open"].to_numpy(float), frame["High"].to_numpy(float), frame["Low"].to_numpy(float),
            frame["Close"].to_numpy(float), frame["Volume"].fillna(0).to_numpy(float),
        )

    def to_rows(self) -> List[Dict[str, Any]]:
        return [
            {"t": self.t[i], "o": float(self.o[i]), "h": float(self.h[i]), "l": float(self.l[i]), "c": float(self.c[i]), "v": float(self.v[i])}
            for i in range(len(self))
        ]


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def true_range(bars: Bars) -> np.ndarray:
    if not len(bars):
        return np.array([])
    previous_close = np.concatenate(([bars.c[0]], bars.c[:-1]))
    return np.maximum(bars.h - bars.l, np.maximum(np.abs(bars.h - previous_close), np.abs(bars.l - previous_close)))


def atr(bars: Bars, period: int = 14) -> np.ndarray:
    """Wilder ATR; the first `period` values are an expanding mean. Floored at
    0.01% of price so flat data never yields a zero tolerance."""
    def compute() -> np.ndarray:
        ranges = true_range(bars)
        out = np.empty(len(ranges))
        running = 0.0
        for i, value in enumerate(ranges):
            if i < period:
                running += value
                out[i] = running / (i + 1)
            else:
                out[i] = (out[i - 1] * (period - 1) + value) / period
        return np.maximum(out, np.maximum(1e-6, np.abs(bars.c) * 1e-4))
    return bars.memo(f"atr{period}", compute)


def sma(values: np.ndarray, period: int) -> np.ndarray:
    out = np.full(len(values), np.nan)
    if len(values) >= period:
        cumulative = np.cumsum(np.insert(np.asarray(values, dtype=float), 0, 0.0))
        out[period - 1:] = (cumulative[period:] - cumulative[:-period]) / period
    return out


def ema(values: np.ndarray, period: int) -> np.ndarray:
    out = np.empty(len(values))
    alpha = 2.0 / (period + 1)
    for i, value in enumerate(values):
        out[i] = value if i == 0 else alpha * value + (1 - alpha) * out[i - 1]
    return out


def rsi(values: np.ndarray, period: int) -> np.ndarray:
    """Wilder RSI; NaN until `period` changes exist."""
    out = np.full(len(values), np.nan)
    if len(values) <= period:
        return out
    deltas = np.diff(np.asarray(values, dtype=float))
    gains = np.clip(deltas, 0, None)
    losses = np.clip(-deltas, 0, None)
    average_gain = gains[:period].mean()
    average_loss = losses[:period].mean()

    def value(gain: float, loss: float) -> float:
        if loss == 0:
            return 100.0 if gain > 0 else 50.0
        return 100.0 - 100.0 / (1.0 + gain / loss)

    out[period] = value(average_gain, average_loss)
    for i in range(period + 1, len(values)):
        average_gain = (average_gain * (period - 1) + gains[i - 1]) / period
        average_loss = (average_loss * (period - 1) + losses[i - 1]) / period
        out[i] = value(average_gain, average_loss)
    return out


def prior_average_volume(bars: Bars, period: int = 20, minimum: int = 5) -> np.ndarray:
    """Mean volume of up to `period` bars BEFORE bar i (never including i)."""
    def compute() -> np.ndarray:
        out = np.full(len(bars), np.nan)
        cumulative = np.cumsum(np.insert(bars.v, 0, 0.0))
        for i in range(1, len(bars)):
            count = min(period, i)
            if count >= minimum:
                out[i] = (cumulative[i] - cumulative[i - count]) / count
        return out
    return bars.memo(f"prior_volume{period}", compute)


def session_keys(bars: Bars) -> List[str]:
    """Trading-session date (New York) of each bar."""
    def compute() -> List[str]:
        keys = []
        for stamp in bars.t:
            moment = parse_time(stamp)
            if _NEW_YORK is not None and moment.tzinfo is not None:
                moment = moment.astimezone(_NEW_YORK)
            keys.append(moment.date().isoformat())
        return keys
    return bars.memo("session_keys", compute)


def regular_session_mask(bars: Bars) -> np.ndarray:
    """True for intraday bars that start inside regular hours (09:30-16:00 ET)."""
    def compute() -> np.ndarray:
        mask = np.zeros(len(bars), dtype=bool)
        for i, stamp in enumerate(bars.t):
            moment = parse_time(stamp)
            if _NEW_YORK is not None and moment.tzinfo is not None:
                moment = moment.astimezone(_NEW_YORK)
            mask[i] = REGULAR_OPEN <= moment.time() < REGULAR_CLOSE
        return mask
    return bars.memo("regular_mask", compute)


def session_vwap(bars: Bars) -> np.ndarray:
    """Regular-session VWAP, reset each session; NaN outside regular hours."""
    def compute() -> np.ndarray:
        out = np.full(len(bars), np.nan)
        keys = session_keys(bars)
        mask = regular_session_mask(bars)
        typical = (bars.h + bars.l + bars.c) / 3.0
        current_key = None
        price_volume = volume = 0.0
        for i in range(len(bars)):
            if not mask[i]:
                continue
            if keys[i] != current_key:
                current_key, price_volume, volume = keys[i], 0.0, 0.0
            price_volume += typical[i] * bars.v[i]
            volume += bars.v[i]
            out[i] = price_volume / volume if volume > 0 else typical[i]
        return out
    return bars.memo("vwap", compute)
