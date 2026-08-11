from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("PLUTO_DATA_DIR", str(BASE_DIR / "data"))).resolve()
USER_DATA_ROOT = DATA_DIR / "users"


def _store_file(user_id: str) -> Path:
    if not user_id:
        raise ValueError("user_id is required.")
    path = USER_DATA_ROOT / user_id
    path.mkdir(parents=True, exist_ok=True)
    return path / "webull_active_stops.json"


def _read(user_id: str) -> Dict[str, List[Dict[str, str]]]:
    store_file = _store_file(user_id)
    if not store_file.exists():
        return {}
    try:
        data = json.loads(store_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _write(user_id: str, data: Dict[str, List[Dict[str, str]]]) -> None:
    _store_file(user_id).write_text(json.dumps(data, indent=2), encoding="utf-8")


def record_exit_order(user_id: str, ticker: str, order_id: str, order_type: str) -> None:
    """A position can have more than one resting broker-side exit order at
    once - a STOP_LOSS and a take-profit LIMIT riding together as a manual
    bracket (Webull's OpenAPI OTOCO combo type would link them so one fill
    auto-cancels the other, but that combo wire format hasn't been verified
    here, so the app reconciles stale legs itself instead - see
    _reconcile_exit_orders in app.py). Track every resting order, tagged by
    type, so closing the position by any route can find and cancel all of
    them instead of leaving a stale one behind that could later sell shares
    that are no longer held."""
    data = _read(user_id)
    ticker = ticker.strip().upper()
    data.setdefault(ticker, [])
    if not any(order["id"] == order_id for order in data[ticker]):
        data[ticker].append({"id": order_id, "type": order_type})
    _write(user_id, data)


def pop_exit_orders(user_id: str, ticker: str) -> List[Dict[str, str]]:
    """Returns and clears every tracked exit order (stop-loss and/or
    take-profit) for a ticker - call this whenever the position is found to
    be closed, whether closed manually or because one of the legs already
    filled at the broker, so the caller can cancel whatever's left resting."""
    data = _read(user_id)
    ticker = ticker.strip().upper()
    order_ids = data.pop(ticker, [])
    if order_ids:
        _write(user_id, data)
    return order_ids


def pop_exit_orders_by_type(user_id: str, ticker: str, order_type: str) -> List[Dict[str, str]]:
    """Like pop_exit_orders, but only removes/returns orders of one leg type
    - used when refreshing a single leg (e.g. re-pricing the stop after a
    confidence drop) so a resting take-profit order for the same ticker is
    left completely untouched."""
    data = _read(user_id)
    ticker = ticker.strip().upper()
    all_orders = data.get(ticker, [])
    matching = [order for order in all_orders if order.get("type") == order_type]
    if not matching:
        return []
    remaining = [order for order in all_orders if order.get("type") != order_type]
    if remaining:
        data[ticker] = remaining
    else:
        data.pop(ticker, None)
    _write(user_id, data)
    return matching


def tracked_tickers(user_id: str) -> List[str]:
    """Every ticker that currently has at least one resting exit order
    tracked - used to spot stale legs for positions that closed on their own."""
    return list(_read(user_id).keys())
