"""Automatic halt after a daily loss or a losing streak.

The daily-loss check itself still lives next to the scan (percent of
balance). This module adds the losing-streak half and a one-shot phone
alert so a halt is not re-announced on every scan tick.

Halting blocks new entries. It does not flatten. Flattening is the separate
confirmed close-everything action in flatten_account.py.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import phone_alerts
from paper_costs import performance_net

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("PLUTO_DATA_DIR", str(BASE_DIR / "data"))).resolve()
USER_DATA_ROOT = DATA_DIR / "users"

DEFAULT_LOSING_STREAK = 3


def consecutive_losses(trades_newest_first: List[Dict[str, Any]]) -> int:
    """Complete trades, newest first. A winner or a scratch ends the streak.
    Incomplete P&L is skipped rather than counted as a loss."""
    streak = 0
    for trade in trades_newest_first:
        if trade.get("pnl_status") not in (None, "", "complete"):
            continue
        net = performance_net(trade)
        if net is None:
            continue
        if net < 0:
            streak += 1
            continue
        break
    return streak


def streak_block_reason(trades_newest_first: List[Dict[str, Any]], limit: int) -> Optional[str]:
    """None when the limit is off (0) or the streak is shorter than it."""
    if limit <= 0:
        return None
    streak = consecutive_losses(trades_newest_first)
    if streak >= limit:
        return (
            f"Losing streak halt: {streak} closed trades in a row lost money "
            f"(limit {limit}). New entries are paused until the streak is reset in Account Hub "
            "or a later close breaks it. Open positions stay protected."
        )
    return None


def _state_file(user_id: str) -> Path:
    path = USER_DATA_ROOT / user_id
    path.mkdir(parents=True, exist_ok=True)
    return path / "kill_switch.json"


def _read_state(user_id: str) -> Dict[str, Any]:
    path = _state_file(user_id)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _write_state(user_id: str, data: Dict[str, Any]) -> None:
    path = _state_file(user_id)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def current_halt(user_id: str) -> Dict[str, Any]:
    state = _read_state(user_id)
    return {
        "active": bool(state.get("active")),
        "reason": str(state.get("reason") or ""),
        "since": state.get("since") or "",
    }


def note_halt(user_id: str, reason: str) -> bool:
    """Records the halt and phones once per distinct reason. Returns True
    when this call sent (or tried) a new alert."""
    state = _read_state(user_id)
    if state.get("active") and state.get("reason") == reason:
        return False
    state = {"active": True, "reason": reason, "since": datetime.now(timezone.utc).isoformat()}
    _write_state(user_id, state)
    phone_alerts.notify("halt", "Trading halted", reason)
    return True


def clear_halt(user_id: str) -> None:
    state = _read_state(user_id)
    if not state.get("active"):
        return
    state["active"] = False
    state["cleared_at"] = datetime.now(timezone.utc).isoformat()
    _write_state(user_id, state)
