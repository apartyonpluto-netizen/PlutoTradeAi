"""Turn the four observations into confidence votes and a weekly summary.

A vote can raise or lower the score of a CALL or PUT that the strategy
brain already produced. It cannot raise a below-floor score up to the
floor, and it never chooses a side or places an order. Attention from
X confirms the existing side; it does not invent one.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, wait
from typing import Any, Callable, Dict, List, Optional

from market_intel import edgar, mentions, unusual
from market_intel.support import clear_state as clear_support_state
from market_intel.support import missing

PLACES_ORDERS = False
SIGNAL_IDS = (
    "form4_insider_buy",
    "form13f_holdings",
    "unusual_activity",
    "x_mention_velocity",
)
SIGNAL_LABELS = {
    "form4_insider_buy": "SEC Form 4 insider open-market buys",
    "form13f_holdings": "13F institutional holdings, quarter over quarter",
    "unusual_activity": "Unusual volume and price versus the trailing baseline",
    "x_mention_velocity": "X mention-velocity spikes",
}
MAX_POINTS_PER_SIGNAL = 3
MAX_TOTAL_POINTS = 8
OBSERVE_BUDGET_SECONDS = 6

def _signal_funcs():
    return (
        (edgar.FORM4_SIGNAL, edgar.form4_signal),
        (edgar.HOLDINGS_SIGNAL, edgar.holdings_signal),
        (unusual.SIGNAL, unusual.signal),
        (mentions.SIGNAL, mentions.signal),
    )


def score(ticker: str, *, recommendation: str, base_confidence: int, threshold: int) -> Dict[str, Any]:
    """Nudge `base_confidence`. The recommendation is not changed."""
    base = _clamp_score(base_confidence)
    floor = int(threshold or 0)
    raw_votes = observe(ticker)
    votes = []
    total = 0
    for raw in raw_votes:
        points = aligned_points(raw, recommendation)
        total += points
        votes.append(
            {
                "signal": raw.get("signal"),
                "present": bool(raw.get("present")),
                "points": points,
                "direction": raw.get("direction") or "neutral",
                "detail": raw.get("detail") or "",
                "reason": raw.get("reason"),
            }
        )
    total = max(-MAX_TOTAL_POINTS, min(MAX_TOTAL_POINTS, total))
    adjusted = apply_adjustment(base, total, floor)
    return {
        "base_confidence": base,
        "adjusted_confidence": adjusted,
        "adjustment": adjusted - base,
        "threshold": floor,
        "recommendation": str(recommendation or "").upper(),
        "places_orders": False,
        "votes": votes,
    }


def observe(ticker: str) -> List[Dict[str, Any]]:
    """One observation per signal. A timeout or a crash becomes a missing vote."""
    by_id: Dict[str, Dict[str, Any]] = {}
    funcs = _signal_funcs()
    pool = ThreadPoolExecutor(max_workers=len(funcs), thread_name_prefix="mkt-intel")
    futures = {}
    try:
        for signal_id, func in funcs:
            futures[pool.submit(_safe_signal, signal_id, func, ticker)] = signal_id
        done, pending = wait(futures, timeout=OBSERVE_BUDGET_SECONDS)
        for future in done:
            signal_id = futures[future]
            try:
                by_id[signal_id] = future.result()
            except Exception:
                by_id[signal_id] = missing(signal_id, "source_unavailable")
        for future in pending:
            by_id[futures[future]] = missing(futures[future], "timeout")
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    ordered = []
    for signal_id, _func in funcs:
        vote = by_id.get(signal_id) or missing(signal_id, "source_unavailable")
        if not isinstance(vote, dict):
            vote = missing(signal_id, "source_unavailable")
        vote["signal"] = signal_id
        ordered.append(vote)
    return ordered


def aligned_points(raw: Dict[str, Any], recommendation: str) -> int:
    if not raw.get("present"):
        return 0
    side = str(recommendation or "").upper()
    if side not in ("CALL", "PUT"):
        return 0
    try:
        magnitude = int(raw.get("magnitude") or 0)
    except (TypeError, ValueError):
        magnitude = 0
    magnitude = max(0, min(MAX_POINTS_PER_SIGNAL, magnitude))
    direction = str(raw.get("direction") or "")
    if direction == "attention":
        return magnitude
    if direction == "bullish":
        return magnitude if side == "CALL" else -magnitude
    if direction == "bearish":
        return magnitude if side == "PUT" else -magnitude
    return 0


def apply_adjustment(base: int, points: int, threshold: int) -> int:
    """Apply the vote total. A below-floor score stays below the floor."""
    base = _clamp_score(base)
    bounded = max(-MAX_TOTAL_POINTS, min(MAX_TOTAL_POINTS, int(points)))
    adjusted = _clamp_score(base + bounded)
    if threshold > 0 and base < int(threshold) and adjusted >= int(threshold):
        adjusted = int(threshold) - 1
    return _clamp_score(adjusted)


def stamp_grades(row: Dict[str, Any]) -> None:
    """Mark each graded vote using the ledger row's forward return."""
    intel = row.get("market_intel")
    if not isinstance(intel, dict):
        return
    forward = _forward(row)
    for vote in intel.get("votes") or []:
        if isinstance(vote, dict):
            vote["hit"] = vote_hit(vote, forward)


