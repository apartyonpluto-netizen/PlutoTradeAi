from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

BASE_DIR = Path(__file__).resolve().parents[2]
SETTINGS_FILE = BASE_DIR / "data" / "autonomy_settings.json"
MODES = ("OFF", "SCOUT", "ANALYST", "PAPER", "APPROVAL", "AUTONOMOUS")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_settings() -> Dict[str, object]:
    now = _now_iso()
    return {
        "current_mode": "OFF",
        "live_trading_enabled": False,
        "paper_trading_enabled": False,
        "approval_required": False,
        "daily_loss_limit": 500.0,
        "max_trade_size": 250.0,
        "max_positions": 3,
        "emergency_stop_enabled": False,
        "last_mode_change": now,
        "mode_change_reason": "Initialization",
    }


def _ensure_file() -> None:
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    if SETTINGS_FILE.exists():
        return
    SETTINGS_FILE.write_text(json.dumps(_default_settings(), indent=2), encoding="utf-8")


def _load() -> Dict[str, object]:
    _ensure_file()
    try:
        payload = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        payload = _default_settings()
    merged = {**_default_settings(), **payload}
    SETTINGS_FILE.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    return merged


def _save(payload: Dict[str, object]) -> Dict[str, object]:
    SETTINGS_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def _derived(payload: Dict[str, object]) -> Dict[str, object]:
    current_mode = str(payload.get("current_mode", "OFF")).upper()
    emergency_stop_enabled = bool(payload.get("emergency_stop_enabled", False))
    return {
        **payload,
        "allowed_modes": list(MODES),
        "live_trading_locked": True,
        "paper_trading_active": current_mode == "PAPER" and not emergency_stop_enabled,
        "approval_required_status": current_mode == "APPROVAL" and bool(payload.get("approval_required", False)),
        "autonomous_mode_locked_message": "Locked until future release" if current_mode == "AUTONOMOUS" else "",
    }


def get_autonomy_status() -> Dict[str, object]:
    return _derived(_load())


def set_mode(mode: str, reason: str = "") -> Dict[str, object]:
    normalized_mode = str(mode or "").strip().upper()
    if normalized_mode not in MODES:
        raise ValueError("Unsupported autonomy mode.")

    settings = _load()
    if normalized_mode == "AUTONOMOUS":
        settings["mode_change_reason"] = "Locked until future release"
        settings["last_mode_change"] = _now_iso()
        return _derived(_save(settings))

    settings["current_mode"] = normalized_mode
    settings["last_mode_change"] = _now_iso()
    settings["mode_change_reason"] = reason or f"Mode changed to {normalized_mode}"
    settings["live_trading_enabled"] = False
    settings["paper_trading_enabled"] = normalized_mode == "PAPER"
    settings["approval_required"] = normalized_mode == "APPROVAL"
    if settings.get("emergency_stop_enabled"):
        settings["paper_trading_enabled"] = False
        settings["approval_required"] = False
    return _derived(_save(settings))


def emergency_stop(reason: str = "") -> Dict[str, object]:
    settings = _load()
    settings["emergency_stop_enabled"] = True
    settings["live_trading_enabled"] = False
    settings["paper_trading_enabled"] = False
    settings["approval_required"] = False
    settings["last_mode_change"] = _now_iso()
    settings["mode_change_reason"] = reason or "Emergency stop enabled"
    return _derived(_save(settings))


def reset_emergency_stop(reason: str = "") -> Dict[str, object]:
    settings = _load()
    settings["emergency_stop_enabled"] = False
    settings["last_mode_change"] = _now_iso()
    settings["mode_change_reason"] = reason or "Emergency stop reset"
    return _derived(_save(settings))

