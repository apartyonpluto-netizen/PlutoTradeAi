from __future__ import annotations

"""Autonomy mode / risk-settings store - single JSON document per user.

Hardened 2026-09-02 after a real incident: this file's mode silently
reset to OFF (mode_change_reason == "Initialization", the value ONLY
ever written by _default_settings - never by a deliberate human toggle,
which always supplies its own real reason string) sometime between two
routine checks, with no error, no alert, nothing - it just stopped. Root
cause was never pinned down with certainty (the raw corrupted file, if
that's what happened, was already overwritten by the self-healing
_load path by the time this was investigated), but the mechanism was
real and reproducible: every read-modify-write here (_load then mutate
then _save) ran as two SEPARATE, unlocked file operations, with gunicorn
running multiple worker processes against the same file - the exact
"two concurrent writes race into a lost or corrupted record" class of
bug already fixed once elsewhere in this codebase (see
research_log.py's own _locked/_atomic_write, closed_trades.py,
alerts.py, ambiguous_resolution_audit.py) but never applied to this
file, arguably the single MOST safety-critical piece of state in the
whole app - it gates whether the autonomous scan ever places a real
order at all.

Every read-modify-write now runs under ONE exclusive lock held across
the ENTIRE load-mutate-save sequence (_locked_read_modify_write), not
two separately-locked operations - closes the classic TOCTOU gap a
lock-per-operation design would still leave open. Every write is
atomic (write to a temp file, then os.replace - never a partial write
a concurrent reader or the next crashed-mid-write process could see).
Plain reads (get_autonomy_status) stay lock-free, same as
research_log.py's own list_research_decisions - atomic writes make a
torn read impossible, so a lock buys nothing there."""

import contextlib
import fcntl
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Optional

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


@contextlib.contextmanager
def _locked(path: Path):
    """Same exclusive-lock-around-read-modify-write pattern as
    research_log.py/closed_trades.py/alerts.py/ambiguous_resolution_audit.py -
    gunicorn's multiple WORKER PROCESSES could otherwise race two
    concurrent writes into a lost or corrupted record."""
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.touch(exist_ok=True)
    with open(lock_path, "r+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _atomic_write(path: Path, payload: Dict[str, object]) -> None:
    tmp_path = path.with_name(f"{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}")
    tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)


def _read_raw(path: Path) -> Optional[Dict[str, object]]:
    """None means "missing or unparseable" - the caller decides whether
    that's an error or (as _load below treats it) simply "not created
    yet", never silently coerced to {} here."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _load(user_id: str) -> Dict[str, object]:
    """Lock-free read - atomic writes (see _atomic_write) make a torn
    read impossible, so the only remaining question is "does a real,
    parseable file exist yet", not "is it mid-write". A missing/corrupt
    file self-heals to defaults, but that write happens under the lock
    in _locked_read_modify_write/get_autonomy_status below, re-checking
    after acquiring it - never here, where two callers hitting this
    branch at once would just be a second way to race a corrupting
    write into existence."""
    path = _settings_file(user_id)
    raw = _read_raw(path)
    if raw is None:
        raw = _ensure_file_exists_locked(path)
    return {**_default_settings(), **raw}


def _ensure_file_exists_locked(path: Path) -> Dict[str, object]:
    with _locked(path):
        raw = _read_raw(path)
        if raw is not None:
            return raw  # another worker created/repaired it while this one waited for the lock
        raw = _default_settings()
        _atomic_write(path, raw)
        return raw


def _locked_read_modify_write(user_id: str, mutate: Callable[[Dict[str, object]], None]) -> Dict[str, object]:
    """The one path every mode/risk-setting change in this module goes
    through: load, mutate, and save ALL under the SAME lock acquisition -
    not _load() then a separately-locked _save(), which would still let
    two concurrent callers each read the same starting state and one's
    write silently clobber the other's. mutate receives the merged
    (defaults + on-disk) settings dict and modifies it in place; nothing
    is returned by mutate itself."""
    path = _settings_file(user_id)
    with _locked(path):
        raw = _read_raw(path)
        if raw is None:
            raw = _default_settings()
        settings = {**_default_settings(), **raw}
        mutate(settings)
        _atomic_write(path, settings)
        return settings


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

    def _mutate(settings: Dict[str, object]) -> None:
        settings["current_mode"] = normalized_mode
        settings["last_mode_change"] = _now_iso()
        settings["mode_change_reason"] = reason or f"Mode changed to {normalized_mode}"
        settings["live_trading_enabled"] = False
        settings["paper_trading_enabled"] = normalized_mode == "PAPER"
        settings["approval_required"] = normalized_mode == "APPROVAL"
        if settings.get("emergency_stop_enabled"):
            settings["paper_trading_enabled"] = False
            settings["approval_required"] = False

    return _derived(_locked_read_modify_write(user_id, _mutate))


def update_risk_settings(
    user_id: str,
    daily_loss_limit_percent: float | None = None,
    risk_percent_of_balance: float | None = None,
    max_positions: int | None = None,
) -> Dict[str, object]:
    if daily_loss_limit_percent is not None and not (0 <= daily_loss_limit_percent <= 100):
        raise ValueError("Daily loss limit percent must be between 0 and 100.")
    if risk_percent_of_balance is not None and not (0 <= risk_percent_of_balance <= 100):
        raise ValueError("Risk percent of balance must be between 0 and 100.")
    if max_positions is not None and max_positions < 0:
        raise ValueError("Max positions must be zero or positive.")

    def _mutate(settings: Dict[str, object]) -> None:
        if daily_loss_limit_percent is not None:
            settings["daily_loss_limit_percent"] = float(daily_loss_limit_percent)
        if risk_percent_of_balance is not None:
            settings["risk_percent_of_balance"] = float(risk_percent_of_balance)
        if max_positions is not None:
            settings["max_positions"] = int(max_positions)

    return _derived(_locked_read_modify_write(user_id, _mutate))


def emergency_stop(user_id: str, reason: str = "") -> Dict[str, object]:
    def _mutate(settings: Dict[str, object]) -> None:
        settings["emergency_stop_enabled"] = True
        settings["live_trading_enabled"] = False
        settings["paper_trading_enabled"] = False
        settings["approval_required"] = False
        settings["last_mode_change"] = _now_iso()
        settings["mode_change_reason"] = reason or "Emergency stop enabled"

    return _derived(_locked_read_modify_write(user_id, _mutate))


def reset_emergency_stop(user_id: str, reason: str = "") -> Dict[str, object]:
    def _mutate(settings: Dict[str, object]) -> None:
        settings["emergency_stop_enabled"] = False
        settings["last_mode_change"] = _now_iso()
        settings["mode_change_reason"] = reason or "Emergency stop reset"

    return _derived(_locked_read_modify_write(user_id, _mutate))
