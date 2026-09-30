"""Walk-forward evaluation of detectors on historical bars.

For every bar t, the engine sees only bars[:t+1] (scan_bars(as_of=t)). A
detection that CONFIRMS at bar t is traded as the rules define it:
  entry   = the NEXT bar's open (never the confirming bar's own close),
  stop    = the detection's invalidation level at t,
  target  = the detection's target at t,
  exit    = stop or target, whichever the bars reach first - the stop is
            assumed first when both fall inside one bar; a gap through
            either exits at that bar's open; otherwise the close
            `max_hold` bars later.
Net R = (exit - entry) / (entry - stop) in the trade's direction, minus a
round-trip cost of `cost_bps` per side expressed in R.

Split: each symbol's trades before `split_fraction` of its history are
in-sample, the rest out-of-sample. Detector rules were written before any of
this data was examined; the split still guards against later tuning. The
validation status uses out-of-sample results only (evidence.status_from)."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from . import evidence, model
from .bars import Bars
from .engine import scan_bars

DEFAULT_COST_BPS = 5.0
DEFAULT_MAX_HOLD = 20
BACKTEST_FILE = evidence.BACKTEST_FILE


def simulate(bars: Bars, detection: Dict[str, Any], *, max_hold: int, cost_bps: float) -> Optional[Dict[str, Any]]:
    t = detection["confirmed_index"]
    n = len(bars)
    if t is None or t + 1 >= n or detection["direction"] not in ("long", "short"):
        return None
    s = 1.0 if detection["direction"] == "long" else -1.0
    stop, target = detection["levels"]["invalidation"], detection["levels"]["target"]
    if stop is None or target is None:
        return None
    entry = float(bars.o[t + 1])
    risk = s * (entry - stop)
    if risk <= 0 or s * (target - entry) <= 0:
        return {"skipped": "gapped beyond the stop or target at entry"}
    exit_price, reason, exit_index = None, None, None
    last = min(t + max_hold, n - 1)
    for j in range(t + 1, last + 1):
        low, high, open_ = float(bars.l[j]), float(bars.h[j]), float(bars.o[j])
        adverse, favorable = (low, high) if s > 0 else (high, low)
        if s * (adverse - stop) <= 0:
            exit_price = stop if j == t + 1 or s * (open_ - stop) > 0 else open_
            reason, exit_index = "stop", j
            break
        if s * (favorable - target) >= 0:
            exit_price = target if j == t + 1 or s * (open_ - target) < 0 else open_
            reason, exit_index = "target", j
            break
    if exit_price is None:
        if t + max_hold > n - 1:
            return None  # unresolved at the end of the data - excluded, not guessed
        exit_price, reason, exit_index = float(bars.c[last]), "time", last
    gross = s * (exit_price - entry) / risk
    cost = 2 * cost_bps / 10_000 * entry / risk
    return {
        "detector_id": detection["detector_id"], "version": detection["version"], "timeframe": bars.timeframe,
        "symbol": bars.symbol, "confirmed_t": bars.t[t], "index": t, "direction": detection["direction"],
        "entry": round(entry, 4), "stop": stop, "target": target, "exit": round(exit_price, 4), "exit_reason": reason,
        "bars_held": exit_index - t, "gross_r": round(gross, 4), "cost_r": round(cost, 4), "net_r": round(gross - cost, 4),
        "regime": (detection.get("regime") or {}).get("trend"),
    }


def walk_forward(bars: Bars, *, start: int = 210, max_hold: int = DEFAULT_MAX_HOLD, cost_bps: float = DEFAULT_COST_BPS,
                 detector_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    trades: List[Dict[str, Any]] = []
    seen = set()
    for t in range(min(start, len(bars) - 1), len(bars) - 1):
        for detection in scan_bars(bars, as_of=t, detector_ids=detector_ids)["detections"]:
            if detection["state"] != model.CONFIRMED or detection["confirmed_index"] != t or detection["key"] in seen:
                continue
            seen.add(detection["key"])
            outcome = simulate(bars, detection, max_hold=max_hold, cost_bps=cost_bps)
            if outcome and "skipped" not in outcome:
                outcome["position"] = t / max(1, len(bars) - 1)
                trades.append(outcome)
    return trades


def aggregate(trades: Iterable[Dict[str, Any]], *, split_fraction: float = 0.6) -> Dict[str, Dict[str, Any]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for trade in trades:
        grouped.setdefault(evidence.key(trade["detector_id"], trade["version"], trade["timeframe"]), []).append(trade)
    out = {}
    for key, rows in grouped.items():
        ins = [r["net_r"] for r in rows if r["position"] < split_fraction]
        oos = [r["net_r"] for r in rows if r["position"] >= split_fraction]
        by_regime: Dict[str, List[float]] = {}
        for r in rows:
            if r["position"] >= split_fraction:
                by_regime.setdefault(r.get("regime") or "unknown", []).append(r["net_r"])
        out[key] = {
            "in_sample": evidence.summarize_r(ins),
            "out_of_sample": evidence.summarize_r(oos),
            "by_regime": {regime: evidence.summarize_r(values) for regime, values in by_regime.items()},
            "exit_reasons": {reason: sum(1 for r in rows if r["exit_reason"] == reason) for reason in ("stop", "target", "time")},
            "symbols": len({r["symbol"] for r in rows}),
        }
        out[key]["status"] = evidence.status_from(out[key]["out_of_sample"], {"n": 0})
    return out


def _code_version() -> Optional[str]:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5,
                              cwd=str(Path(__file__).resolve().parent)).stdout.strip() or os.environ.get("RENDER_GIT_COMMIT", "")[:7] or None
    except Exception:  # noqa: BLE001
        return os.environ.get("RENDER_GIT_COMMIT", "")[:7] or None


def run(symbols: List[str], *, timeframe: str = "1d", fetch: Callable[[List[str], str], Dict[str, Bars]],
        max_hold: int = DEFAULT_MAX_HOLD, cost_bps: float = DEFAULT_COST_BPS, progress: Optional[Callable[[str], None]] = None,
        output: Optional[Path] = BACKTEST_FILE) -> Dict[str, Any]:
    """Evaluates every detector on `symbols` and writes the evidence file."""
    bars_by_symbol = fetch(symbols, timeframe)
    trades: List[Dict[str, Any]] = []
    for k, symbol in enumerate(sorted(bars_by_symbol)):
        if progress:
            progress(f"{k + 1}/{len(bars_by_symbol)} {symbol}")
        trades.extend(walk_forward(bars_by_symbol[symbol], max_hold=max_hold, cost_bps=cost_bps))
    periods = [(b.t[0], b.t[-1]) for b in bars_by_symbol.values() if len(b)]
    snapshot = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "code_version": _code_version(),
        "data_source": f"Alpaca {os.environ.get('ALPACA_DATA_FEED', 'iex')} {timeframe} bars (raw, unadjusted)",
        "universe_size": len(bars_by_symbol),
        "symbols": sorted(bars_by_symbol),
        "period": {"from": min(p[0] for p in periods), "to": max(p[1] for p in periods)} if periods else None,
        "costs": {"bps_per_side": cost_bps, "max_hold_bars": max_hold, "entry": "next bar open"},
        "trades": len(trades),
        "entries": aggregate(trades),
        "limitations": [
            "Raw (split-unadjusted) bars: a split inside the window can create false breaks.",
            "IEX feed: volume is IEX-only; highs/lows may differ slightly from consolidated prints.",
            "Stop assumed first when stop and target fall inside the same bar; fills at levels assume no slippage beyond the cost allowance.",
            "Universe is the app's own scan list (large, liquid US stocks) - results may not generalize to others.",
        ],
    }
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        tmp = output.with_name(output.name + ".tmp")
        tmp.write_text(json.dumps(snapshot, indent=1), encoding="utf-8")
        os.replace(tmp, output)
    return snapshot
