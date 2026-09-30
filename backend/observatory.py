"""Data for the observatory, built only from recorded events
(autonomy/event_journal.py) - nothing is simulated or interpolated.

Two kinds of traffic are kept apart:
  STRUCTURAL - the paths a trade idea CAN take through the agent (fixed,
               from the code's design). Drawn faint; they say nothing about
               activity.
  OBSERVED   - transitions that actually happened in the window: for each
               correlation id, its events in time order, counted per
               consecutive pair of stages. Drawn bright, sized by count.

Levels: "agent" (stage flow), "sectors" (sector -> tickers seen), "ticker"
(one ticker's trade chains; each chain's full trace comes from
/api/events/trace/<correlation_id>)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

try:
    from autonomy import event_journal
except ImportError:  # pragma: no cover - package-relative import
    from .autonomy import event_journal  # type: ignore

import portfolio_limits

STAGES = [
    {"id": "signal", "label": "Signal evaluated", "detail": "the scan evaluated a candidate"},
    {"id": "plan", "label": "Trade plan", "detail": "size, invalidation and scenarios computed"},
    {"id": "ticket", "label": "Approval ticket", "detail": "proposed, approved, declined, expired..."},
    {"id": "order", "label": "Order", "detail": "submitted to the broker (or failed / ambiguous)"},
    {"id": "fill", "label": "Fill", "detail": "the broker reported shares/contracts filled"},
    {"id": "protection", "label": "Protection", "detail": "protective stop pending / active / failed"},
    {"id": "reconcile", "label": "Reconciliation", "detail": "manual resolution states"},
    {"id": "exit", "label": "Exit", "detail": "position closed (with broker fees when known)"},
]
STRUCTURAL_EDGES = [
    ("signal", "plan"), ("plan", "ticket"), ("plan", "order"), ("ticket", "order"), ("order", "fill"),
    ("order", "reconcile"), ("fill", "protection"), ("fill", "exit"), ("protection", "exit"), ("reconcile", "fill"),
    ("reconcile", "exit"),
]
MAX_WINDOW_HOURS = 24 * 14


def _since(window_hours: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat()


def _chains(events: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    chains: Dict[str, List[Dict[str, Any]]] = {}
    for event in events:
        if event.get("correlation_id"):
            chains.setdefault(event["correlation_id"], []).append(event)
    for chain in chains.values():
        chain.sort(key=lambda e: (str(e.get("at") or ""), str(e.get("logged_at") or "")))
    return chains


def build(user_id: str, *, level: str = "agent", window_hours: float = 24, sector: Optional[str] = None,
          ticker: Optional[str] = None) -> Dict[str, Any]:
    window_hours = max(1.0, min(float(window_hours or 24), MAX_WINDOW_HOURS))
    since = _since(window_hours)
    events = event_journal.read_events(user_id, since=since, limit=0)
    chains = _chains(events)
    stage_ids = [s["id"] for s in STAGES]

    stage_counts = {sid: 0 for sid in stage_ids}
    last_at: Dict[str, str] = {}
    for event in events:
        stage = event.get("stage")
        if stage in stage_counts:
            stage_counts[stage] += 1
            last_at[stage] = max(last_at.get(stage, ""), str(event.get("logged_at") or ""))

    observed: Dict[tuple, int] = {}
    envs = set()
    for chain in chains.values():
        stages = [e.get("stage") for e in chain if e.get("stage") in stage_counts]
        envs.update(e.get("env") for e in chain if e.get("stage") in ("order", "fill", "protection", "exit") and e.get("env"))
        for a, b in zip(stages, stages[1:]):
            if a != b:
                observed[(a, b)] = observed.get((a, b), 0) + 1

    out: Dict[str, Any] = {
        "level": level,
        "window_hours": window_hours,
        "since": since,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "event_count": len(events),
        "chain_count": len(chains),
        "environments_seen": sorted(envs),
        "journal_write_failures": event_journal.failure_stats()["count"],
        "stages": [{**s, "count": stage_counts[s["id"]], "last_at": last_at.get(s["id"])} for s in STAGES],
        "structural_edges": [{"from": a, "to": b} for a, b in STRUCTURAL_EDGES],
        "observed_edges": [{"from": a, "to": b, "count": n, "structural": (a, b) in STRUCTURAL_EDGES}
                           for (a, b), n in sorted(observed.items(), key=lambda kv: -kv[1])],
    }

    by_ticker: Dict[str, Dict[str, Any]] = {}
    for correlation_id, chain in chains.items():
        symbol = next((e.get("ticker") for e in chain if e.get("ticker")), None)
        if not symbol:
            continue
        stages_reached = [s for s in stage_ids if any(e.get("stage") == s for e in chain)]
        entry = by_ticker.setdefault(symbol, {"ticker": symbol, "sector": portfolio_limits.sector_for(symbol) or "Unclassified",
                                              "events": 0, "chains": 0, "furthest_stage": None, "last_at": ""})
        entry["events"] += len(chain)
        entry["chains"] += 1
        furthest = stages_reached[-1] if stages_reached else None
        if furthest and (entry["furthest_stage"] is None or stage_ids.index(furthest) > stage_ids.index(entry["furthest_stage"])):
            entry["furthest_stage"] = furthest
        entry["last_at"] = max(entry["last_at"], str(chain[-1].get("logged_at") or ""))

    sectors: Dict[str, Dict[str, Any]] = {}
    for entry in by_ticker.values():
        bucket = sectors.setdefault(entry["sector"], {"sector": entry["sector"], "tickers": [], "events": 0, "chains": 0})
        bucket["tickers"].append(entry)
        bucket["events"] += entry["events"]
        bucket["chains"] += entry["chains"]
    for bucket in sectors.values():
        bucket["tickers"].sort(key=lambda t: -t["events"])
    out["sectors"] = sorted(sectors.values(), key=lambda b: -b["events"])
    if sector:
        out["sector"] = next((b for b in out["sectors"] if b["sector"] == sector), {"sector": sector, "tickers": [], "events": 0, "chains": 0})

    if ticker:
        symbol = ticker.upper()
        ticker_chains = []
        for correlation_id, chain in chains.items():
            if not any(e.get("ticker") == symbol for e in chain):
                continue
            ticker_chains.append({
                "correlation_id": correlation_id,
                "first_at": chain[0].get("at"),
                "last_at": chain[-1].get("logged_at"),
                "stages": [s for s in stage_ids if any(e.get("stage") == s for e in chain)],
                "latest": chain[-1].get("type"),
                "events": [{"type": e.get("type"), "stage": e.get("stage"), "at": e.get("at"), "env": e.get("env")} for e in chain][-40:],
            })
        ticker_chains.sort(key=lambda c: str(c["last_at"] or ""), reverse=True)
        out["ticker"] = {"ticker": symbol, "sector": portfolio_limits.sector_for(symbol) or "Unclassified", "chains": ticker_chains[:30]}
    return out