def summary(user_id: Optional[str] = None) -> Dict[str, Any]:
    """Hit rates the weekly check-in can read. Same ~5-weekday grade as the ledger."""
    import suggestion_ledger

    buckets = {
        signal_id: {"signal": signal_id, "label": SIGNAL_LABELS[signal_id], "graded": 0, "hits": 0, "hit_rate": None, "fires": 0, "missing": 0, "disabled": 0}
        for signal_id in SIGNAL_IDS
    }
    for row in suggestion_ledger.list_rows(user_id):
        intel = row.get("market_intel")
        if not isinstance(intel, dict):
            continue
        forward = _forward(row) if row.get("graded_at") else None
        for vote in intel.get("votes") or []:
            if not isinstance(vote, dict):
                continue
            signal_id = str(vote.get("signal") or "")
            bucket = buckets.get(signal_id)
            if bucket is None:
                continue
            reason = vote.get("reason")
            if reason == "disabled" or (not vote.get("present") and reason == "disabled"):
                bucket["disabled"] += 1
                continue
            if not vote.get("present"):
                bucket["missing"] += 1
                continue
            try:
                points = int(vote.get("points") or 0)
            except (TypeError, ValueError):
                points = 0
            if points:
                bucket["fires"] += 1
            if forward is None:
                continue
            hit = vote_hit(vote, forward)
            if hit is None:
                continue
            bucket["graded"] += 1
            if hit:
                bucket["hits"] += 1
    for bucket in buckets.values():
        if bucket["graded"]:
            bucket["hit_rate"] = round(bucket["hits"] / bucket["graded"], 4)
    return {
        "paper_only": True,
        "places_orders": False,
        "grade_weekdays": suggestion_ledger.GRADE_WEEKDAYS,
        "signals": list(buckets.values()),
    }


def vote_hit(vote: Dict[str, Any], forward: Optional[float]) -> Optional[bool]:
    """A supporting vote hits when the forward return is positive. An opposing vote hits when it is negative."""
    if forward is None or not vote.get("present") or vote.get("reason") == "disabled":
        return None
    try:
        points = int(vote.get("points") or 0)
        result = float(forward)
    except (TypeError, ValueError):
        return None
    if points > 0:
        return result > 0
    if points < 0:
        return result < 0
    return None


def set_mention_provider(provider: Any) -> None:
    mentions.set_provider(provider)


def clear_state() -> None:
    mentions.set_provider(None)
    clear_support_state()


def _safe_signal(signal_id: str, func: Callable[[str], Dict[str, Any]], ticker: str) -> Dict[str, Any]:
    try:
        vote = func(ticker)
    except Exception:
        return missing(signal_id, "source_unavailable")
    if not isinstance(vote, dict):
        return missing(signal_id, "source_unavailable")
    vote["signal"] = signal_id
    return vote


def _clamp_score(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = 0
    return max(0, min(99, number))


def _forward(row: Dict[str, Any]) -> Optional[float]:
    value = row.get("forward_return_percent")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
