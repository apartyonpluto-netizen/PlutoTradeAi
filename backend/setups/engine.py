"""Runs registered detectors over bars as of one bar.

No-future-data guarantee: scan_bars(bars, as_of=t) slices to bars[:t+1]
BEFORE anything is computed, and every indicator and pivot is causal. A
detector literally cannot see a later bar."""

from __future__ import annotations

import logging
import math
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple, Union

import numpy as np

from . import model
from .bars import Bars, atr, ema, prior_average_volume, rsi, session_vwap, sma
from .pivots import Pivot, sequence, zigzag

logger = logging.getLogger(__name__)

# Swing sensitivity per timeframe, in ATR multiples: "minor" swings feed
# structure/range/triangle detectors, "major" swings feed reversal formations.
PROFILES = {
    "1d": {"minor": 1.5, "major": 2.5},
    "1h": {"minor": 1.5, "major": 2.5},
    "30m": {"minor": 2.0, "major": 3.0},
    "15m": {"minor": 2.0, "major": 3.0},
    "10m": {"minor": 2.0, "major": 3.0},
    "5m": {"minor": 2.0, "major": 3.0},
    "1m": {"minor": 2.5, "major": 4.0},
}

Level = Union[float, Callable[[int], float]]


class Context:
    """Everything a detector may read about bars[:as_of+1]."""

    def __init__(self, bars: Bars, market: Optional[Dict[str, Any]] = None):
        self.bars = bars
        self.symbol = bars.symbol
        self.timeframe = bars.timeframe
        self.n = len(bars)
        self.last = self.n - 1
        self.o, self.h, self.l, self.c, self.v = bars.o, bars.h, bars.l, bars.c, bars.v
        self.atr = atr(bars)
        self.profile = PROFILES.get(bars.timeframe, PROFILES["1d"])
        self.market = market or {}

    # --- series -------------------------------------------------------------
    def sma(self, period: int) -> np.ndarray:
        return self.bars.memo(f"sma{period}", lambda: sma(self.c, period))

    def ema(self, period: int) -> np.ndarray:
        return self.bars.memo(f"ema{period}", lambda: ema(self.c, period))

    def rsi(self, period: int) -> np.ndarray:
        return self.bars.memo(f"rsi{period}", lambda: rsi(self.c, period))

    def avg_volume(self) -> np.ndarray:
        return prior_average_volume(self.bars)

    def vwap(self) -> np.ndarray:
        return session_vwap(self.bars)

    def swings(self, scale: str = "minor", include_provisional: bool = True) -> List[Pivot]:
        return sequence(self.bars, self.profile[scale], include_provisional)

    def confirmed_swings(self, scale: str = "minor") -> List[Pivot]:
        return zigzag(self.bars, self.profile[scale])[0]

    def volume_ratio(self, i: int) -> Optional[float]:
        baseline = self.avg_volume()[i]
        if not np.isfinite(baseline) or baseline <= 0:
            return None
        return float(self.v[i] / baseline)

    def mean_volume(self, start: int, end: int) -> Optional[float]:
        start, end = max(0, start), min(self.last, end)
        if end < start:
            return None
        return float(np.mean(self.v[start:end + 1]))

    # --- context ------------------------------------------------------------
    def regime(self) -> Dict[str, Any]:
        """Trend and volatility regime at the last bar, from this series only."""
        def compute() -> Dict[str, Any]:
            if self.n < 60:
                return {"trend": "unknown", "volatility": "unknown", "reason": "fewer than 60 bars"}
            close = self.c[self.last]
            sma50 = self.sma(50)
            sma200 = self.sma(200)
            slope50 = sma50[self.last] - sma50[self.last - 10]
            above200 = None if not np.isfinite(sma200[self.last]) else close > sma200[self.last]
            if close > sma50[self.last] and slope50 > 0 and above200 is not False:
                trend = "uptrend"
            elif close < sma50[self.last] and slope50 < 0 and above200 is not True:
                trend = "downtrend"
            else:
                trend = "range"
            atr_percent = self.atr / np.maximum(self.c, 1e-9) * 100
            window = atr_percent[max(0, self.last - 250): self.last + 1]
            rank = float((window < atr_percent[self.last]).mean() * 100)
            volatility = "high" if rank >= 80 else "low" if rank <= 20 else "normal"
            return {
                "trend": trend, "volatility": volatility, "atr_percent": round(float(atr_percent[self.last]), 2),
                "atr_percentile": round(rank), "sma50_rising": bool(slope50 > 0),
                "above_sma200": above200,
            }
        return self.bars.memo("regime", compute)

    def point(self, label: str, index: int, price: float, provisional: bool = False) -> Dict[str, Any]:
        return {"label": label, "index": int(index), "t": self.bars.t[index], "price": round(float(price), 4), "provisional": bool(provisional)}

    def pivot_point(self, label: str, pivot: Pivot) -> Dict[str, Any]:
        point = self.point(label, pivot.index, pivot.price, pivot.provisional)
        point["confirmed_t"] = None if pivot.provisional else self.bars.t[pivot.confirmed_index]
        return point

    def segment(self, label: str, start: int, start_price: float, end: int, end_price: float, *, kind: str = "boundary") -> Dict[str, Any]:
        return {"label": label, "kind": kind, "from": self.point(label, start, start_price), "to": self.point(label, end, end_price)}


