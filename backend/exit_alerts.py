"""One phone message for every recorded exit.

The savings line uses that account's own goal name and amount. It is omitted
when the goal is off or has no amount. No product name is built into the text.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("PLUTO_DATA_DIR", str(BASE_DIR / "data"))).resolve()
SENT_FILE = DATA_DIR / "research" / "exit_alerts_sent.json"


def _parse(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def hold_phrase(entry_at: Any, exit_at: Any) -> str:
    start = _parse(entry_at)
    end = _parse(exit_at)
    if start is None or end is None or end < start:
        return "Held time unknown."
    minutes = int((end - start).total_seconds() // 60)
    if minutes < 90:
        return f"Held {minutes} min."
    hours = minutes // 60
    if hours < 48:
        return f"Held {hours} hour{'s' if hours != 1 else ''}."
    days = hours // 24
    return f"Held {days} day{'s' if days != 1 else ''}."


def reason_phrase(record: Dict[str, Any]) -> str:
    text = " ".join(
        str(record.get(key) or "")
        for key in ("exit_type", "close_reason", "stop_refresh_reason")
    ).lower()
    if "trail" in text:
        return "at the trailing stop"
    if "kill" in text or "emergency" in text:
        return "by the kill switch"
    if "flatten" in text or "close everything" in text or "close_everything" in text:
        return "when positions were flattened"
    if "manual" in text:
        return "manually"
    if "target" in text:
        return "at target"
    if "stop" in text:
        return "at the stop"
    cleaned = str(record.get("close_reason") or record.get("exit_type") or "for a recorded reason").replace("_", " ")
    return f"({cleaned})"


def money_phrase(record: Dict[str, Any]) -> Optional[str]:
    if record.get("pnl_status") not in (None, "", "complete"):
        return None
    net = record.get("net_realized_pnl")
    if net is None:
        return None
    try:
        amount = float(net)
    except (TypeError, ValueError):
        return None
    fees = record.get("fees")
    fee_note = "after fees" if isinstance(fees, dict) and fees.get("total") is not None else "fees were not reported"
    if amount > 0:
        return f"+${amount:.0f} {fee_note}"
    if amount < 0:
        return f"-${abs(amount):.0f} {fee_note}"
    return f"$0 {fee_note}"


def goal_line(user_id: str) -> Optional[str]:
    """The account's own goal, or None when it is off or unset."""
    if not user_id:
        return None
    try:
        from settings_store import get_settings
    except ImportError:  # pragma: no cover
        from .settings_store import get_settings  # type: ignore
    try:
        from home_status import build_savings_goal
    except ImportError:  # pragma: no cover
        from .home_status import build_savings_goal  # type: ignore
    settings = get_settings(user_id)
    if not bool(settings.get("savings_goal_visible", True)):
        return None
    goal = build_savings_goal(user_id)
    if goal.get("unset") or goal.get("amount") in (None, ""):
        return None
    name = str(goal.get("name") or "").strip() or "Savings goal"
    return f"{name}: ${float(goal['banked']):.0f} of ${float(goal['amount']):.0f}."


def format_message(record: Dict[str, Any], goal: Optional[str] = None) -> str:
    ticker = str(record.get("ticker") or "the position").upper()
    reason = reason_phrase(record)
    money = money_phrase(record)
    held = hold_phrase(record.get("entry_timestamp"), record.get("exit_timestamp"))
    if money is None:
        head = f"Exited {ticker} {reason}. The dollar result isn't known yet."
    elif money.startswith("+") and reason == "at target":
        head = f"Exited {ticker} at target, {money}."
    elif money.startswith("-"):
        head = f"Exited {ticker} {reason}, {money}."
    else:
        head = f"Exited {ticker} {reason}, {money}."
    parts = [head, held]
    if goal:
        parts.append(goal)
    return " ".join(parts)


def _claim(trade_id: str) -> bool:
    if not trade_id:
        return False
    try:
        sent = json.loads(SENT_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        sent = {}
    ids = list(sent.get("ids") or [])
    if trade_id in ids:
        return False
    ids.append(trade_id)
    SENT_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {"ids": ids[-5000:]}
    tmp = SENT_FILE.with_name(f"{SENT_FILE.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:6]}")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(tmp, SENT_FILE)
    return True


def maybe_notify(user_id: str, record: Dict[str, Any]) -> bool:
    """Phone the exit once per trade id. Failures never affect the close itself."""
    trade_id = str(record.get("trade_id") or "")
    if not _claim(trade_id):
        return False
    try:
        import phone_alerts
    except ImportError:  # pragma: no cover
        from . import phone_alerts  # type: ignore
    try:
        goal = goal_line(user_id)
    except Exception:
        goal = None
    message = format_message(record, goal)
    kind = "stop" if message_is_loss(record) else "fill"
    return bool(phone_alerts.notify(kind, f"Exited {record.get('ticker') or ''}".strip(), message))


def message_is_loss(record: Dict[str, Any]) -> bool:
    try:
        return float(record.get("net_realized_pnl")) < 0
    except (TypeError, ValueError):
        return False
