"""Forward (out-of-time) evidence: setups recorded the day they confirm, and
scored only after later bars exist.

Each run (daily, after the close):
  1. scan every symbol's COMPLETED daily bars; a detection that confirmed on
     the newest completed bar is recorded with its levels as they stood then.
     Only same-day confirmations are recorded - never backfilled - so a missed
     day is simply missed.
  2. every open record whose confirmation bar is still in the fetched history
     is re-simulated with validation.simulate (next-bar-open entry, the
     recorded stop/target, 20-bar limit, costs); once resolved it is final.
  3. resolved net R per detector version and timeframe is written to
     evidence.FORWARD_FILE, which evidence.status_from requires (>= 20 trades,
     mean > 0) before any setup can be VALIDATED.

Nothing here places orders. Read-only market data."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List

from . import evidence, model, validation
from .bars import Bars
from .engine import scan_bars

LOG_FILE = evidence.DATA_DIR / "research" / "setup_forward_log.json"
MAX_HOLD = validation.DEFAULT_MAX_HOLD
COST_BPS = validation.DEFAULT_COST_BPS


def _read(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:6]}")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def run(bars_by_symbol: Dict[str, Bars], *, completed: Callable[[Bars], Bars], now: str = "") -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc).isoformat()
    log = _read(LOG_FILE) or {"records": []}
    records: List[Dict[str, Any]] = log.get("records") or []
    known = {r["key"] for r in records}
    added = resolved = 0

    closed_by_symbol = {symbol: completed(bars) for symbol, bars in bars_by_symbol.items()}
    for symbol, bars in closed_by_symbol.items():
        if len(bars) < 60:
            continue
        last = len(bars) - 1
        for detection in scan_bars(bars)["detections"]:
            if (detection["state"] != model.CONFIRMED or detection["confirmed_index"] != last or detection["key"] in known
                    or detection["direction"] not in ("long", "short")):
                continue
            levels = detection["levels"]
            if levels.get("invalidation") is None or levels.get("target") is None:
                continue
            records.append({
                "key": detection["key"], "detector_id": detection["detector_id"], "version": detection["version"],
                "timeframe": bars.timeframe, "symbol": symbol, "direction": detection["direction"],
                "confirmed_t": bars.t[last], "stop": levels["invalidation"], "target": levels["target"],
                "trigger": levels.get("trigger"), "recorded_at": now, "status": "open",
                "regime": (detection.get("regime") or {}).get("trend"),
            })
            known.add(detection["key"])
            added += 1

    for record in records:
        if record.get("status") != "open":
            continue
        bars = closed_by_symbol.get(record["symbol"])
        if bars is None or record["confirmed_t"] not in bars.t:
            continue
        index = bars.t.index(record["confirmed_t"])
        outcome = validation.simulate(bars, {
            "detector_id": record["detector_id"], "version": record["version"], "direction": record["direction"],
            "confirmed_index": index, "levels": {"invalidation": record["stop"], "target": record["target"]},
            "regime": {"trend": record.get("regime")},
        }, max_hold=MAX_HOLD, cost_bps=COST_BPS)
        if outcome is None:
            continue  # still inside the holding window
        if "skipped" in outcome:
            record.update({"status": "skipped", "resolved_at": now, "reason": outcome["skipped"]})
        else:
            record.update({"status": "resolved", "resolved_at": now, "net_r": outcome["net_r"], "exit_reason": outcome["exit_reason"],
                           "entry": outcome["entry"], "exit": outcome["exit"], "bars_held": outcome["bars_held"]})
        resolved += 1

    log["records"] = records
    log["updated_at"] = now
    _write(LOG_FILE, log)

    grouped: Dict[str, List[float]] = {}
    for record in records:
        if record.get("status") == "resolved":
            grouped.setdefault(evidence.key(record["detector_id"], record["version"], record["timeframe"]), []).append(record["net_r"])
    _write(evidence.FORWARD_FILE, {"generated_at": now, "entries": {k: {"stats": evidence.summarize_r(v)} for k, v in grouped.items()}})
    return {"added": added, "resolved": resolved, "open": sum(1 for r in records if r.get("status") == "open"),
            "resolved_total": sum(1 for r in records if r.get("status") == "resolved"), "updated_at": now}


def summary() -> Dict[str, Any]:
    log = _read(LOG_FILE)
    records = log.get("records") or []
    return {"updated_at": log.get("updated_at"), "open": sum(1 for r in records if r.get("status") == "open"),
            "resolved": sum(1 for r in records if r.get("status") == "resolved"), "records": len(records)}
