from __future__ import annotations

"""Tier 1 of the "make autonomy learn" roadmap: human-readable performance
reporting, not automated behavior change. Joins realized outcomes
(closed_trades.py) back to the research-log decision that produced each one
(research_log.py, keyed by entry_client_order_id) and breaks down realized
win rate / P&L by strategy, confidence bucket, and VIX regime bucket at scan
time - so a person can see the account's actual track record and decide
what to adjust themselves, instead of guessing.

Deliberately does NOT feed anything back into _run_autonomous_trade_scan_locked
or any other live decision path - see that function's own confidence/sizing
logic, which remains completely unaffected by this module. An automated
system that adjusts its own trading parameters from this data (Tier 2) is a
substantially bigger, riskier undertaking that needs real trade-history
volume behind it first; this module is only the reporting layer."""

from typing import Any, Dict, List, Optional

from .closed_trades import list_closed_trades
from .research_log import list_research_decisions

# Below this many trades with KNOWN P&L in a bucket, a win rate/average P&L
# is still computed (so the number exists) but flagged sufficient_sample=False -
# a report reader must not treat a 2-trade "100% win rate" as a real signal.
MIN_SAMPLE_SIZE_FOR_RATES = 5

# (low, high, label) - inclusive low, inclusive high. Order here IS display
# order, not alphabetical - "55-64" before "65-74" etc.
CONFIDENCE_BUCKETS = [
    (55, 64, "55-64"),
    (65, 74, "65-74"),
    (75, 84, "75-84"),
    (85, 100, "85+"),
]

# (low, high, label) - inclusive low, EXCLUSIVE high (a VIX reading of
# exactly 25.0 falls into "Elevated", not "Normal").
VIX_BUCKETS = [
    (0, 15, "Low (<15)"),
    (15, 25, "Normal (15-25)"),
    (25, 35, "Elevated (25-35)"),
    (35, 999, "High (35+)"),
]


def _confidence_bucket(confidence: Optional[int]) -> str:
    if confidence is None:
        return "Unknown"
    for low, high, label in CONFIDENCE_BUCKETS:
        if low <= confidence <= high:
            return label
    return "Unknown"


def _vix_bucket(vix_level: Optional[float]) -> str:
    if vix_level is None:
        return "Unknown"
    for low, high, label in VIX_BUCKETS:
        if low <= vix_level < high:
            return label
    return "Unknown"


def _new_accumulator() -> Dict[str, Any]:
    return {"count": 0, "wins": 0, "losses": 0, "total_pnl": 0.0, "pnl_known_count": 0}


def _record_outcome(accumulator: Dict[str, Any], net_realized_pnl: Optional[float]) -> None:
    accumulator["count"] += 1
    if net_realized_pnl is None:
        return
    accumulator["pnl_known_count"] += 1
    accumulator["total_pnl"] += net_realized_pnl
    # A trade that closed at exactly breakeven counts toward pnl totals and
    # the trade count, but is neither a win nor a loss - a "scratch."
    if net_realized_pnl > 0:
        accumulator["wins"] += 1
    elif net_realized_pnl < 0:
        accumulator["losses"] += 1


def _finalize_bucket(label: str, accumulator: Dict[str, Any]) -> Dict[str, Any]:
    pnl_known = accumulator["pnl_known_count"]
    decided = accumulator["wins"] + accumulator["losses"]
    return {
        "label": label,
        "count": accumulator["count"],
        "wins": accumulator["wins"],
        "losses": accumulator["losses"],
        "win_rate_percent": round(accumulator["wins"] / decided * 100, 1) if decided else None,
        "total_pnl": round(accumulator["total_pnl"], 2) if pnl_known else None,
        "avg_pnl": round(accumulator["total_pnl"] / pnl_known, 2) if pnl_known else None,
        "sufficient_sample": pnl_known >= MIN_SAMPLE_SIZE_FOR_RATES,
    }


def _ordered_buckets(accumulators: Dict[str, Dict[str, Any]], bucket_defs: List[tuple]) -> List[Dict[str, Any]]:
    """Renders buckets in the FIXED display order from bucket_defs (never
    alphabetical - "55-64" must sort before "65-74" as a concept, not as a
    string), only including a bucket that actually has at least one trade,
    with "Unknown" always last if present."""
    ordered = [
        _finalize_bucket(label, accumulators[label]) for _, _, label in bucket_defs if label in accumulators
    ]
    if "Unknown" in accumulators:
        ordered.append(_finalize_bucket("Unknown", accumulators["Unknown"]))
    return ordered


def build_performance_report(user_id: str) -> Dict[str, Any]:
    closed_trades = list_closed_trades(user_id)
    research_by_client_order_id = {
        record["entry_client_order_id"]: record
        for record in list_research_decisions(user_id)
        if record.get("entry_client_order_id")
    }

    by_strategy: Dict[str, Dict[str, Any]] = {}
    by_confidence: Dict[str, Dict[str, Any]] = {}
    by_regime: Dict[str, Dict[str, Any]] = {}
    overall = _new_accumulator()
    incomplete_pnl_count = 0

    for trade in closed_trades:
        net_pnl = trade.get("net_realized_pnl")
        if trade.get("pnl_status") != "complete":
            incomplete_pnl_count += 1

        strategy_label = trade.get("strategy") or "Unknown"
        by_strategy.setdefault(strategy_label, _new_accumulator())
        _record_outcome(by_strategy[strategy_label], net_pnl)

        research_record = research_by_client_order_id.get(trade.get("entry_client_order_id"))
        confidence_label = _confidence_bucket(research_record.get("raw_confidence") if research_record else None)
        by_confidence.setdefault(confidence_label, _new_accumulator())
        _record_outcome(by_confidence[confidence_label], net_pnl)

        vix_level = None
        if research_record and isinstance(research_record.get("regime_shadow"), dict):
            vix_level = research_record["regime_shadow"].get("vix_level")
        regime_label = _vix_bucket(vix_level)
        by_regime.setdefault(regime_label, _new_accumulator())
        _record_outcome(by_regime[regime_label], net_pnl)

        _record_outcome(overall, net_pnl)

    return {
        "overall": _finalize_bucket("Overall", overall),
        "by_strategy": [_finalize_bucket(label, acc) for label, acc in sorted(by_strategy.items())],
        "by_confidence": _ordered_buckets(by_confidence, CONFIDENCE_BUCKETS),
        "by_regime": _ordered_buckets(by_regime, VIX_BUCKETS),
        "total_closed_trades": len(closed_trades),
        "incomplete_pnl_count": incomplete_pnl_count,
        "min_sample_size_for_rates": MIN_SAMPLE_SIZE_FOR_RATES,
    }
