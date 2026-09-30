"""Helpers shared by detectors."""

from __future__ import annotations

from typing import Callable, List, Optional, Sequence, Tuple

import numpy as np

from ..pivots import HIGH, LOW, Pivot

DAILY_AND_INTRADAY = ("1d", "1h", "30m", "15m", "5m")
DAILY_AND_HOURLY = ("1d", "1h")
INTRADAY_ONLY = ("30m", "15m", "10m", "5m", "1m")


def line_through(i1: int, p1: float, i2: int, p2: float) -> Callable[[int], float]:
    slope = (p2 - p1) / (i2 - i1) if i2 != i1 else 0.0
    return lambda i: p1 + slope * (i - i1)


def fit_line(points: Sequence[Tuple[int, float]]) -> Tuple[float, float]:
    """Least-squares (slope, intercept) through (index, price) points."""
    xs = np.array([p[0] for p in points], dtype=float)
    ys = np.array([p[1] for p in points], dtype=float)
    if len(xs) < 2 or np.ptp(xs) == 0:
        return 0.0, float(ys.mean()) if len(ys) else 0.0
    slope, intercept = np.polyfit(xs, ys, 1)
    return float(slope), float(intercept)


def windows(seq: Sequence[Pivot], kinds: str, max_back: int = 3) -> List[List[Pivot]]:
    """Runs of consecutive pivots whose kinds spell `kinds` (e.g. "HLHLH"),
    ending at one of the last `max_back` pivots, newest first."""
    out: List[List[Pivot]] = []
    size = len(kinds)
    for end in range(len(seq) - 1, max(len(seq) - 1 - max_back, size - 2), -1):
        start = end - size + 1
        if start < 0:
            break
        run = list(seq[start:end + 1])
        if "".join(p.kind for p in run) == kinds:
            out.append(run)
    return out


def lowest_before(ctx, index: int, lookback: int) -> float:
    start = max(0, index - lookback)
    return float(ctx.l[start:index + 1].min())


def highest_before(ctx, index: int, lookback: int) -> float:
    start = max(0, index - lookback)
    return float(ctx.h[start:index + 1].max())


def ready_of(pivots: Sequence[Pivot]) -> Optional[int]:
    """Bar at which the last of `pivots` became known; None if any is provisional."""
    if any(p.provisional for p in pivots):
        return None
    return max(p.confirmed_index for p in pivots)


def volume_note(ctx, label_a: str, a: Tuple[int, int], label_b: str, b: Tuple[int, int], *, want_lower_b: bool) -> Tuple[List[str], List[str]]:
    """Evidence/counter-evidence comparing mean volume of two bar windows."""
    va, vb = ctx.mean_volume(*a), ctx.mean_volume(*b)
    if not va or vb is None:
        return [], ["volume comparison unavailable"]
    ratio = vb / va
    text = f"{label_b} volume is {ratio:.2f}x {label_a} volume"
    good = ratio < 1 if want_lower_b else ratio > 1
    return ([text], []) if good else ([], [text])


def is_high(p: Pivot) -> bool:
    return p.kind == HIGH


def is_low(p: Pivot) -> bool:
    return p.kind == LOW
