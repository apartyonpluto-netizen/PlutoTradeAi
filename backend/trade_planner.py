"""Account-aware trade plan: one explainable decision per candidate.

Pure functions, no I/O. The caller supplies the account snapshot, the
user's settings, the strategy's levels and the sizing result from
app._compute_position_quantity - the SAME function the autonomous scan
sizes with - so a plan and the order the scan would place never disagree.

Every number keeps its own name and meaning (never "20%" for three
different things):

    allocation_percent    position market value / plan equity
    planned_loss          $ lost if the stop fills exactly at the stop,
                          including the per-share cost allowance
    risk_percent          planned_loss / plan equity
    exposure              position market value (price x quantity)
    leverage_after        (existing exposure + this position) / plan equity
    buying_power_used     $ of available buying power this order consumes
    win_probability       only from real closed trades of this strategy,
                          same exit rules, at least MIN_TRADES_FOR_PROBABILITY
                          of them - otherwise None, "not reliably estimated"
    expected_value        mean net return of those same trades x position
                          value - only when win_probability is available

A "win" is a closed trade of this strategy with positive net realized P&L
(after fees) under its own stop/target exits. Nothing here is a promise:
stops can gap, fills can slip, and a small sample can mislead.
"""

from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, List, Optional, Sequence

PLANNER_VERSION = "trade_plan_v1"
MIN_TRADES_FOR_PROBABILITY = 30
MAX_QUOTE_AGE_SECONDS = 120
# Adverse-scenario assumptions (labeled as such on every plan).
STOP_SLIPPAGE_PERCENT = Decimal("0.5")   # stop fills 0.5% worse than the stop price
DEFAULT_GAP_MULTIPLE = Decimal("2")      # overnight gap through the stop: 2x the stop distance
MIN_REWARD_TO_RISK = Decimal("1.0")      # below this the setup is "watch", not "trade"

_CENT = Decimal("0.01")


def _d(value: Any) -> Optional[Decimal]:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except Exception:  # noqa: BLE001
        return None
    return number if number.is_finite() else None


def _money(value: Decimal) -> float:
    return float(value.quantize(_CENT, rounding=ROUND_HALF_UP))


