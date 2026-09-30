"""From detections to opportunities and decisions.

* Detections on the same timeframe whose bar windows overlap by half (or that
  share a swing point) are ONE opportunity. Other names for the same price
  action are listed as "also matches" and are never counted as independent
  confirmation - nothing here adds up detections into a score.
* Opposite directions inside one opportunity, or across timeframes, are
  reported as conflicts.
* Decision per opportunity: QUALIFY only when the primary setup is confirmed,
  an entry is still actionable at the current price, the detector version is
  VALIDATED for that timeframe, its regime applies and liquidity is adequate.
  Otherwise WATCH (could become a trade) or REJECT (no trade here), always
  with the reasons. No confidence percentages are produced.
* At most one trade candidate per symbol."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from . import evidence as evidence_store
from . import model

QUALIFY = "qualify"
WATCH = "watch"
REJECT = "reject"

MIN_REWARD_TO_RISK = 1.5
MAX_CHASE_ATR = 1.0
MIN_DOLLAR_VOLUME = 5_000_000.0        # consolidated (SIP) volume
MIN_IEX_DOLLAR_VOLUME = 150_000.0     # IEX carries roughly 2-3% of volume; ~$5-7M consolidated
LIMITATIONS = (
    "No earnings or corporate-event calendar is connected; event risk is not checked.",
    "Market data is Alpaca's IEX feed: volume is IEX-only (used as ratios) and highs/lows may differ slightly from consolidated prints.",
)


def _window(d: Dict[str, Any]) -> tuple:
    end = max(d["end_index"], d.get("confirmed_index") or d["end_index"])
    return d["start_index"], end


def _overlaps(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    (a0, a1), (b0, b1) = _window(a), _window(b)
    shared = min(a1, b1) - max(a0, b0)
    shorter = max(1, min(a1 - a0, b1 - b0))
    if shared >= 0.5 * shorter:
        return True
    return bool({p["index"] for p in a["points"]} & {p["index"] for p in b["points"]})


def group(detections: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    live = [d for d in detections if d["state"] in model.LIVE_STATES]
    groups: List[List[Dict[str, Any]]] = []
    for d in sorted(live, key=lambda d: (d["timeframe"], d["start_index"])):
        home = next((g for g in groups if g[0]["timeframe"] == d["timeframe"] and any(_overlaps(d, m) for m in g)), None)
        if home is None:
            groups.append([d])
        else:
            home.append(d)
    return groups


def _primary(members: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Most advanced state, then most specific rule. When members disagree on
    direction, a direction-neutral detection (e.g. the range itself) leads,
    since it describes the price action without taking a side."""
    directions = {m["direction"] for m in members} - {"both"}
    neutral = [m for m in members if m["direction"] == "both"]
    if len(directions) > 1 and neutral:
        members = neutral
    return max(members, key=lambda d: (model.STATE_RANK[d["state"]], d["specificity"], d.get("confirmed_index") or -1, d["end_index"]))


def _sign(direction: str) -> float:
    return 1.0 if direction == "long" else -1.0


def _entry_check(d: Dict[str, Any]) -> Dict[str, Any]:
    lv = d["levels"]
    trigger, invalidation, target, atr, close = lv["trigger_now"], lv["invalidation"], lv["target"], lv["atr"], lv["last_close"]
    if d["direction"] == "both" or trigger is None or invalidation is None:
        return {"actionable": False, "reason": "direction not decided yet"}
    s = _sign(d["direction"])
    if d["state"] == model.CONFIRMED:
        entry = close
        risk = s * (entry - invalidation)
        reward = s * (target - entry) if target is not None else None
        chase = s * (entry - (lv["trigger"] or trigger)) / atr if atr else None
        out = {"entry_reference": entry, "risk_per_unit": round(risk, 4) if risk else risk,
               "reward_to_risk": round(reward / risk, 2) if reward is not None and risk and risk > 0 else None,
               "extension_atr": round(chase, 2) if chase is not None else None}
        if risk is None or risk <= 0:
            return {**out, "actionable": False, "reason": "price is already beyond the invalidation level"}
        if reward is None or reward <= 0:
            return {**out, "actionable": False, "reason": "price is already at or beyond the target"}
        if chase is not None and chase > MAX_CHASE_ATR:
            return {**out, "actionable": False, "reason": f"price has run {chase:.1f} ATR past the trigger - entering now would be chasing"}
        if reward / risk < MIN_REWARD_TO_RISK:
            return {**out, "actionable": False, "reason": f"reward-to-risk at the current price is {reward / risk:.2f} (needs {MIN_REWARD_TO_RISK})"}
        return {**out, "actionable": True, "reason": "entry still available at the current price"}
    risk = s * (trigger - invalidation)
    reward = s * (target - trigger) if target is not None else None
    return {"entry_reference": trigger, "risk_per_unit": round(risk, 4),
            "reward_to_risk": round(reward / risk, 2) if reward is not None and risk > 0 else None,
            "actionable": False, "reason": "not confirmed yet - the entry is the trigger"}


