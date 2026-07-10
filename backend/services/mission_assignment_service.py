from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

MISSION_FILE = Path("data/mission_assignments.json")
USER_MISSION_ROOT = Path("data/users")
MISSION_NAMESPACE_PATTERN = re.compile(r"[^a-z0-9_.-]+")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_store() -> Dict[str, Any]:
    return {"missions": []}


def load_store() -> Dict[str, Any]:
    if not MISSION_FILE.exists():
        return _default_store()

    try:
        raw = MISSION_FILE.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, ValueError):
        return _default_store()

    if not isinstance(data, dict):
        return _default_store()

    missions = data.get("missions")
    if not isinstance(missions, list):
        return _default_store()

    return {"missions": missions}


def save_store(store: Dict[str, Any]) -> None:
    MISSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {"missions": store.get("missions", [])}
    MISSION_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def normalize_namespace(namespace: str) -> str:
    normalized = (namespace or "guest").strip().lower()
    normalized = MISSION_NAMESPACE_PATTERN.sub("_", normalized)
    return normalized or "guest"


def _user_store_path(namespace: str) -> Path:
    safe_namespace = normalize_namespace(namespace)
    return USER_MISSION_ROOT / safe_namespace / "missions.json"


def load_user_store(namespace: str) -> Dict[str, Any]:
    store_path = _user_store_path(namespace)
    if not store_path.exists():
        return _default_store()

    try:
        raw = store_path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, ValueError):
        return _default_store()

    if not isinstance(data, dict):
        return _default_store()

    missions = data.get("missions")
    if not isinstance(missions, list):
        return _default_store()

    return {"missions": missions}


def save_user_store(namespace: str, store: Dict[str, Any]) -> None:
    store_path = _user_store_path(namespace)
    store_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"missions": store.get("missions", [])}
    store_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_all_user_stores() -> Dict[str, Dict[str, Any]]:
    stores: Dict[str, Dict[str, Any]] = {}
    if not USER_MISSION_ROOT.exists():
        return stores

    for store_path in USER_MISSION_ROOT.glob("*/missions.json"):
        namespace = store_path.parent.name
        stores[namespace] = load_user_store(namespace)

    return stores


def mission_exists(missions: List[Dict[str, Any]], ticker: str) -> bool:
    ticker = (ticker or "").upper()
    for mission in missions:
        if (mission.get("ticker") or "").upper() == ticker:
            return True
    return False


def upsert_mission(missions: List[Dict[str, Any]], new_mission: Dict[str, Any]) -> List[Dict[str, Any]]:
    ticker = (new_mission.get("ticker") or "").upper()
    updated = []
    replaced = False

    for mission in missions:
        if (mission.get("ticker") or "").upper() == ticker:
            updated.append(new_mission)
            replaced = True
        else:
            updated.append(mission)

    if not replaced:
        updated.append(new_mission)

    return updated


def _append_history(series: List[Dict[str, Any]], value: Any) -> List[Dict[str, Any]]:
    updated = list(series or [])
    updated.append({"timestamp": utc_now_iso(), "value": value})
    return updated


def refresh_mission_profile(
    mission: Dict[str, Any],
    snapshot: Dict[str, Any],
    overview: Dict[str, Any],
    scanner_overview: Dict[str, Any],
) -> Dict[str, Any]:
    updated = dict(mission)
    overview_data = overview.get("data", {}) if overview.get("success") else {}
    scanner_data = scanner_overview.get("data", {}) if scanner_overview.get("success") else {}

    updated["company"] = snapshot.get("company") or updated.get("company")
    updated["asset_type"] = snapshot.get("asset_type") or updated.get("asset_type")
    updated["assigned_scanners"] = updated.get("assigned_scanners", [])
    updated["assigned_brains"] = updated.get("assigned_brains", [])
    updated["monitoring_flags"] = updated.get("monitoring_flags", [])
    updated["confidence_history"] = _append_history(updated.get("confidence_history", []), overview_data.get("confidence"))
    updated["risk_history"] = _append_history(updated.get("risk_history", []), overview_data.get("risk_score"))
    updated["trade_thesis_history"] = _append_history(updated.get("trade_thesis_history", []), overview_data.get("trade_thesis"))
    updated["support_history"] = _append_history(updated.get("support_history", []), overview_data.get("support"))
    updated["resistance_history"] = _append_history(updated.get("resistance_history", []), overview_data.get("resistance"))
    updated["options_history"] = _append_history(
        updated.get("options_history", []),
        {
            "direction": overview_data.get("call_put_wait_bias"),
            "strategy": overview_data.get("recommended_strategy"),
            "confidence": overview_data.get("confidence"),
        },
    )
    updated["scanner_results"] = scanner_data.get("rows", [])[:5]
    updated["last_ai_update"] = utc_now_iso()
    updated["last_market_update"] = snapshot.get("last_updated") or utc_now_iso()
    updated["mission_status"] = "Active"
    return updated


def build_mission_timeline(mission: Dict[str, Any]) -> List[Dict[str, Any]]:
    timeline: List[Dict[str, Any]] = []
    key_map = [
        ("Confidence", mission.get("confidence_history", [])),
        ("Risk", mission.get("risk_history", [])),
        ("Trade Thesis", mission.get("trade_thesis_history", [])),
        ("Support", mission.get("support_history", [])),
        ("Resistance", mission.get("resistance_history", [])),
        ("Options", mission.get("options_history", [])),
    ]

    for label, entries in key_map:
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            timeline.append(
                {
                    "label": label,
                    "timestamp": entry.get("timestamp"),
                    "value": entry.get("value") if label not in {"Options"} else entry,
                }
            )

    timeline.sort(key=lambda item: item.get("timestamp") or "")
    return timeline


def build_mission_summary(missions: List[Dict[str, Any]]) -> Dict[str, Any]:
    active = len(missions)
    high_priority = sum(1 for m in missions if (m.get("priority") or "").startswith("5") or "Critical" in (m.get("priority") or ""))
    recently_changed = sorted(
        missions,
        key=lambda m: m.get("last_ai_update") or "",
        reverse=True,
    )[:5]

    confidence_changes = []
    risk_changes = []
    upcoming = []

    for mission in missions:
        confidence_history = mission.get("confidence_history") or []
        if len(confidence_history) >= 2:
            before = confidence_history[-2].get("value")
            after = confidence_history[-1].get("value")
            if before is not None and after is not None and before != after:
                confidence_changes.append(
                    {
                        "ticker": mission.get("ticker"),
                        "from": before,
                        "to": after,
                    }
                )

        risk_history = mission.get("risk_history") or []
        if len(risk_history) >= 2:
            before = risk_history[-2].get("value")
            after = risk_history[-1].get("value")
            if before != after:
                risk_changes.append({"ticker": mission.get("ticker"), "from": before, "to": after})

        if (mission.get("mission_type") or "").lower() in {"ai discovery", "options", "futures"}:
            upcoming.append(
                {
                    "ticker": mission.get("ticker"),
                    "mission_type": mission.get("mission_type"),
                    "priority": mission.get("priority"),
                }
            )

    return {
        "active_missions": active,
        "symbols_being_monitored": active,
        "high_priority_missions": high_priority,
        "recently_changed_missions": recently_changed,
        "ai_confidence_changes": confidence_changes,
        "risk_changes": risk_changes,
        "upcoming_opportunities": upcoming,
        "last_updated": utc_now_iso(),
    }
