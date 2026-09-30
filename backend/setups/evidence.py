"""Versioned evidence per (detector id, version, timeframe) and the validation
status derived from it. Recognition never implies permission to trade: only
VALIDATED detector versions are eligible for execution.

Two sources, merged:
  * research snapshots committed to the repo (backend/research/setup_evidence*.json),
    produced offline by validation.py from historical bars, and
  * forward (after-the-fact) outcomes of live detections, under PLUTO_DATA_DIR.
Evidence is keyed by detector VERSION: changing a detector's rules means
bumping its version, and the old evidence no longer applies."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import model

BACKEND_DIR = Path(__file__).resolve().parents[1]
SNAPSHOT_DIR = BACKEND_DIR / "research"
DATA_DIR = Path(os.environ.get("PLUTO_DATA_DIR", str(BACKEND_DIR.parent / "data"))).resolve()
FORWARD_FILE = DATA_DIR / "research" / "setup_forward_evidence.json"

MIN_OUT_OF_SAMPLE = 30
MIN_FORWARD = 20
# One-sided 95% lower bound on mean net R. Stricter than 90% because ~36
# detectors x several timeframes are tested at once: a looser bound would let
# some pass on luck. Forward evidence is still required on top.
LOWER_BOUND_Z = 1.645


def key(detector_id: str, version: str, timeframe: str) -> str:
    return f"{detector_id}@{version}|{timeframe}"


def summarize_r(values: List[float]) -> Dict[str, Any]:
    """Sample statistics of net R-multiples."""
    n = len(values)
    if n == 0:
        return {"n": 0}
    mean = sum(values) / n
    sd = math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1)) if n > 1 else 0.0
    wins = sum(1 for v in values if v > 0)
    gross_win = sum(v for v in values if v > 0)
    gross_loss = -sum(v for v in values if v < 0)
    return {
        "n": n, "mean_r": round(mean, 4), "sd_r": round(sd, 4),
        "mean_r_lower_95": round(mean - LOWER_BOUND_Z * sd / math.sqrt(n), 4) if n > 1 else None,
        "win_rate": round(wins / n, 4), "wins": wins,
        "profit_factor": round(gross_win / gross_loss, 3) if gross_loss > 0 else None,
    }


def status_from(out_of_sample: Dict[str, Any], forward: Dict[str, Any]) -> str:
    n = int(out_of_sample.get("n") or 0)
    if n < MIN_OUT_OF_SAMPLE:
        return model.RESEARCH
    lower = out_of_sample.get("mean_r_lower_95")
    if lower is None or lower <= 0:
        return model.BACKTEST_REJECTED if (out_of_sample.get("mean_r") or 0) <= 0 else model.RESEARCH
    if int(forward.get("n") or 0) >= MIN_FORWARD and (forward.get("mean_r") or 0) > 0:
        return model.VALIDATED
    return model.BACKTEST_SUPPORTED


_LOAD_CACHE: Dict[str, Any] = {}


def _load(path: Path) -> Dict[str, Any]:
    """JSON file contents, re-read only when the file changes."""
    try:
        stamp = path.stat().st_mtime_ns
    except OSError:
        return {}
    cached = _LOAD_CACHE.get(str(path))
    if cached and cached[0] == stamp:
        return cached[1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    _LOAD_CACHE[str(path)] = (stamp, data)
    return data


BACKTEST_FILE = DATA_DIR / "research" / "setup_evidence_backtest.json"


def snapshots() -> List[Dict[str, Any]]:
    """Committed snapshots plus the latest server-side backtest, oldest first
    (by generated_at), so the newest evidence for a key wins."""
    paths = sorted(SNAPSHOT_DIR.glob("setup_evidence*.json")) if SNAPSHOT_DIR.exists() else []
    found = [data for data in (_load(p) for p in paths + [BACKTEST_FILE]) if data]
    return sorted(found, key=lambda data: str(data.get("generated_at") or ""))


def latest_snapshot_summary() -> Optional[Dict[str, Any]]:
    found = snapshots()
    if not found:
        return None
    data = found[-1]
    return {k: data.get(k) for k in ("generated_at", "code_version", "data_source", "universe_size", "period", "costs", "trades", "limitations")}


def lookup(detector_id: str, version: str, timeframe: str) -> Dict[str, Any]:
    """Everything known for one detector version on one timeframe."""
    wanted = key(detector_id, version, timeframe)
    record: Dict[str, Any] = {}
    for snapshot in snapshots():
        entry = (snapshot.get("entries") or {}).get(wanted)
        if entry:
            record = {**entry, "snapshot": {k: snapshot.get(k) for k in ("generated_at", "universe_size", "period", "data_source", "costs", "code_version")}}
    forward = (_load(FORWARD_FILE).get("entries") or {}).get(wanted) or {}
    out_of_sample = record.get("out_of_sample") or {"n": 0}
    forward_stats = forward.get("stats") or {"n": 0}
    return {
        "key": wanted,
        "in_sample": record.get("in_sample") or {"n": 0},
        "out_of_sample": out_of_sample,
        "forward": forward_stats,
        "by_regime": record.get("by_regime") or {},
        "snapshot": record.get("snapshot"),
        "status": status_from(out_of_sample, forward_stats),
    }


def execution_eligible(detector_id: str, version: str, timeframe: str) -> bool:
    return lookup(detector_id, version, timeframe)["status"] == model.VALIDATED
