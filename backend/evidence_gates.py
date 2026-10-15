"""Whether a named strategy may open a paper trade.

A pass is an out-of-sample result after fees and slippage. The confidence
floor is chosen on the older part of the sample only, then frozen and
applied to the newer part. The newer part is never used to pick that floor.

Live trading is not changed here. When live is armed this module does not
block, so the existing live path (including the go-live gate) stays as it
is. Paper scans call block_reason before an order is sent.

PLUTO_EVIDENCE_GATES=0 turns the backtest gate off. PLUTO_REGIME_FILTER=0
turns the choppy-regime block off. Both default on for paper.
"""

from __future__ import annotations

import json
import math
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("PLUTO_DATA_DIR", str(BASE_DIR / "data"))).resolve()
GATE_FILE = DATA_DIR / "research" / "strategy_oos_gate.json"

MIN_OOS = 30
MIN_TUNE = 8
SPLIT_FRACTION = 0.6
COST_BPS_PER_SIDE = 5.0
SLIPPAGE_BPS_PER_SIDE = 5.0
THRESHOLDS = (55, 60, 65, 70, 75, 80)
LOWER_BOUND_Z = 1.645

# Trend-following names are skipped in a choppy range. Mean-reversion names
# are skipped in a clean trend. Anything not listed is not blocked: an
# unknown label is not treated as unsuitable.
TREND_STRATEGIES = frozenset({
    "Breakout",
    "Momentum",
    "Trend Continuation",
    "Gap and Go",
    "Earnings Momentum",
    "Options Momentum",
    "VWAP Bounce",
    "Pullback",
    "Breakdown Continuation",
    "Breakout Continuation",
})
MEAN_REVERSION = frozenset({
    "Mean Reversion",
    "Support Bounce",
    "Resistance Rejection",
})


def round_trip_drag_percent(
    cost_bps: float = COST_BPS_PER_SIDE,
    slippage_bps: float = SLIPPAGE_BPS_PER_SIDE,
) -> float:
    """Round-trip cost plus slippage, in percentage points of price."""
    return 2.0 * (float(cost_bps) + float(slippage_bps)) / 100.0


def net_return_percent(gross_percent: float, drag: Optional[float] = None) -> float:
    return float(gross_percent) - (round_trip_drag_percent() if drag is None else drag)


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _live_armed() -> bool:
    try:
        from integrations.webull import is_live_trading_armed
    except ImportError:  # pragma: no cover
        from .integrations.webull import is_live_trading_armed  # type: ignore
    return bool(is_live_trading_armed())


def evidence_gates_enabled() -> bool:
    if _live_armed():
        return False
    return _flag("PLUTO_EVIDENCE_GATES", True)


def regime_filter_enabled() -> bool:
    if _live_armed():
        return False
    return _flag("PLUTO_REGIME_FILTER", True)


def summarize(values: Sequence[float]) -> Dict[str, Any]:
    rows = [float(value) for value in values]
    n = len(rows)
    if n == 0:
        return {"n": 0, "mean": None, "lower_95": None, "win_rate": None}
    mean = sum(rows) / n
    sd = math.sqrt(sum((value - mean) ** 2 for value in rows) / (n - 1)) if n > 1 else 0.0
    lower = mean - LOWER_BOUND_Z * sd / math.sqrt(n) if n > 1 else None
    wins = sum(1 for value in rows if value > 0)
    return {
        "n": n,
        "mean": round(mean, 4),
        "lower_95": None if lower is None else round(lower, 4),
        "win_rate": round(wins / n, 4),
    }


def _passes(stats: Dict[str, Any]) -> Tuple[bool, str]:
    n = int(stats.get("n") or 0)
    if n < MIN_OOS:
        return False, f"only {n} out-of-sample trades after costs (need {MIN_OOS})"
    lower = stats.get("lower_95")
    mean = stats.get("mean")
    if lower is None or lower <= 0:
        if (mean or 0) <= 0:
            return False, f"out-of-sample mean return after costs is {mean}% (not above zero)"
        return False, (
            f"out-of-sample mean is {mean}% but the lower bound is {lower}% "
            "(not reliably above zero after costs)"
        )
    return True, f"out-of-sample {n} trades, mean {mean}% after costs, lower bound {lower}%"


