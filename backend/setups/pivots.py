"""Swing points by an ATR-scaled zigzag.

A swing high at bar i is CONFIRMED at the first later bar whose low is at
least `atr_multiple` x ATR(i) below it (a swing low mirrors that). Until then
the running extreme is PROVISIONAL and is always labeled as such. A bar that
makes a new extreme cannot also confirm the reversal (the order of the high
and low inside one bar is unknown), so confirmation is always strictly later.

Every pivot carries the bar at which it became known (`confirmed_index`).
The zigzag walks bars in order and never looks ahead, so the confirmed
pivots computed on bars[:t+1] are exactly the pivots of the full series
whose confirmed_index <= t."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from .bars import Bars, atr

HIGH = "H"
LOW = "L"


@dataclass(frozen=True)
class Pivot:
    index: int
    price: float
    kind: str
    confirmed_index: Optional[int]

    @property
    def provisional(self) -> bool:
        return self.confirmed_index is None


def zigzag(bars: Bars, atr_multiple: float) -> Tuple[List[Pivot], Optional[Pivot]]:
    """(confirmed pivots in order, the provisional running extreme or None)."""
    def compute() -> Tuple[List[Pivot], Optional[Pivot]]:
        n = len(bars)
        if n < 3:
            return [], None
        ranges = atr(bars)
        highs, lows = bars.h, bars.l
        confirmed: List[Pivot] = []
        direction = 0  # +1 tracking a high, -1 tracking a low, 0 undecided
        high_index = low_index = 0
        extreme = 0
        for i in range(1, n):
            if direction == 0:
                if highs[i] > highs[high_index]:
                    high_index = i
                if lows[i] < lows[low_index]:
                    low_index = i
                if high_index < i and highs[high_index] - lows[i] >= atr_multiple * ranges[high_index]:
                    confirmed.append(Pivot(high_index, float(highs[high_index]), HIGH, i))
                    direction, extreme = -1, i
                elif low_index < i and highs[i] - lows[low_index] >= atr_multiple * ranges[low_index]:
                    confirmed.append(Pivot(low_index, float(lows[low_index]), LOW, i))
                    direction, extreme = 1, i
                continue
            if direction == 1:
                if highs[i] > highs[extreme]:
                    extreme = i
                elif highs[extreme] - lows[i] >= atr_multiple * ranges[extreme]:
                    confirmed.append(Pivot(extreme, float(highs[extreme]), HIGH, i))
                    direction, extreme = -1, i
            else:
                if lows[i] < lows[extreme]:
                    extreme = i
                elif highs[i] - lows[extreme] >= atr_multiple * ranges[extreme]:
                    confirmed.append(Pivot(extreme, float(lows[extreme]), LOW, i))
                    direction, extreme = 1, i
        provisional = None
        if direction == 1:
            provisional = Pivot(extreme, float(highs[extreme]), HIGH, None)
        elif direction == -1:
            provisional = Pivot(extreme, float(lows[extreme]), LOW, None)
        if provisional is not None and confirmed and provisional.index <= confirmed[-1].index:
            provisional = None
        return confirmed, provisional
    return bars.memo(f"zigzag{atr_multiple}", compute)


def sequence(bars: Bars, atr_multiple: float, include_provisional: bool = True) -> List[Pivot]:
    confirmed, provisional = zigzag(bars, atr_multiple)
    return confirmed + ([provisional] if include_provisional and provisional is not None else [])
