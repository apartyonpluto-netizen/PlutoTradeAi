from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("PLUTO_DATA_DIR", str(BASE_DIR / "data"))).resolve()
GLOBAL_SETTINGS_FILE = DATA_DIR / "global_settings.json"


def _defaults() -> Dict[str, Any]:
    return {
        "registration_open": True,
        "default_ai_confidence_threshold": 68,
        "default_daily_loss_limit_percent": 10.0,
        "default_risk_percent_of_balance": 5.0,
        "default_max_positions": 3,
    }


def get_global_settings() -> Dict[str, Any]:
    """Admin-configured defaults applied to brand-new accounts only - changing
    these never retroactively touches a settings file that already exists,
    same as every other per-user settings store in this app."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not GLOBAL_SETTINGS_FILE.exists():
        GLOBAL_SETTINGS_FILE.write_text(json.dumps(_defaults(), indent=2), encoding="utf-8")
    try:
        payload = json.loads(GLOBAL_SETTINGS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    merged = {**_defaults(), **payload}
    GLOBAL_SETTINGS_FILE.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    return merged


def update_global_settings(updates: Dict[str, Any]) -> Dict[str, Any]:
    settings = get_global_settings()

    if "registration_open" in updates:
        settings["registration_open"] = bool(updates["registration_open"])

    if "default_ai_confidence_threshold" in updates:
        value = int(updates["default_ai_confidence_threshold"])
        if not (0 <= value <= 100):
            raise ValueError("Confidence threshold must be between 0 and 100.")
        settings["default_ai_confidence_threshold"] = value

    if "default_daily_loss_limit_percent" in updates:
        value = float(updates["default_daily_loss_limit_percent"])
        if not (0 <= value <= 100):
            raise ValueError("Daily loss limit percent must be between 0 and 100.")
        settings["default_daily_loss_limit_percent"] = value

    if "default_risk_percent_of_balance" in updates:
        value = float(updates["default_risk_percent_of_balance"])
        if not (0 <= value <= 100):
            raise ValueError("Risk percent of balance must be between 0 and 100.")
        settings["default_risk_percent_of_balance"] = value

    if "default_max_positions" in updates:
        value = int(updates["default_max_positions"])
        if value < 0:
            raise ValueError("Max positions cannot be negative.")
        settings["default_max_positions"] = value

    GLOBAL_SETTINGS_FILE.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    return settings
