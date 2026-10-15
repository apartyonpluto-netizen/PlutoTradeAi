"""Optional trailing stop. Off unless PLUTO_TRAILING_STOP_PERCENT is set.

The percent is the distance behind the latest price. The stop only moves in
the trade's favor, and only while that env var is a positive number. Unset
leaves the existing confidence-based stop refresh as the only tightener.
"""

from __future__ import annotations

import os
from typing import Optional


def configured_percent() -> Optional[float]:
    raw = os.environ.get("PLUTO_TRAILING_STOP_PERCENT", "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    if value <= 0 or value >= 100:
        return None
    return value


def maybe_tighten(current_stop: float, current_price: float, direction: str = "long") -> Optional[float]:
    """A tighter stop, or None when trailing is off or would not improve the stop."""
    percent = configured_percent()
    if percent is None or current_stop <= 0 or current_price <= 0:
        return None
    distance = current_price * (percent / 100.0)
    if direction == "short":
        candidate = round(current_price + distance, 2)
        if candidate < current_stop and candidate > current_price:
            return candidate
        return None
    candidate = round(current_price - distance, 2)
    if candidate > current_stop and candidate < current_price:
        return candidate
    return None
