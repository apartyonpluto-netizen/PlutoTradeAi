from __future__ import annotations

"""Tier 1 reporting, same boundary as performance_report.py/
efficiency_report.py - joins realized outcomes (closed_trades.py) back to
the research-log decision that produced each one (research_log.py, keyed
by entry_client_order_id, same join performance_report.py already does)
and breaks down realized win rate / P&L by the NEW signal_snapshot data
research_log.py started capturing 2026-09-11 (see
_log_research_decision's own docstring in app.py) - strategy_brain's RSI/
EMA-stack/VWAP/relative-volume reading for each candidate at the moment
it was evaluated.

Deliberately does NOT duplicate performance_report.py's existing
by_strategy/by_confidence/by_regime breakdown - that report already
covers those; this one exists specifically to answer the NEW question
the memory layer makes askable: "did strategy_brain's own technical
picture at entry actually correlate with what happened." Never called
from the scan - this is the evidence a human reads before ever deciding
to promote a currently-silent brain to live-decision-influencing (see
autonomy/autonomous_controller.py's validated_brains gate), not a
decision itself.

NOT included here (yet): candle_brain, pattern_brain, and neural_engine
buckets - research_log.py's signal_snapshot does not capture their
output (see _log_research_decision's own docstring for why: they'd add
real yfinance calls to every candidate on every scan tick). Add their
buckets here once signal_snapshot actually carries that data, not
before - a bucket with no real data behind it would be worse than no
bucket at all."""

from typing import Any, Dict, List, Optional

from .closed_trades import list_closed_trades
from .performance_report import MIN_SAMPLE_SIZE_FOR_RATES
from .research_log import list_research_decisions

# (low, high, label) - inclusive low, exclusive high, same convention as
# performance_report.py's own bucket tables.
RSI_BUCKETS = [
    (0, 30, "Oversold (<30)"),
    (30, 70, "Neutral (30-70)"),
    (70, 1000, "Overbought (70+)"),
]

RELATIVE_VOLUME_BUCKETS = [
    (0, 1.0, "Below average (<1.0x)"),
    (1.0, 2.0, "Elevated (1.0-2.0x)"),
    (2.0, 1000, "High (2.0x+)"),
]


def _rsi_bucket(rsi_14: Optional[float]) -> str:
    if rsi_14 is None:
        return "Unknown"
    for low, high, label in RSI_BUCKETS:
        if low <= rsi_14 < high:
            return label
    return "Unknown"


def _relative_volume_bucket(relative_volume: Optional[float]) -> str:
    if relative_volume is None:
        return "Unknown"
    for low, high, label in RELATIVE_VOLUME_BUCKETS:
        if low <= relative_volume < high:
            return label
    return "Unknown"


def _ema_stack_label(market_context: Dict[str, Any]) -> str:
    ema9, ema20, ema50 = market_context.get("ema_9"), market_context.get("ema_20"), market_context.get("ema_50")
    if ema9 is None or ema20 is None or ema50 is None:
        return "Unknown"
    if ema9 > ema20 > ema50:
        return "Bullish stack (9>20>50)"
    if ema9 < ema20 < ema50:
        return "Bearish stack (9<20<50)"
    return "Mixed"


def _price_vs_vwap_label(market_context: Dict[str, Any]) -> str:
    price, vwap = market_context.get("current_price"), market_context.get("vwap")
    if price is None or not vwap:
        return "Unknown"
    if price > vwap:
        return "Above VWAP"
    if price < vwap:
        return "Below VWAP"
    return "At VWAP"


def _new_accumulator() -> Dict[str, Any]:
    return {"count": 0, "wins": 0, "losses": 0, "total_pnl": 0.0, "pnl_known_count": 0}


def _record_outcome(accumulator: Dict[str, Any], net_realized_pnl: Optional[float]) -> None:
    accumulator["count"] += 1
    if net_realized_pnl is None:
        return
    accumulator["pnl_known_count"] += 1
    accumulator["total_pnl"] += net_realized_pnl
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
    ordered = [_finalize_bucket(label, accumulators[label]) for _, _, label in bucket_defs if label in accumulators]
    if "Unknown" in accumulators:
        ordered.append(_finalize_bucket("Unknown", accumulators["Unknown"]))
    return ordered


def _labeled_buckets(accumulators: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """For non-numeric-range buckets (ema stack alignment, vwap relation) -
    sorted alphabetically with "Unknown" always last, mirroring
    _ordered_buckets' "Unknown last" convention without needing a fixed
    display-order table for categorical (not range) labels."""
    known_labels = sorted(label for label in accumulators if label != "Unknown")
    ordered = [_finalize_bucket(label, accumulators[label]) for label in known_labels]
    if "Unknown" in accumulators:
        ordered.append(_finalize_bucket("Unknown", accumulators["Unknown"]))
    return ordered


def build_outcomes_analysis(user_id: str) -> Dict[str, Any]:
    closed_trades = list_closed_trades(user_id)
    research_by_client_order_id = {
        record["entry_client_order_id"]: record
        for record in list_research_decisions(user_id)
        if record.get("entry_client_order_id")
    }

    by_rsi: Dict[str, Dict[str, Any]] = {}
    by_ema_stack: Dict[str, Dict[str, Any]] = {}
    by_relative_volume: Dict[str, Dict[str, Any]] = {}
    by_vwap: Dict[str, Dict[str, Any]] = {}
    overall = _new_accumulator()
    signal_snapshot_available_count = 0

    for trade in closed_trades:
        net_pnl = trade.get("net_realized_pnl")
        research_record = research_by_client_order_id.get(trade.get("entry_client_order_id"))
        signal_snapshot = research_record.get("signal_snapshot") if research_record else None
        market_context = signal_snapshot.get("market_context") if isinstance(signal_snapshot, dict) else None
        if isinstance(market_context, dict) and market_context:
            signal_snapshot_available_count += 1
        market_context = market_context if isinstance(market_context, dict) else {}

        rsi_label = _rsi_bucket(market_context.get("rsi_14"))
        by_rsi.setdefault(rsi_label, _new_accumulator())
        _record_outcome(by_rsi[rsi_label], net_pnl)

        ema_label = _ema_stack_label(market_context)
        by_ema_stack.setdefault(ema_label, _new_accumulator())
        _record_outcome(by_ema_stack[ema_label], net_pnl)

        rel_vol_label = _relative_volume_bucket(market_context.get("relative_volume"))
        by_relative_volume.setdefault(rel_vol_label, _new_accumulator())
        _record_outcome(by_relative_volume[rel_vol_label], net_pnl)

        vwap_label = _price_vs_vwap_label(market_context)
        by_vwap.setdefault(vwap_label, _new_accumulator())
        _record_outcome(by_vwap[vwap_label], net_pnl)

        _record_outcome(overall, net_pnl)

    return {
        "overall": _finalize_bucket("Overall", overall),
        "by_rsi_at_entry": _ordered_buckets(by_rsi, RSI_BUCKETS),
        "by_ema_stack_alignment": _labeled_buckets(by_ema_stack),
        "by_relative_volume": _ordered_buckets(by_relative_volume, RELATIVE_VOLUME_BUCKETS),
        "by_price_vs_vwap": _labeled_buckets(by_vwap),
        "total_closed_trades": len(closed_trades),
        "signal_snapshot_available_count": signal_snapshot_available_count,
        "min_sample_size_for_rates": MIN_SAMPLE_SIZE_FOR_RATES,
        "not_yet_captured": ["candle_brain", "pattern_brain", "neural_engine"],
    }
