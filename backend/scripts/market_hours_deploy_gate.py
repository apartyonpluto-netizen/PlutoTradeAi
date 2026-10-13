"""Refuse a Render deploy during the regular US cash session.

Render cannot do a zero-downtime deploy of a service that has a persistent
disk: it stops the old instance before the new one starts, and this app's
start has been leaving 1-2 minutes of 502s. A failed pre-deploy command
aborts the deploy and leaves the current instance serving (Render keeps
the last successful deploy up). Outside the window this script exits 0
and the disk swap proceeds.

Set PLUTO_ALLOW_MARKET_HOURS_DEPLOY=1 on the web service to let a hotfix
through, then remove it. The window is weekdays 09:30–16:00 America/New_York.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, time
from zoneinfo import ZoneInfo

_NY = ZoneInfo("America/New_York")
_OPEN = time(9, 30)
_CLOSE = time(16, 0)


def market_hours_block(now: datetime | None = None) -> str:
    """Empty string when a deploy may proceed, otherwise the reason to abort."""
    if os.environ.get("PLUTO_ALLOW_MARKET_HOURS_DEPLOY", "").strip() == "1":
        return ""
    moment = now or datetime.now(_NY)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=_NY)
    else:
        moment = moment.astimezone(_NY)
    if moment.weekday() >= 5:
        return ""
    current = moment.time()
    if _OPEN <= current < _CLOSE:
        return (
            f"refusing deploy at {moment.strftime('%Y-%m-%d %H:%M %Z')}: "
            "regular US market hours. The running instance stays up. "
            "Redeploy after 16:00 ET, or set PLUTO_ALLOW_MARKET_HOURS_DEPLOY=1 for a hotfix and then remove it."
        )
    return ""


def main() -> int:
    reason = market_hours_block()
    if reason:
        print(reason, file=sys.stderr)
        return 1
    print("deploy gate: outside regular market hours, or override set; continuing")
    return 0


if __name__ == "__main__":
    sys.exit(main())
