"""Hard dollar caps on REAL-MONEY orders. Applies only when live trading is armed.

Sandbox is never affected. When live is armed, every order that could open or
add to a position passes through `reserve()` before it reaches the broker:

  * PLUTO_LIVE_MAX_ORDER_USD  - the most one order may cost.
  * PLUTO_LIVE_MAX_DAILY_USD  - the most all opening orders may cost per New
                                York trading day, across every process.

Both must be set to a positive number. If either is missing or invalid the
guard refuses every opening order (fail closed), so arming live without caps
places nothing. Orders that reduce risk - exits, stop-losses, take-profits -
are never blocked by these caps.

The running total lives in DATA_DIR/live_exposure.json under an flock so the
four gunicorn workers share one count. Unclear outcomes stay counted (over-
counting is the safe side); only a definite broker rejection releases its
reservation. A retry of the same client_order_id is not counted twice.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parents[1]
MAX_ORDER_VAR = "PLUTO_LIVE_MAX_ORDER_USD"
MAX_DAILY_VAR = "PLUTO_LIVE_MAX_DAILY_USD"
_NY = ZoneInfo("America/New_York")
OPTION_MULTIPLIER = 100


class LiveLimitExceeded(Exception):
    """Raised before anything is sent to the broker; nothing was placed."""


def _data_dir() -> Path:
    return Path(os.environ.get("PLUTO_DATA_DIR", str(BASE_DIR / "data"))).resolve()


def _exposure_file() -> Path:
    directory = _data_dir()
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "live_exposure.json"


def _read_cap(name: str) -> Optional[float]:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def get_caps() -> Dict[str, Optional[float]]:
    return {"max_order_usd": _read_cap(MAX_ORDER_VAR), "max_daily_usd": _read_cap(MAX_DAILY_VAR)}


def caps_configured() -> bool:
    caps = get_caps()
    return caps["max_order_usd"] is not None and caps["max_daily_usd"] is not None


def _today() -> str:
    return datetime.now(_NY).date().isoformat()


@contextlib.contextmanager
def _locked(path: Path):
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.touch(exist_ok=True)
    with open(lock_path, "r+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _load(path: Path) -> Dict[str, Any]:
    today = _today()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict) or data.get("day") != today:
        return {"day": today, "total_usd": 0.0, "orders": {}}
    data.setdefault("total_usd", 0.0)
    data.setdefault("orders", {})
    return data


def _save(path: Path, data: Dict[str, Any]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def order_notional(order: Dict[str, Any]) -> float:
    """Dollar cost of one order at its limit price (options x100 per contract)."""
    quantity = float(order.get("quantity") or 0)
    price = float(order.get("limit_price") or 0)
    multiplier = OPTION_MULTIPLIER if order.get("option_strategy") or order.get("legs") else 1
    return quantity * price * multiplier


def reserve(order: Dict[str, Any]) -> float:
    """Checks the caps and records the spend. Raises LiveLimitExceeded if it would breach them.

    Returns the dollars reserved (0.0 when this exact client_order_id was already counted today).
    """
    caps = get_caps()
    if caps["max_order_usd"] is None or caps["max_daily_usd"] is None:
        raise LiveLimitExceeded(
            f"Live trading is armed but {MAX_ORDER_VAR} and {MAX_DAILY_VAR} are not both set to a positive "
            "dollar amount - refusing to place an opening order."
        )
    notional = order_notional(order)
    if notional <= 0:
        raise LiveLimitExceeded("Live order has no valid quantity x limit price - refusing to place it.")
    if notional > caps["max_order_usd"]:
        raise LiveLimitExceeded(
            f"Order cost ${notional:,.2f} exceeds the live per-order cap of ${caps['max_order_usd']:,.2f}."
        )
    client_order_id = str(order.get("client_order_id") or "")
    path = _exposure_file()
    with _locked(path):
        data = _load(path)
        if client_order_id and client_order_id in data["orders"]:
            return 0.0
        if data["total_usd"] + notional > caps["max_daily_usd"]:
            raise LiveLimitExceeded(
                f"Order cost ${notional:,.2f} would take today's live opening orders to "
                f"${data['total_usd'] + notional:,.2f}, over the daily cap of ${caps['max_daily_usd']:,.2f}."
            )
        data["total_usd"] = round(data["total_usd"] + notional, 2)
        if client_order_id:
            data["orders"][client_order_id] = round(notional, 2)
        _save(path, data)
    return notional


def release(order: Dict[str, Any]) -> None:
    """Gives back a reservation after a definite broker rejection (nothing was placed)."""
    client_order_id = str(order.get("client_order_id") or "")
    if not client_order_id:
        return
    path = _exposure_file()
    with _locked(path):
        data = _load(path)
        spent = data["orders"].pop(client_order_id, None)
        if spent is not None:
            data["total_usd"] = max(0.0, round(data["total_usd"] - spent, 2))
            _save(path, data)


def today_usage() -> Dict[str, Any]:
    """Read-only snapshot for the readiness report."""
    path = _exposure_file()
    with _locked(path):
        data = _load(path)
    caps = get_caps()
    return {"day": data["day"], "spent_usd": data["total_usd"], "order_count": len(data["orders"]), **caps}
