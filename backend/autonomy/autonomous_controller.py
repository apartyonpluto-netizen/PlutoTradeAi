from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

from global_settings import get_global_settings

BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("PLUTO_DATA_DIR", str(BASE_DIR / "data"))).resolve()
USER_DATA_ROOT = DATA_DIR / "users"
MODES = ("OFF", "SCOUT", "ANALYST", "PAPER", "APPROVAL", "AUTONOMOUS")


def _settings_file(user_id: str) -> Path:
    if not user_id:
        raise ValueError("user_id is required.")
    path = USER_DATA_ROOT / user_id
    path.mkdir(parents=True, exist_ok=True)
    return path / "autonomy_settings.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_settings() -> Dict[str, object]:
    now = _now_iso()
    global_defaults = get_global_settings()
    return {
        "current_mode": "OFF",
        "live_trading_enabled": False,
        "paper_trading_enabled": False,
        "approval_required": False,
        "daily_loss_limit_percent": global_defaults["default_daily_loss_limit_percent"],
        "risk_percent_of_balance": global_defaults["default_risk_percent_of_balance"],
        "max_positions": global_defaults["default_max_positions"],
        "emergency_stop_enabled": False,
        "last_mode_change": now,
        "mode_change_reason": "Initialization",
    }


def _load(user_id: str) -> Dict[str, object]:
    settings_file = _settings_file(user_id)
    if not settings_file.exists():
        settings_file.write_text(json.dumps(_default_settings(), indent=2), encoding="utf-8")
    try:
        payload = json.loads(settings_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        payload = _default_settings()
    merged = {**_default_settings(), **payload}
    settings_file.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    return merged


def _save(user_id: str, payload: Dict[str, object]) -> Dict[str, object]:
    _settings_file(user_id).write_text(json.dumps(payload, indent=2), encoding="utf-8")
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
        "autonomous_mode_locked_message": (
            "Sandbox only - real-money live execution stays locked regardless of this mode." if current_mode == "AUTONOMOUS" else ""
        ),
    }


def get_autonomy_status(user_id: str) -> Dict[str, object]:
    return _derived(_load(user_id))


def set_mode(user_id: str, mode: str, reason: str = "") -> Dict[str, object]:
    normalized_mode = str(mode or "").strip().upper()
    if normalized_mode not in MODES:
        raise ValueError("Unsupported autonomy mode.")

    settings = _load(user_id)
    settings["current_mode"] = normalized_mode
    settings["last_mode_change"] = _now_iso()
    settings["mode_change_reason"] = reason or f"Mode changed to {normalized_mode}"
    settings["live_trading_enabled"] = False
    settings["paper_trading_enabled"] = normalized_mode == "PAPER"
    settings["approval_required"] = normalized_mode == "APPROVAL"
    if settings.get("emergency_stop_enabled"):
        settings["paper_trading_enabled"] = False
        settings["approval_required"] = False
    return _derived(_save(user_id, settings))


def update_risk_settings(
    user_id: str,
    daily_loss_limit_percent: float | None = None,
    risk_percent_of_balance: float | None = None,
    max_positions: int | None = None,
) -> Dict[str, object]:
    settings = _load(user_id)
    if daily_loss_limit_percent is not None:
        if not (0 <= daily_loss_limit_percent <= 100):
            raise ValueError("Daily loss limit percent must be between 0 and 100.")
        settings["daily_loss_limit_percent"] = float(daily_loss_limit_percent)
    if risk_percent_of_balance is not None:
        if not (0 <= risk_percent_of_balance <= 100):
            raise ValueError("Risk percent of balance must be between 0 and 100.")
        settings["risk_percent_of_balance"] = float(risk_percent_of_balance)
    if max_positions is not None:
        if max_positions < 0:
            raise ValueError("Max positions must be zero or positive.")
        settings["max_positions"] = int(max_positions)
    return _derived(_save(user_id, settings))


def emergency_stop(user_id: str, reason: str = "") -> Dict[str, object]:
    settings = _load(user_id)
    settings["emergency_stop_enabled"] = True
    settings["live_trading_enabled"] = False
    settings["paper_trading_enabled"] = False
    settings["approval_required"] = False
    settings["last_mode_change"] = _now_iso()
    settings["mode_change_reason"] = reason or "Emergency stop enabled"
    return _derived(_save(user_id, settings))


def reset_emergency_stop(user_id: str, reason: str = "") -> Dict[str, object]:
    settings = _load(user_id)
    settings["emergency_stop_enabled"] = False
    settings["last_mode_change"] = _now_iso()
    settings["mode_change_reason"] = reason or "Emergency stop reset"
    return _derived(_save(user_id, settings))
