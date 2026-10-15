"""X mention-velocity spikes, behind a provider the caller can replace.

The built-in provider uses the recent-counts endpoint with requests.
It is off unless X_BEARER_TOKEN (or X_API_BEARER_TOKEN) is set. There is
no paid client library. A spike is attention on a suggestion that already
exists: it does not pick CALL versus PUT.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Protocol

from market_intel.support import cached, missing, observation

SIGNAL = "x_mention_velocity"
COUNTS_URL = "https://api.x.com/2/tweets/counts/recent"
RECENT_HOURS = 1
BASELINE_HOURS = 6
SPIKE_MULTIPLE = 3.0
SPIKE_MIN_RECENT = 5
SPIKE_MIN_FROM_QUIET = 8


class MentionProvider(Protocol):
    def enabled(self) -> bool:
        """False when this provider should not be called."""

    def hourly_counts(self, ticker: str) -> Optional[List[int]]:
        """Oldest hour first, latest hour last. None when the source cannot answer."""


class DisabledMentionProvider:
    def enabled(self) -> bool:
        return False

    def hourly_counts(self, ticker: str) -> Optional[List[int]]:
        return None


class XRecentCountProvider:
    def __init__(self, token: str, getter: Any = None):
        self._token = token
        self._getter = getter

    def enabled(self) -> bool:
        return bool(self._token)

    def hourly_counts(self, ticker: str) -> Optional[List[int]]:
        symbol = str(ticker or "").strip().upper()
        if not symbol:
            return None
        end = datetime.now(timezone.utc) - timedelta(seconds=30)
        start = end - timedelta(hours=RECENT_HOURS + BASELINE_HOURS)
        params = {
            "query": f"${symbol} -is:retweet",
            "granularity": "hour",
            "start_time": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end_time": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        getter = self._getter
        if getter is None:
            import requests

            getter = requests.get
        response = getter(
            COUNTS_URL,
            params=params,
            headers={"Authorization": f"Bearer {self._token}"},
            timeout=(2.5, 3.5),
        )
        status = int(getattr(response, "status_code", 200) or 200)
        if status != 200:
            return None
        payload = response.json()
        rows = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            return None
        counts = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                counts.append(int(row.get("tweet_count") or 0))
            except (TypeError, ValueError):
                counts.append(0)
        return counts or None


_provider: Optional[MentionProvider] = None


def set_provider(provider: Optional[MentionProvider]) -> None:
    global _provider
    _provider = provider


def get_provider() -> MentionProvider:
    if _provider is not None:
        return _provider
    token = os.environ.get("X_BEARER_TOKEN") or os.environ.get("X_API_BEARER_TOKEN") or ""
    token = token.strip()
    if not token:
        return DisabledMentionProvider()
    return XRecentCountProvider(token)


def signal(ticker: str) -> Dict[str, Any]:
    symbol = str(ticker or "").strip().upper()
    provider = get_provider()
    if not provider.enabled():
        return missing(SIGNAL, "disabled", "X mention velocity is off until X_BEARER_TOKEN is set")
    if not symbol:
        return missing(SIGNAL, "invalid_ticker")

    def produce() -> Dict[str, Any]:
        try:
            counts = provider.hourly_counts(symbol)
        except Exception:
            return missing(SIGNAL, "source_unavailable")
        if counts is None:
            return missing(SIGNAL, "source_unavailable")
        return from_hourly_counts(counts)

    try:
        return cached(f"mentions:v1:{symbol}", 10 * 60, produce)
    except Exception:
        return missing(SIGNAL, "source_unavailable")


def from_hourly_counts(counts: List[int]) -> Dict[str, Any]:
    if len(counts) < 2:
        return missing(SIGNAL, "insufficient_history", "not enough mention hours to judge velocity")
    recent = max(0, int(counts[-1]))
    prior = [max(0, int(value)) for value in counts[:-1]]
    baseline = sum(prior) / len(prior)
    if baseline <= 0:
        spike = recent >= SPIKE_MIN_FROM_QUIET
    else:
        spike = recent >= SPIKE_MIN_RECENT and recent >= SPIKE_MULTIPLE * baseline
    if not spike:
        return observation(
            SIGNAL,
            direction="neutral",
            magnitude=0,
            detail=f"mentions {recent} in the latest hour versus {baseline:.1f}/hour before that",
        )
    return observation(
        SIGNAL,
        direction="attention",
        magnitude=2,
        detail=f"mention velocity spiked to {recent} in the latest hour versus {baseline:.1f}/hour",
    )
