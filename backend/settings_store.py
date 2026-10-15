from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List

if __package__:
    from .global_settings import get_global_settings
    from . import read_cache
else:
    from global_settings import get_global_settings
    import read_cache

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("PLUTO_DATA_DIR", str(BASE_DIR / "data"))).resolve()
USER_DATA_ROOT = DATA_DIR / "users"


def _settings_file(user_id: str) -> Path:
    if not user_id:
        raise ValueError("user_id is required.")
    return USER_DATA_ROOT / user_id / "settings.json"


def _default_settings() -> Dict[str, object]:
    return {
        "theme": "Neon",
        "notifications_enabled": True,
        "ai_confidence_threshold": get_global_settings()["default_ai_confidence_threshold"],
        "scanner_frequency_seconds": 20,
        "market_hours": "09:30-16:00 ET",
        "paper_trading_enabled": True,
        "auto_suggestions_enabled": True,
        "show_mission_brief_again": False,
        "mission_brief_last_viewed_date": "",
        "api_status": "Operational",
        "trusted_news_sources": ["Official X API", "Yahoo Finance News", "MarketWatch RSS"],
        # Private per-user goal. Amount stays unset until the owner names one.
        # Visible can be turned off to hide the meter, the recap, and the exit-alert line.
        "savings_goal_name": "",
        "savings_goal_amount": None,
        "savings_goal_visible": True,
    }


_THEME_CANONICAL = {
    "neon": "Neon",
    "icy": "Icy",
    "gold": "Gold",
    "light": "Light",
    # Older palette names resolve to the default so a saved setting still loads.
    "midnight": "Neon",
    "onyx": "Neon",
    "nebula": "Neon",
}


def _ensure_file(user_id: str) -> Path:
    settings_file = _settings_file(user_id)
    settings_file.parent.mkdir(parents=True, exist_ok=True)
    if not settings_file.exists():
        settings_file.write_text(json.dumps(_default_settings(), indent=2), encoding="utf-8")
    return settings_file


def get_settings(user_id: str) -> Dict[str, object]:
    """Read settings. Rewrite the file only when a new default key is missing.

    The old read path wrote the merged document on every call. A page render
    calls this several times, so every logged-in click rewrote settings.json
    and global_settings.json on the persistent disk.
    """
    settings_file = _ensure_file(user_id)

    def _read() -> Dict[str, object]:
        try:
            payload = json.loads(settings_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        merged = {**_default_settings(), **payload}
        if merged != payload:
            settings_file.write_text(json.dumps(merged, indent=2), encoding="utf-8")
            read_cache.forget(settings_file)
        return merged

    return dict(read_cache.load(settings_file, _read))


def update_settings(user_id: str, payload: Dict[str, object]) -> Dict[str, object]:
    settings = get_settings(user_id)
    safe_updates: Dict[str, object] = {}
    for key in [
        "theme",
        "notifications_enabled",
        "ai_confidence_threshold",
        "scanner_frequency_seconds",
        "market_hours",
        "paper_trading_enabled",
        "auto_suggestions_enabled",
        "show_mission_brief_again",
        "mission_brief_last_viewed_date",
        "trusted_news_sources",
        "savings_goal_name",
        "savings_goal_amount",
        "savings_goal_visible",
    ]:
        if key not in payload:
            continue
        if key == "theme":
            canonical = _THEME_CANONICAL.get(str(payload[key] or "").strip().lower())
            if canonical is None:
                raise ValueError("Unknown palette.")
            safe_updates[key] = canonical
            continue
        if key == "savings_goal_visible":
            raw = payload[key]
            safe_updates[key] = raw is True or str(raw).strip().lower() in {"1", "true", "yes", "on"}
            continue
        if key == "savings_goal_name":
            safe_updates[key] = str(payload[key] or "")[:80]
            continue
        if key == "savings_goal_amount":
            raw = payload[key]
            if raw in (None, "", 0, "0"):
                safe_updates[key] = None
                continue
            amount = float(raw)
            if amount <= 0:
                raise ValueError("Savings goal amount must be greater than zero, or blank to leave it unset.")
            safe_updates[key] = round(amount, 2)
            continue
        safe_updates[key] = payload[key]
    if bool(safe_updates.get("show_mission_brief_again")):
        safe_updates["mission_brief_last_viewed_date"] = ""
    settings.update(safe_updates)
    _settings_file(user_id).write_text(json.dumps(settings, indent=2), encoding="utf-8")
    return settings


def available_themes() -> List[str]:
    return ["Neon", "Icy", "Gold", "Light"]