def split_older_newer(
    trades: Iterable[Dict[str, Any]],
    fraction: float = SPLIT_FRACTION,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Older trades are in-sample. Newer trades start strictly after the cutoff date."""
    ordered = sorted((trade for trade in trades if trade.get("entry_date")), key=lambda trade: str(trade["entry_date"]))
    if not ordered:
        return [], []
    dates = [str(trade["entry_date"]) for trade in ordered]
    cutoff = dates[min(len(dates) - 1, max(0, int((len(dates) - 1) * fraction)))]
    older = [trade for trade in ordered if str(trade["entry_date"]) <= cutoff]
    newer = [trade for trade in ordered if str(trade["entry_date"]) > cutoff]
    if not newer and len(ordered) > 1:
        # A single-day sample has no unseen period. Keep the last day out of sample
        # only when an earlier day exists; otherwise the caller fails closed on n.
        last = dates[-1]
        older = [trade for trade in ordered if str(trade["entry_date"]) < last]
        newer = [trade for trade in ordered if str(trade["entry_date"]) == last]
    return older, newer


def choose_threshold(trades: Sequence[Dict[str, Any]]) -> Optional[int]:
    """Highest-expectancy confidence floor that is profitable on THESE trades only."""
    best: Optional[Tuple[float, int]] = None
    drag = round_trip_drag_percent()
    for threshold in THRESHOLDS:
        subset = [trade for trade in trades if int(trade.get("confidence") or 0) >= threshold]
        if len(subset) < MIN_TUNE:
            continue
        nets = [net_return_percent(float(trade.get("pnl_percent") or 0), drag) for trade in subset]
        mean = sum(nets) / len(nets)
        if mean <= 0:
            continue
        if best is None or mean > best[0]:
            best = (mean, threshold)
    return None if best is None else best[1]


def evaluate_trades(trades: Iterable[Dict[str, Any]], strategy: str = "") -> Dict[str, Any]:
    """Tune on the older period, judge only the newer period, after costs."""
    rows = list(trades)
    older, newer = split_older_newer(rows)
    drag = round_trip_drag_percent()
    threshold = choose_threshold(older)
    costs = {
        "cost_bps_per_side": COST_BPS_PER_SIDE,
        "slippage_bps_per_side": SLIPPAGE_BPS_PER_SIDE,
        "round_trip_drag_percent": round(drag, 4),
    }
    in_sample = summarize(
        [net_return_percent(float(trade.get("pnl_percent") or 0), drag) for trade in older if threshold is not None and int(trade.get("confidence") or 0) >= threshold]
    ) if threshold is not None else summarize([])
    if threshold is None:
        verdict = {
            "strategy": strategy,
            "passed": False,
            "reason": "no confidence floor was profitable on the older in-sample period after costs",
            "frozen_threshold": None,
            "in_sample": in_sample,
            "out_of_sample": summarize([]),
            "costs": costs,
        }
        return verdict
    oos_rows = [trade for trade in newer if int(trade.get("confidence") or 0) >= threshold]
    oos = summarize([net_return_percent(float(trade.get("pnl_percent") or 0), drag) for trade in oos_rows])
    passed, reason = _passes(oos)
    return {
        "strategy": strategy,
        "passed": passed,
        "reason": reason,
        "frozen_threshold": threshold,
        "in_sample": in_sample,
        "out_of_sample": oos,
        "costs": costs,
    }


def evaluate_fixed_rules(trades: Iterable[Dict[str, Any]], strategy: str = "") -> Dict[str, Any]:
    """User strategies have no confidence score. The rules stay fixed; only the newer period can pass."""
    older, newer = split_older_newer(list(trades))
    drag = round_trip_drag_percent()
    oos = summarize([net_return_percent(float(trade.get("pnl_percent") or 0), drag) for trade in newer])
    passed, reason = _passes(oos)
    return {
        "strategy": strategy,
        "passed": passed,
        "reason": reason,
        "frozen_threshold": None,
        "in_sample": summarize([net_return_percent(float(trade.get("pnl_percent") or 0), drag) for trade in older]),
        "out_of_sample": oos,
        "costs": {
            "cost_bps_per_side": COST_BPS_PER_SIDE,
            "slippage_bps_per_side": SLIPPAGE_BPS_PER_SIDE,
            "round_trip_drag_percent": round(drag, 4),
        },
    }


def _write_gate(payload: Dict[str, Any]) -> None:
    GATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = GATE_FILE.with_name(f"{GATE_FILE.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:6]}")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, GATE_FILE)


def load_gate() -> Dict[str, Any]:
    try:
        data = json.loads(GATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"strategies": {}}
    return data if isinstance(data, dict) else {"strategies": {}}


def publish_from_trades(trades: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Stores the paper-trade gate. Called from calibration, not the monitor loop."""
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for trade in trades:
        name = str(trade.get("strategy") or "").strip()
        if name:
            grouped.setdefault(name, []).append(trade)
    current = load_gate()
    strategies = dict(current.get("strategies") or {})
    for name, rows in grouped.items():
        strategies[name] = evaluate_trades(rows, name)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "split_fraction": SPLIT_FRACTION,
        "strategies": strategies,
    }
    _write_gate(payload)
    return payload


def strategy_allowed(strategy: str) -> Tuple[bool, str]:
    name = str(strategy or "").strip()
    if not name:
        return False, "no strategy name, so there is no out-of-sample result to trust"
    verdict = (load_gate().get("strategies") or {}).get(name)
    if not isinstance(verdict, dict):
        return False, f"{name} has no stored out-of-sample backtest after costs"
    if verdict.get("passed"):
        return True, str(verdict.get("reason") or "out-of-sample backtest passed")
    return False, str(verdict.get("reason") or f"{name} did not pass the out-of-sample backtest")


