"""Every scanner suggestion, taken or skipped, with the features known then.

Rows live under PLUTO_DATA_DIR/research/suggestion_ledger.jsonl. The daily
snapshot tars that directory, and the off-site upload sends the snapshot,
so this file is in the same R2 backup as the other JSON stores.

Grading waits five weekdays, then fills the forward return from bars that
exist only after the suggestion. Nothing here places an order.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("PLUTO_DATA_DIR", str(BASE_DIR / "data"))).resolve()
LEDGER_FILE = DATA_DIR / "research" / "suggestion_ledger.jsonl"
MAX_RECORDS = 8000
GRADE_WEEKDAYS = 5

FEATURE_KEYS = (
    "current_price",
    "day_change_percent",
    "gap_percent",
    "relative_volume",
    "ema_9",
    "ema_20",
    "ema_50",
    "ema_200",
    "rsi_14",
    "vwap",
    "extended_trend",
)

BUCKETS = (
    (0, 49, "0-49"),
    (50, 64, "50-64"),
    (65, 74, "65-74"),
    (75, 84, "75-84"),
    (85, 99, "85-99"),
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _grade_on(trading_day: str) -> str:
    try:
        day = datetime.fromisoformat(str(trading_day)[:10]).date()
    except ValueError:
        day = _now().date()
    left = GRADE_WEEKDAYS
    while left:
        day += timedelta(days=1)
        if day.weekday() < 5:
            left -= 1
    return day.isoformat()


def features_from(signal_snapshot: Optional[Dict[str, Any]], confidence: Optional[int] = None) -> Dict[str, Any]:
    """Only fields already on the suggestion. No later prices."""
    context = {}
    if isinstance(signal_snapshot, dict):
        raw = signal_snapshot.get("market_context")
        if isinstance(raw, dict):
            context = raw
    out: Dict[str, Any] = {}
    for key in FEATURE_KEYS:
        if key in context and context[key] is not None:
            out[key] = context[key]
    if confidence is not None:
        out["confidence"] = int(confidence)
    return out


@contextlib.contextmanager
def _locked(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.touch(exist_ok=True)
    with open(lock_path, "r+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _read_all() -> List[Dict[str, Any]]:
    if not LEDGER_FILE.exists():
        return []
    rows = []
    for line in LEDGER_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _write_all(rows: List[Dict[str, Any]]) -> None:
    LEDGER_FILE.parent.mkdir(parents=True, exist_ok=True)
    kept = rows[-MAX_RECORDS:]
    tmp = LEDGER_FILE.with_name(f"{LEDGER_FILE.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:6]}")
    tmp.write_text("".join(json.dumps(row, separators=(",", ":")) + "\n" for row in kept), encoding="utf-8")
    os.replace(tmp, LEDGER_FILE)


def append(record: Dict[str, Any]) -> Dict[str, Any]:
    row = {
        "id": str(record.get("id") or uuid.uuid4().hex),
        "recorded_at": record.get("recorded_at") or _now().isoformat(),
        "trading_day": str(record.get("trading_day") or _now().date().isoformat()),
        "user_id": record.get("user_id"),
        "ticker": str(record.get("ticker") or "").upper(),
        "strategy": record.get("strategy"),
        "recommendation": record.get("recommendation"),
        "confidence": record.get("confidence"),
        "decision": record.get("decision") or "skipped",
        "skip_category": record.get("skip_category"),
        "reason": record.get("reason"),
        "features": record.get("features") if isinstance(record.get("features"), dict) else {},
        "market_intel": record.get("market_intel") if isinstance(record.get("market_intel"), dict) else None,
        "entry": record.get("entry"),
        "stop": record.get("stop"),
        "target": record.get("target"),
        "quantity": record.get("quantity"),
        "regime": record.get("regime"),
        "reentry": bool(record.get("reentry")),
        "confirm_expires_at": record.get("confirm_expires_at"),
        "grade_on": record.get("grade_on") or _grade_on(str(record.get("trading_day") or "")),
        "graded_at": None,
        "forward_return_percent": None,
        "forward_pnl_dollars": None,
        "hit_target": None,
        "hit_stop": None,
        "what_if_sent": False,
    }
    with _locked(LEDGER_FILE):
        rows = _read_all()
        rows.append(row)
        _write_all(rows)
    return row


def update(suggestion_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
    if not suggestion_id:
        return None
    found = None
    with _locked(LEDGER_FILE):
        rows = _read_all()
        for row in rows:
            if row.get("id") == suggestion_id:
                row.update(fields)
                found = row
                break
        if found is not None:
            _write_all(rows)
    return found


def list_rows(user_id: Optional[str] = None) -> List[Dict[str, Any]]:
    rows = _read_all()
    if user_id is None:
        return rows
    return [row for row in rows if row.get("user_id") == user_id]


def _direction_sign(recommendation: str) -> float:
    return -1.0 if str(recommendation or "").upper() == "PUT" else 1.0


def _as_date(value: Any):
    text = str(value)[:10]
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        return None


def grade_row(row: Dict[str, Any], bars: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Score one suggestion from bars dated on or after its trading day.

    `bars` items are {date, open, high, low, close}. The entry reference is
    the suggestion's own entry (or that day's close). The exit is the close
    GRADE_WEEKDAYS sessions later. Stop is assumed before target in a bar.
    """
    start = _as_date(row.get("trading_day"))
    if start is None:
        return None
    ordered = sorted(
        (bar for bar in bars if _as_date(bar.get("date")) and _as_date(bar.get("date")) >= start),
        key=lambda bar: str(bar.get("date")),
    )
    if len(ordered) <= GRADE_WEEKDAYS:
        return None
    sign = _direction_sign(str(row.get("recommendation") or ""))
    entry = row.get("entry")
    try:
        entry_price = float(entry) if entry not in (None, "") else float(ordered[0]["close"])
    except (TypeError, ValueError, KeyError):
        return None
    if entry_price <= 0:
        return None
    window = ordered[: GRADE_WEEKDAYS + 1]
    exit_price = float(window[-1]["close"])
    hit_stop = False
    hit_target = False
    stop = row.get("stop")
    target = row.get("target")
    try:
        stop_price = float(stop) if stop not in (None, "") else None
    except (TypeError, ValueError):
        stop_price = None
    try:
        target_price = float(target) if target not in (None, "") else None
    except (TypeError, ValueError):
        target_price = None
    for bar in window[1:]:
        high = float(bar["high"])
        low = float(bar["low"])
        if sign > 0:
            stopped = stop_price is not None and low <= stop_price
            targeted = target_price is not None and high >= target_price
        else:
            stopped = stop_price is not None and high >= stop_price
            targeted = target_price is not None and low <= target_price
        if stopped:
            hit_stop = True
            exit_price = stop_price
            break
        if targeted:
            hit_target = True
            exit_price = target_price
            break
    forward = sign * (exit_price - entry_price) / entry_price * 100.0
    dollars = None
    try:
        quantity = float(row.get("quantity") or 0)
    except (TypeError, ValueError):
        quantity = 0
    if quantity > 0:
        dollars = round(sign * (exit_price - entry_price) * quantity, 2)
    return {
        "graded_at": _now().isoformat(),
        "forward_return_percent": round(forward, 4),
        "forward_pnl_dollars": dollars,
        "hit_target": hit_target,
        "hit_stop": hit_stop,
    }