def build_opportunity(members: List[Dict[str, Any]], *, liquidity: Optional[Dict[str, Any]] = None,
                      lookup: Callable[[str, str, str], Dict[str, Any]] = evidence_store.lookup) -> Dict[str, Any]:
    primary = _primary(members)
    directions = {m["direction"] for m in members} - {"both"}
    conflict = len(directions) > 1
    also = [m for m in members if m is not primary]
    ev = lookup(primary["detector_id"], primary["version"], primary["timeframe"])
    entry = _entry_check(primary)
    spec = model.spec_for(primary["detector_id"])

    counter: List[str] = []
    if conflict:
        opposing = ", ".join(f"{m['name']} ({m['direction']})" for m in members if m["direction"] not in ("both", primary["direction"]))
        counter.append(f"conflicting detectors on the same price action: {opposing}")
    if liquidity and not liquidity.get("ok", True):
        counter.append(liquidity.get("reason") or "insufficient liquidity")
    if primary["state"] == model.CONFIRMED and not entry["actionable"]:
        counter.append(entry["reason"])
    if not primary["regime_applicable"]:
        counter.append(f"regime is {primary['regime'].get('trend')}, outside where this setup applies ({', '.join(spec.regimes) if spec else '?'})")
    if primary["provisional"]:
        counter.append("formation relies on a provisional swing point")
    oos = ev["out_of_sample"].get("n") or 0
    if ev["status"] != model.VALIDATED:
        counter.append(f"setup version not validated for execution (status: {ev['status']}; {oos} out-of-sample trades)")
    counter.extend(primary["counter_evidence"])
    counter.append(LIMITATIONS[0])

    support = list(primary["evidence"])
    if primary["regime_applicable"]:
        support.append(f"regime ({primary['regime'].get('trend')}) is one this setup applies to")
    if ev["out_of_sample"].get("n"):
        o = ev["out_of_sample"]
        support.append(f"out-of-sample: {o['n']} trades, mean {o.get('mean_r')}R after costs, win rate {o.get('win_rate', 0):.0%}")

    if conflict:
        decision, why = WATCH, "detectors disagree on direction"
    elif liquidity and not liquidity.get("ok", True):
        decision, why = REJECT, "liquidity below the minimum"
    elif primary["direction"] == "both":
        decision, why = WATCH, "waiting for the formation to pick a direction"
    elif primary["state"] != model.CONFIRMED:
        decision, why = WATCH, f"{primary['state'].replace('_', ' ')} - not confirmed"
    elif not entry["actionable"]:
        extended = (entry.get("extension_atr") or 0) > MAX_CHASE_ATR
        decision, why = (WATCH if extended else REJECT), entry["reason"]
    elif not primary["regime_applicable"]:
        decision, why = WATCH, "regime does not match the setup"
    elif ev["status"] != model.VALIDATED:
        decision, why = WATCH, "research only - this setup version has not passed out-of-sample and forward validation"
    else:
        decision, why = QUALIFY, "confirmed, actionable, validated, regime and liquidity pass"

    lv = dict(primary["levels"])
    two_sided = primary["direction"] == "both"
    if two_sided:  # no side is taken yet, so there is no invalidation or target yet
        lv["invalidation"] = lv["target"] = None
    expiry = primary.get("bars_until_expiry")
    return {
        "symbol": primary["symbol"],
        "timeframe": primary["timeframe"],
        "direction": primary["direction"],
        "setup": {"id": primary["detector_id"], "name": primary["name"], "version": primary["version"], "family": primary["family"],
                  "definition": spec.structure if spec else None},
        "state": primary["state"],
        "state_reason": primary["state_reason"],
        "as_of": primary["as_of"],
        "levels": lv,
        "entry": entry,
        "entry_conditions": (primary["trigger_rule"] if two_sided else
                             f"{primary['trigger_rule']} (trigger now {lv['trigger_now']}); invalid on a close beyond {lv['invalidation']}"),
        "expires_in_bars": expiry,
        "evidence": support,
        "counter_evidence": counter,
        "strongest_counterargument": next((c for c in counter if c != why), None),
        "historical": ev,
        "decision": decision,
        "decision_reason": why,
        "conflict": conflict,
        "also_matches": [{"id": m["detector_id"], "name": m["name"], "state": m["state"], "direction": m["direction"]} for m in also],
        "also_matches_note": "Other names for the same price action - not independent confirmation." if also else None,
        "primary_detection": primary,
        "explanation": explain(primary, decision, why, entry),
    }


