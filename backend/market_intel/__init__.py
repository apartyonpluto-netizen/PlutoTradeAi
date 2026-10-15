"""Extra market-intel votes. They nudge confidence on a suggestion that
already exists. They do not place orders, and they cannot lift a failing
strategy score up to the trade floor.

Each source is cached, timed out, and allowed to fail on its own. A dead
source leaves that vote missing. The scan keeps the strategy score.
"""

from market_intel.scoring import (
    PLACES_ORDERS,
    SIGNAL_IDS,
    clear_state,
    score,
    set_mention_provider,
    stamp_grades,
    summary,
)

__all__ = [
    "PLACES_ORDERS",
    "SIGNAL_IDS",
    "clear_state",
    "score",
    "set_mention_provider",
    "stamp_grades",
    "summary",
]