def expire_unconfirmed(now: Optional[datetime] = None) -> int:
    """Awaiting buttons whose time ran out become skipped, so they can be graded."""
    moment = now or _now()
    changed = 0
    with _locked(LEDGER_FILE):
        rows = _read_all()
        for row in rows:
            if row.get("decision") != "awaiting_confirm":
                continue
            exp = row.get("confirm_expires_at")
            try:
                deadline = datetime.fromisoformat(str(exp))
            except (TypeError, ValueError):
                continue
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=timezone.utc)
            if moment >= deadline:
                row["decision"] = "skipped"
                row["skip_category"] = "confirm_expired"
                row["reason"] = "confirm button expired before it was used"
                changed += 1
        if changed:
            _write_all(rows)
    return changed


def grade_due(
    bars_for: Callable[[str], List[Dict[str, Any]]],
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Grade suggestions whose grade_on date has arrived. Returns the newly graded rows."""
    moment = now or _now()
    today = moment.date().isoformat()
    expire_unconfirmed(moment)
    graded: List[Dict[str, Any]] = []
    cache: Dict[str, List[Dict[str, Any]]] = {}
    with _locked(LEDGER_FILE):
        rows = _read_all()
        dirty = False
        for row in rows:
            if row.get("graded_at") or str(row.get("grade_on") or "") > today:
                continue
            ticker = str(row.get("ticker") or "")
            if not ticker:
                continue
            if ticker not in cache:
                try:
                    cache[ticker] = list(bars_for(ticker) or [])
                except Exception:
                    cache[ticker] = []
            outcome = grade_row(row, cache[ticker])
            if outcome is None:
                continue
            row.update(outcome)
            try:
                import market_intel

                market_intel.stamp_grades(row)
            except Exception:
                pass
            graded.append(dict(row))
            dirty = True
        if dirty:
            _write_all(rows)
    return graded


def what_if_line(row: Dict[str, Any]) -> Optional[str]:
    """A one-line follow-up for a suggestion that was not taken."""
    if row.get("decision") == "taken" or not row.get("graded_at"):
        return None
    ticker = str(row.get("ticker") or "The setup")
    dollars = row.get("forward_pnl_dollars")
    if dollars is None:
        pct = row.get("forward_return_percent")
        if pct is None:
            return None
        return f"{ticker} setup you skipped would have returned {float(pct):+.1f}%."
    amount = abs(float(dollars))
    if float(dollars) >= 0:
        return f"{ticker} setup you skipped would have made +${amount:.0f}."
    return f"{ticker} setup you skipped would have lost ${amount:.0f}."


def pending_what_ifs(user_id: Optional[str] = None) -> List[Dict[str, Any]]:
    lines = []
    for row in list_rows(user_id):
        text = what_if_line(row)
        if text and not row.get("what_if_sent"):
            lines.append({"id": row["id"], "user_id": row.get("user_id"), "text": text})
    return lines


def mark_what_ifs_sent(ids: Iterable[str]) -> None:
    wanted = {str(item) for item in ids}
    if not wanted:
        return
    with _locked(LEDGER_FILE):
        rows = _read_all()
        for row in rows:
            if row.get("id") in wanted:
                row["what_if_sent"] = True
        _write_all(rows)


def hit_rates(user_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Hit rate of graded suggestions by the confidence known at the time."""
    buckets = {label: {"bucket": label, "n": 0, "hits": 0, "hit_rate": None} for _, _, label in BUCKETS}
    for row in list_rows(user_id):
        if not row.get("graded_at"):
            continue
        try:
            confidence = int(row.get("confidence") or 0)
        except (TypeError, ValueError):
            continue
        label = next((name for low, high, name in BUCKETS if low <= confidence <= high), None)
        if label is None:
            continue
        buckets[label]["n"] += 1
        forward = row.get("forward_return_percent")
        if forward is not None and float(forward) > 0:
            buckets[label]["hits"] += 1
    for item in buckets.values():
        if item["n"]:
            item["hit_rate"] = round(item["hits"] / item["n"], 4)
    return list(buckets.values())