def _value(level: Level, i: int) -> float:
    return float(level(i)) if callable(level) else float(level)


def resolve(
    ctx: Context,
    *,
    direction: str,
    ready_index: int,
    trigger: Level,
    invalidation: Level,
    target: Optional[Level],
    awaiting_bars: int,
    signal_bars: int,
    volume_ratio: Optional[float] = None,
    trigger_buffer_atr: float = 0.0,
) -> Dict[str, Any]:
    """The shared state machine, walked bar by bar from `ready_index` (the bar
    at which every structural rule was first known to hold):

      a close beyond `invalidation` first            -> INVALIDATED
      a close beyond `trigger` (+ buffer, + volume)  -> CONFIRMED at that bar
      neither within `awaiting_bars`                 -> EXPIRED
    After confirming: a close back beyond invalidation -> INVALIDATED (failed),
    the target traded -> EXPIRED (no longer actionable), more than
    `signal_bars` bars since confirming -> EXPIRED."""
    sign = 1.0 if direction == "long" else -1.0
    notes: List[str] = []
    confirmed_index: Optional[int] = None
    ready_index = max(0, ready_index)

    def past(price: float, level: float) -> bool:   # price beyond level in the trade's direction
        return sign * (price - level) > 0

    for i in range(ready_index, ctx.last + 1):
        if past(_value(invalidation, i), ctx.c[i]):
            return {"state": model.INVALIDATED, "reason": f"closed beyond the invalidation level ({_value(invalidation, i):.2f}) before confirming",
                    "confirmed_index": None, "ended_index": i, "expires_index": None, "notes": notes}
        level = _value(trigger, i) + sign * trigger_buffer_atr * ctx.atr[i]
        if past(ctx.c[i], level):
            ratio = ctx.volume_ratio(i) if volume_ratio else None
            if volume_ratio and ratio is not None and ratio < volume_ratio:
                notes.append(f"{ctx.bars.t[i][:16]}: closed beyond the trigger on {ratio:.2f}x average volume (needs {volume_ratio:.2f}x) - not counted")
            else:
                if volume_ratio and ratio is None:
                    notes.append("volume baseline unavailable - confirmation counted on price alone")
                confirmed_index = i
                break
        if i - ready_index >= awaiting_bars:
            return {"state": model.EXPIRED, "reason": f"not confirmed within {awaiting_bars} bars", "confirmed_index": None,
                    "ended_index": i, "expires_index": ready_index + awaiting_bars, "notes": notes}

    if confirmed_index is None:
        return {"state": model.AWAITING_CONFIRMATION, "reason": "all structural rules met; waiting for the trigger",
                "confirmed_index": None, "ended_index": None, "expires_index": ready_index + awaiting_bars, "notes": notes}

    for j in range(confirmed_index + 1, ctx.last + 1):
        if past(_value(invalidation, j), ctx.c[j]):
            return {"state": model.INVALIDATED, "reason": "failed after confirming - closed back beyond the invalidation level",
                    "confirmed_index": confirmed_index, "ended_index": j, "expires_index": None, "notes": notes}
        if target is not None:
            extreme = ctx.h[j] if direction == "long" else ctx.l[j]
            if sign * (extreme - _value(target, j)) >= 0:
                return {"state": model.EXPIRED, "reason": "target already reached - no longer actionable",
                        "confirmed_index": confirmed_index, "ended_index": j, "expires_index": None, "notes": notes}
    if ctx.last - confirmed_index >= signal_bars:
        return {"state": model.EXPIRED, "reason": f"signal window of {signal_bars} bars after confirmation has passed",
                "confirmed_index": confirmed_index, "ended_index": confirmed_index + signal_bars, "expires_index": confirmed_index + signal_bars, "notes": notes}
    return {"state": model.CONFIRMED, "reason": "trigger crossed under the detector's rules", "confirmed_index": confirmed_index,
            "ended_index": None, "expires_index": confirmed_index + signal_bars, "notes": notes}