def _pct(numerator: Decimal, denominator: Decimal) -> Optional[float]:
    if denominator <= 0:
        return None
    return float((numerator / denominator * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def wilson_interval(wins: int, trials: int, z: float = 1.96) -> Optional[Dict[str, float]]:
    """95% Wilson score interval for a win rate - honest about small samples."""
    if trials <= 0:
        return None
    p = wins / trials
    denominator = 1 + z * z / trials
    centre = (p + z * z / (2 * trials)) / denominator
    margin = z * math.sqrt(p * (1 - p) / trials + z * z / (4 * trials * trials)) / denominator
    return {"low": round(max(0.0, centre - margin) * 100, 1), "high": round(min(1.0, centre + margin) * 100, 1)}


def evidence_from_returns(net_returns_percent: Sequence[Any], *, strategy: str, source: str) -> Dict[str, Any]:
    """Summarizes real per-trade net returns (%) for one strategy.

    Probability and expected value are only reported at or above
    MIN_TRADES_FOR_PROBABILITY trades. Below that the raw counts are still
    shown, but the plan says "not reliably estimated"."""
    returns: List[float] = []
    for value in net_returns_percent:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            returns.append(number)
    n = len(returns)
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r <= 0]
    summary: Dict[str, Any] = {
        "strategy": strategy,
        "source": source,
        "outcome_definition": "closed trade of this strategy with positive net P&L after fees, under its own stop/target exits",
        "sample_size": n,
        "win_count": len(wins),
        "win_probability_percent": None,
        "win_probability_interval": None,
        "avg_net_win_percent": round(sum(wins) / len(wins), 2) if wins else None,
        "avg_net_loss_percent": round(sum(losses) / len(losses), 2) if losses else None,
        "mean_net_return_percent": None,
        "mean_net_return_interval": None,
        "reliable": False,
        "note": "",
    }
    if n < MIN_TRADES_FOR_PROBABILITY:
        summary["note"] = (
            f"Probability not reliably estimated: {n} closed trade(s) of this strategy, "
            f"{MIN_TRADES_FOR_PROBABILITY} needed."
        )
        return summary
    mean = sum(returns) / n
    variance = sum((r - mean) ** 2 for r in returns) / (n - 1)
    standard_error = math.sqrt(variance / n)
    summary.update(
        {
            "win_probability_percent": round(len(wins) / n * 100, 1),
            "win_probability_interval": wilson_interval(len(wins), n),
            "mean_net_return_percent": round(mean, 2),
            "mean_net_return_interval": {"low": round(mean - 1.96 * standard_error, 2), "high": round(mean + 1.96 * standard_error, 2)},
            "reliable": True,
            "note": "Estimated from this deployment's own closed trades; past results can differ from future ones.",
        }
    )
    return summary


def build_trade_plan(
    *,
    ticker: str,
    strategy: Optional[str],
    direction: str,
    setup_score: Optional[float],
    entry_price: Any,
    stop_price: Any,
    target_price: Any,
    quote_price: Any,
    quote_age_seconds: Optional[float],
    plan_equity: Any,
    equity_source: str,
    existing_exposure: Any,
    available_buying_power: Any,
    sizing: Dict[str, Any],
    per_share_cost: Any = 0,
    short_permitted: bool = False,
    evidence: Optional[Dict[str, Any]] = None,
    atr: Any = None,
    quote_deferred: bool = False,
) -> Dict[str, Any]:
    """Builds one plan. `sizing` is app._compute_position_quantity's result
    for exactly these inputs; the plan never re-derives the quantity.

    quote_deferred=True is for previews, which must not spend a market-data
    call: a missing quote is then noted rather than rejected, because the
    scan re-checks the price immediately before any submission."""
    direction = "short" if direction == "short" else "long"
    entry, stop, target = _d(entry_price), _d(stop_price), _d(target_price)
    quote = _d(quote_price)
    equity = _d(plan_equity) or Decimal(0)
    exposure_before = _d(existing_exposure) or Decimal(0)
    buying_power = _d(available_buying_power)
    cost_per_share = _d(per_share_cost) or Decimal(0)
    quantity = int(sizing.get("quantity") or 0)

    reject: List[str] = []
    watch: List[str] = []
    limitations: List[str] = [
        "A stop order is not a guaranteed exit price - gaps and fast markets can fill it worse.",
        f"Adverse scenarios assume {STOP_SLIPPAGE_PERCENT}% stop slippage and an overnight gap of "
        f"{DEFAULT_GAP_MULTIPLE}x the stop distance (or 1 ATR beyond the stop when ATR is known).",
    ]

    if direction == "short" and not short_permitted:
        reject.append("Short selling is not permitted for this account.")
    if entry is None or entry <= 0:
        reject.append("No valid entry price.")
    stop_valid = entry is not None and stop is not None and stop > 0 and ((stop < entry) if direction == "long" else (stop > entry))
    if not stop_valid:
        reject.append("No valid protective stop on the correct side of entry - the plan cannot measure its risk.")
    target_valid = entry is not None and target is not None and target > 0 and ((target > entry) if direction == "long" else (target < entry))
    if not target_valid:
        watch.append("No valid profit target - the setup's exit plan is incomplete.")
    if quote is None and quote_deferred:
        limitations.append(
            "Preview: the current price was not fetched. It is re-checked immediately before any submission, "
            "and the trade is skipped if it has moved too far."
        )
    elif quote is None:
        reject.append("No current quote.")
    elif quote_age_seconds is None or quote_age_seconds > MAX_QUOTE_AGE_SECONDS:
        reject.append(f"Quote is stale or its age is unknown (limit {MAX_QUOTE_AGE_SECONDS}s).")
    if equity <= 0:
        reject.append("Account equity is zero or unknown.")

    risk_per_share = None
    if stop_valid and entry is not None:
        risk_per_share = ((entry - stop) if direction == "long" else (stop - entry)) + cost_per_share

    if not reject and quantity < 1:
        reason = sizing.get("reason") or "the smallest position (1 share) exceeds this account's limits"
        reject.append(f"Trade does not fit this account: {reason}.")

    numbers: Dict[str, Any] = {
        "quantity": quantity,
        "entry_price": float(entry) if entry is not None else None,
        "stop_price": float(stop) if stop is not None else None,
        "target_price": float(target) if target is not None else None,
        "current_quote": float(quote) if quote is not None else None,
        "quote_age_seconds": quote_age_seconds,
        "plan_equity": _money(equity),
        "equity_source": equity_source,
        "exposure": None,
        "allocation_percent": None,
        "planned_loss": None,
        "risk_percent": None,
        "leverage_after": None,
        "buying_power_used": None,
        "buying_power_after": None,
        "reward_if_target": None,
        "reward_to_risk": None,
        "binding_constraints": list(sizing.get("binding_constraints") or []),
        "constraints": dict(sizing.get("constraints") or {}),
    }
    scenarios: List[Dict[str, Any]] = []

    if not reject and quantity >= 1 and entry is not None and stop is not None and risk_per_share is not None:
        qty = Decimal(quantity)
        position_value = entry * qty
        planned_loss = risk_per_share * qty
        numbers.update(
            {
                "exposure": _money(position_value),
                "allocation_percent": _pct(position_value, equity),
                "planned_loss": _money(planned_loss),
                "risk_percent": _pct(planned_loss, equity),
                "leverage_after": float(((exposure_before + position_value) / equity).quantize(Decimal("0.01"))),
                "buying_power_used": _money(position_value),
                "buying_power_after": _money(buying_power - position_value) if buying_power is not None else None,
            }
        )
        if target_valid:
            reward_per_share = ((target - entry) if direction == "long" else (entry - target)) - cost_per_share
            numbers["reward_if_target"] = _money(reward_per_share * qty)
            reward_to_risk = reward_per_share / risk_per_share if risk_per_share > 0 else Decimal(0)
            numbers["reward_to_risk"] = float(reward_to_risk.quantize(Decimal("0.01")))
            if reward_to_risk < MIN_REWARD_TO_RISK:
                watch.append(f"Reward-to-risk {numbers['reward_to_risk']} is below {MIN_REWARD_TO_RISK} after costs.")

        stop_distance = (entry - stop) if direction == "long" else (stop - entry)
        slipped = stop * (Decimal(1) - STOP_SLIPPAGE_PERCENT / 100) if direction == "long" else stop * (Decimal(1) + STOP_SLIPPAGE_PERCENT / 100)
        atr_value = _d(atr)
        gap_distance = atr_value if atr_value is not None and atr_value > 0 else stop_distance * (DEFAULT_GAP_MULTIPLE - 1)
        gapped = max(stop - gap_distance, Decimal(0)) if direction == "long" else stop + gap_distance
        for label, exit_price in (
            ("Stop fills at the stop price", stop),
            (f"Stop fills {STOP_SLIPPAGE_PERCENT}% worse", slipped),
            ("Price gaps through the stop", gapped),
        ):
            per_share = ((entry - exit_price) if direction == "long" else (exit_price - entry)) + cost_per_share
            loss = per_share * qty
            scenarios.append({"scenario": label, "exit_price": _money(exit_price), "loss": _money(loss), "loss_percent_of_equity": _pct(loss, equity)})
        if direction == "short":
            limitations.append("A short position's loss is not capped by the stop - a gap up can exceed every scenario shown.")

    probability = evidence or evidence_from_returns([], strategy=strategy or "unknown", source="none")
    expected_value = None
    if probability.get("reliable") and numbers["exposure"] is not None:
        mean = probability["mean_net_return_percent"]
        interval = probability["mean_net_return_interval"]
        exposure_value = numbers["exposure"]
        expected_value = {
            "per_trade": round(exposure_value * mean / 100, 2),
            "interval": {"low": round(exposure_value * interval["low"] / 100, 2), "high": round(exposure_value * interval["high"] / 100, 2)},
            "note": "Mean net return of this strategy's closed trades applied to this position's size.",
        }
        if mean <= 0:
            watch.append("This strategy's closed trades have not shown a positive average net return.")

    if reject:
        decision = "reject"
    elif watch:
        decision = "watch"
    else:
        decision = "trade"

    return {
        "planner_version": PLANNER_VERSION,
        "ticker": ticker,
        "instrument": "EQUITY",
        "direction": direction,
        "strategy": strategy,
        "setup_score": setup_score,
        "setup_score_note": "Setup quality score from the strategy engine - not a probability of winning.",
        "decision": decision,
        "reasons": reject or watch or ["Levels, size and data checks pass. Approval is still required before any order."],
        "numbers": numbers,
        "adverse_scenarios": scenarios,
        "probability": probability,
        "expected_value": expected_value,
        "limitations": limitations,
    }


def compact_plan(plan: Dict[str, Any]) -> Dict[str, Any]:
    """What gets stored with each evaluated candidate: the decision and the
    numbers behind it, without the fixed explanatory text."""
    numbers = plan.get("numbers") or {}
    probability = plan.get("probability") or {}
    return {
        "planner_version": plan.get("planner_version"),
        "decision": plan.get("decision"),
        "reasons": plan.get("reasons"),
        "numbers": {key: value for key, value in numbers.items() if key != "constraints"},
        "adverse_scenarios": plan.get("adverse_scenarios"),
        "probability": {
            key: probability.get(key)
            for key in ("sample_size", "win_count", "win_probability_percent", "win_probability_interval", "mean_net_return_percent", "reliable")
        },
        "expected_value": plan.get("expected_value"),
    }