def regime_from_context(context: Optional[Dict[str, Any]]) -> str:
    """uptrend, downtrend, range, or unknown. Unknown never blocks."""
    if not isinstance(context, dict):
        return "unknown"
    try:
        ema9 = float(context.get("ema_9"))
        ema20 = float(context.get("ema_20"))
        ema50 = float(context.get("ema_50"))
    except (TypeError, ValueError):
        return "unknown"
    if ema9 > ema20 > ema50:
        return "uptrend"
    if ema9 < ema20 < ema50:
        return "downtrend"
    return "range"


def regime_block_reason(strategy: str, recommendation: str, regime: str) -> Optional[str]:
    if regime in {"", "unknown"}:
        return None
    name = str(strategy or "")
    side = str(recommendation or "").upper()
    if regime == "range" and name in TREND_STRATEGIES:
        return f"{name} is a trend setup and the regime is choppy ({regime})"
    if regime in {"uptrend", "downtrend"} and name in MEAN_REVERSION:
        return f"{name} is a mean-reversion setup and the regime is a trend ({regime})"
    if name in TREND_STRATEGIES and regime == "downtrend" and side == "CALL":
        return f"{name} long is unsuitable in a downtrend"
    if name in TREND_STRATEGIES and regime == "uptrend" and side == "PUT":
        return f"{name} short is unsuitable in an uptrend"
    return None


def block_reason(opportunity: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    """(reason, skip_category) when paper trading must skip. None when the order may proceed.

    Live-armed processes return None so this module does not change live behavior.
    """
    if _live_armed():
        return None
    if evidence_gates_enabled():
        allowed, why = strategy_allowed(str(opportunity.get("strategy") or ""))
        if not allowed:
            return why, "backtest_gate"
    context = opportunity.get("market_context") if isinstance(opportunity.get("market_context"), dict) else {}
    regime = regime_from_context(context)
    if regime_filter_enabled():
        why = regime_block_reason(
            str(opportunity.get("strategy") or ""),
            str(opportunity.get("recommendation") or ""),
            regime,
        )
        if why:
            return why, "skipped_by_regime"
    try:
        import ml_confidence
        why = ml_confidence.filter_reason(opportunity)
        if why:
            return why, "ml_confidence"
    except Exception:
        return None
    return None


def public_summary() -> Dict[str, Any]:
    data = load_gate()
    rows = []
    for name, verdict in sorted((data.get("strategies") or {}).items()):
        if not isinstance(verdict, dict):
            continue
        rows.append({
            "strategy": name,
            "passed": bool(verdict.get("passed")),
            "reason": verdict.get("reason"),
            "frozen_threshold": verdict.get("frozen_threshold"),
            "out_of_sample": verdict.get("out_of_sample") or {},
            "in_sample": verdict.get("in_sample") or {},
        })
    return {
        "generated_at": data.get("generated_at"),
        "gates_enabled": evidence_gates_enabled(),
        "regime_filter_enabled": regime_filter_enabled(),
        "live_armed": _live_armed(),
        "strategies": rows,
    }


def setup_summary() -> List[Dict[str, Any]]:
    """Pass/fail for setup detectors from the stored walk-forward file. Display only."""
    try:
        from setups import evidence, model
    except ImportError:  # pragma: no cover
        from .setups import evidence, model  # type: ignore
    rows = []
    for snapshot in evidence.snapshots():
        for key, entry in (snapshot.get("entries") or {}).items():
            if not isinstance(entry, dict):
                continue
            looked = None
            parts = str(key).split("|")
            ident = parts[0].split("@") if parts else []
            if len(ident) == 2 and len(parts) == 2:
                looked = evidence.lookup(ident[0], ident[1], parts[1])
            status = (looked or {}).get("status") or entry.get("status") or model.RESEARCH
            oos = (looked or entry).get("out_of_sample") or {}
            passed = status in {model.BACKTEST_SUPPORTED, model.VALIDATED}
            if status == model.VALIDATED:
                why = "Pass: out-of-sample and forward evidence both support this version."
            elif status == model.BACKTEST_SUPPORTED:
                why = "Out-of-sample after costs supports it. Forward evidence is not finished, so it is not validated for execution."
            elif int(oos.get("n") or 0) < evidence.MIN_OUT_OF_SAMPLE:
                why = f"Fail: only {int(oos.get('n') or 0)} out-of-sample trades (need {evidence.MIN_OUT_OF_SAMPLE})."
            else:
                why = (
                    f"Fail: out-of-sample mean {oos.get('mean_r')}R, lower bound {oos.get('mean_r_lower_95')}R "
                    "after costs."
                )
            rows.append({"key": key, "status": status, "passed": passed, "reason": why, "out_of_sample": oos})
    return rows