def developing(reason: str, expires_index: Optional[int] = None) -> Dict[str, Any]:
    return {"state": model.DEVELOPING, "reason": reason, "confirmed_index": None, "ended_index": None,
            "expires_index": expires_index, "notes": []}


def _clean_number(value: Any) -> Any:
    if isinstance(value, (float, np.floating)):
        return None if not math.isfinite(float(value)) else round(float(value), 4)
    if isinstance(value, (np.integer,)):
        return int(value)
    return value


def build_detection(
    ctx: Context,
    spec: model.DetectorSpec,
    *,
    direction: str,
    resolved: Dict[str, Any],
    anchor_index: int,
    start_index: int,
    end_index: int,
    points: Sequence[Dict[str, Any]],
    lines: Sequence[Dict[str, Any]] = (),
    trigger: Level,
    invalidation: Level,
    target: Optional[Level],
    trigger_rule: str,
    measurements: Optional[Dict[str, Any]] = None,
    evidence: Iterable[str] = (),
    counter: Iterable[str] = (),
) -> Dict[str, Any]:
    last = ctx.last
    at = resolved.get("confirmed_index") if resolved.get("confirmed_index") is not None else last
    provisional = any(point.get("provisional") for point in points)
    counter = list(counter)
    if provisional and resolved["state"] != model.DEVELOPING:
        counter.append("uses a provisional swing point")
    expires_index = resolved.get("expires_index")
    return {
        "key": f"{spec.id}|{ctx.timeframe}|{ctx.symbol}|{ctx.bars.t[anchor_index]}",
        "detector_id": spec.id,
        "version": spec.version,
        "name": spec.name,
        "family": spec.family,
        "specificity": spec.specificity,
        "symbol": ctx.symbol,
        "timeframe": ctx.timeframe,
        "direction": direction,
        "state": resolved["state"],
        "state_reason": resolved["reason"],
        "provisional": provisional,
        "as_of": ctx.bars.t[last],
        "as_of_index": last,
        "start_index": int(start_index),
        "end_index": int(end_index),
        "start_t": ctx.bars.t[start_index],
        "confirmed_index": resolved.get("confirmed_index"),
        "confirmed_t": ctx.bars.t[resolved["confirmed_index"]] if resolved.get("confirmed_index") is not None else None,
        "ended_t": ctx.bars.t[resolved["ended_index"]] if resolved.get("ended_index") is not None else None,
        "expires_after_bar": int(expires_index) if expires_index is not None else None,
        "bars_until_expiry": int(expires_index - last) if expires_index is not None else None,
        "points": list(points),
        "lines": list(lines),
        "levels": {
            "trigger": _clean_number(_value(trigger, at)),
            "trigger_now": _clean_number(_value(trigger, last)),
            "invalidation": _clean_number(_value(invalidation, last)),
            "target": _clean_number(_value(target, at)) if target is not None else None,
            "atr": _clean_number(ctx.atr[last]),
            "last_close": _clean_number(ctx.c[last]),
        },
        "trigger_rule": trigger_rule,
        "measurements": {key: _clean_number(value) for key, value in (measurements or {}).items()},
        "evidence": list(evidence),
        "counter_evidence": counter + list(resolved.get("notes") or []),
        "regime": ctx.regime(),
        "regime_applicable": ctx.regime().get("trend") in spec.regimes or "any" in spec.regimes,
    }


def scan_bars(
    bars: Bars,
    *,
    as_of: Optional[int] = None,
    detector_ids: Optional[List[str]] = None,
    market: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Runs every applicable detector on bars[:as_of+1] (default: all bars)."""
    if as_of is not None:
        bars = bars.upto(as_of)
    ctx = Context(bars, market)
    detections: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []
    for spec, fn in model.applicable(bars.timeframe, detector_ids):
        if ctx.n < spec.min_bars:
            continue
        try:
            detections.extend(fn(ctx, spec) or [])
        except Exception as error:  # noqa: BLE001 - one broken detector must not hide the others
            logger.exception("setup detector %s failed on %s %s", spec.id, bars.symbol, bars.timeframe)
            errors.append({"detector_id": spec.id, "error": f"{type(error).__name__}: {str(error)[:200]}"})
    return {
        "symbol": bars.symbol,
        "timeframe": bars.timeframe,
        "as_of": bars.t[-1] if len(bars) else None,
        "bars": len(bars),
        "regime": ctx.regime() if len(bars) else {},
        "detections": detections,
        "errors": errors,
    }