def explain(d: Dict[str, Any], decision: str, why: str, entry: Dict[str, Any]) -> str:
    lv = d["levels"]
    parts = [f"{d['symbol']} {d['timeframe']}: {d['name']} ({d['direction']}) is {d['state'].replace('_', ' ')} - {d['state_reason']}."]
    if d["direction"] == "both":
        parts.append(f"It picks a direction on a {d['trigger_rule']}; invalidation and target are set by that break.")
    else:
        parts.append(f"It confirms on {d['trigger_rule']} (level now {lv['trigger_now']}) and is invalidated by a close beyond {lv['invalidation']}.")
    if lv.get("target") is not None:
        parts.append(f"The rule-based target is {lv['target']}.")
    if entry.get("reward_to_risk") is not None:
        parts.append(f"Reward-to-risk from {'the current price' if d['state'] == model.CONFIRMED else 'the trigger'} is {entry['reward_to_risk']}.")
    parts.append(f"Decision: {decision} - {why}.")
    parts.append("The chart annotations show the rules are met; they do not show the trade will work.")
    return " ".join(parts)


def evaluate_symbol(scans: List[Dict[str, Any]], *, liquidity: Optional[Dict[str, Any]] = None,
                    lookup: Callable[[str, str, str], Dict[str, Any]] = evidence_store.lookup) -> Dict[str, Any]:
    """scans: scan_bars() results for ONE symbol, any number of timeframes."""
    detections = [d for scan in scans for d in scan["detections"]]
    opportunities = [build_opportunity(g, liquidity=liquidity, lookup=lookup) for g in group(detections)]
    directional = {o["direction"] for o in opportunities if o["direction"] != "both" and not o["conflict"]}
    if len(directional) > 1:
        for o in opportunities:
            others = ", ".join(sorted({f"{x['setup']['name']} {x['timeframe']} ({x['direction']})" for x in opportunities
                                        if x["direction"] not in ("both", o["direction"])}))
            o["counter_evidence"].insert(0, f"timeframe conflict: {others}")
            o["strongest_counterargument"] = o["counter_evidence"][0]
            if o["decision"] == QUALIFY:
                o["decision"], o["decision_reason"] = WATCH, "timeframes disagree on direction"
    order = {QUALIFY: 0, WATCH: 1, REJECT: 2}
    opportunities.sort(key=lambda o: (order[o["decision"]], -((o["historical"]["out_of_sample"].get("mean_r_lower_90") or -99) if o["decision"] == QUALIFY else 0),
                                      -model.STATE_RANK[o["state"]], -(o["entry"].get("reward_to_risk") or 0)))
    inactive = [d for d in detections if d["state"] not in model.LIVE_STATES]
    symbol = scans[0]["symbol"] if scans else None
    qualified = [o for o in opportunities if o["decision"] == QUALIFY]
    return {
        "symbol": symbol,
        "timeframes": [scan["timeframe"] for scan in scans],
        "regimes": {scan["timeframe"]: scan.get("regime") for scan in scans},
        "liquidity": liquidity,
        "opportunities": opportunities,
        "recently_ended": [{"id": d["detector_id"], "name": d["name"], "timeframe": d["timeframe"], "state": d["state"],
                            "reason": d["state_reason"]} for d in inactive],
        "errors": [e for scan in scans for e in scan.get("errors", [])],
        "trade_candidate": qualified[0] if qualified else None,
        "summary": (f"{len(qualified)} qualifying setup(s)" if qualified else
                    "no qualifying trade" + (f" - {len(opportunities)} setup(s) to watch" if opportunities else " - no defined setup on this chart")),
    }


def liquidity_from_daily(bars, minimum: Optional[float] = None) -> Dict[str, Any]:
    """20-day mean dollar volume. On the IEX feed volume is only IEX's share of
    the market, so the floor is scaled down and labeled as an IEX figure."""
    import os

    feed = (os.environ.get("ALPACA_DATA_FEED", "iex").strip() or "iex").lower()
    if minimum is None:
        minimum = MIN_DOLLAR_VOLUME if feed == "sip" else MIN_IEX_DOLLAR_VOLUME
    if bars is None or len(bars) < 5:
        return {"ok": True, "avg_dollar_volume": None, "feed": feed, "reason": "liquidity unknown (too few daily bars)"}
    n = min(20, len(bars))
    value = float((bars.c[-n:] * bars.v[-n:]).mean())
    ok = value >= minimum
    label = "IEX-only " if feed != "sip" else ""
    return {"ok": ok, "avg_dollar_volume": round(value), "minimum": minimum, "feed": feed,
            "reason": None if ok else f"average daily {label}dollar volume ${value:,.0f} is below ${minimum:,.0f}"}
