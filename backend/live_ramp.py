"""Live position sizes start smaller than the paper size.

Paper sizing is unchanged. When live trading is armed, opening quantities
are floored to PLUTO_LIVE_SIZE_FRACTION of the paper quantity (default 0.25,
so the first live trades are a quarter of what paper would have taken).
A value of 0 refuses the trade. A missing or invalid value uses the default
rather than silently sizing at full paper size.
"""

from __future__ import annotations

import os

DEFAULT_FRACTION = 0.25
ENV_VAR = "PLUTO_LIVE_SIZE_FRACTION"


def fraction() -> float:
    raw = os.environ.get(ENV_VAR, "").strip()
    if not raw:
        return DEFAULT_FRACTION
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_FRACTION
    if value < 0 or value > 1:
        return DEFAULT_FRACTION
    return value


def scale_quantity(quantity: int) -> int:
    """Floor. 0 means the ramp leaves less than one share or contract."""
    if quantity < 1:
        return 0
    scaled = int(quantity * fraction())
    if fraction() > 0 and scaled < 1:
        return 0
    return scaled
