"""Decide whether an open equity position is missing a protective stop.

The 10-second monitor uses this to tell three cases apart:

- a working STOP order is already at the broker
- the app already knows a stop price (or the last unfilled STOP_LOSS in
  order history is still below the market) and may attach it
- no safe price is known, so the position is flagged and left alone

This module does not place, cancel, or modify orders.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

_EQUITY_SYMBOL = re.compile(r"^[A-Z]{1,5}$")
HISTORY_CACHE_SECONDS = 15 * 60


def is_equity_symbol(symbol: str) -> bool:
    return bool(_EQUITY_SYMBOL.match((symbol or "").strip().upper()))


def position_quantity(position: Dict[str, object]) -> float:
    try:
        return float(position.get("quantity") or 0)
    except (TypeError, ValueError):
        return 0.0


def position_last_price(position: Dict[str, object]) -> Optional[float]:
    for key in ("last_price", "lastPrice", "market_price", "marketPrice"):
        raw = position.get(key)
        if raw is None or raw == "":
            continue
        try:
            price = float(raw)
        except (TypeError, ValueError):
            continue
        if price > 0:
            return price
    return None


def symbols_with_working_stop(open_orders: List[Dict[str, object]]) -> set:
    found = set()
    for order in open_orders or []:
        symbol = str(order.get("symbol") or "").strip().upper()
        order_type = str(order.get("order_type") or "").upper()
        if symbol and "STOP" in order_type:
            found.add(symbol)
    return found


def _time_key(order: Dict[str, object]) -> float:
    try:
        return float(order.get("place_time") or 0)
    except (TypeError, ValueError):
        return 0.0


def latest_unfilled_long_stop(orders: List[Dict[str, object]], symbol: str) -> Optional[float]:
    """Newest unfilled STOP_LOSS sell for a long. A filled stop is an exit, not a price to reuse."""
    wanted = symbol.strip().upper()
    best_key = -1.0
    best_price: Optional[float] = None
    for order in orders or []:
        if str(order.get("symbol") or "").strip().upper() != wanted:
            continue
        if "STOP" not in str(order.get("order_type") or "").upper():
            continue
        side = str(order.get("side") or "SELL").upper()
        if side != "SELL":
            continue
        try:
            filled = float(order.get("filled_quantity") or 0)
        except (TypeError, ValueError):
            filled = 0.0
        if filled > 0 or str(order.get("status") or "").upper() == "FILLED":
            continue
        try:
            price = float(order.get("stop_price") or 0)
        except (TypeError, ValueError):
            continue
        if price <= 0:
            continue
        key = _time_key(order)
        if key >= best_key:
            best_key = key
            best_price = price
    return best_price


def stop_is_below_market(stop_price: float, last_price: Optional[float]) -> bool:
    """A long stop at or above the last price would be through the market. Refuse it."""
    if last_price is None or stop_price <= 0:
        return False
    return stop_price < last_price


def _cache_path() -> Path:
    root = Path(os.environ.get("PLUTO_DATA_DIR", str(Path(__file__).resolve().parents[1] / "data"))).resolve()
    path = root / "scanner"
    path.mkdir(parents=True, exist_ok=True)
    return path / "recovered_stop_prices.json"


def historical_stops_for_account(
    account_id: str,
    loader: Callable[[], List[Dict[str, object]]],
    *,
    ttl_seconds: int = HISTORY_CACHE_SECONDS,
) -> Dict[str, float]:
    """Map ticker -> last unfilled long-stop price. One history read is cached so the 10-second monitor does not pull 30 days of orders on every tick."""
    path = _cache_path()
    now = time.time()
    cached: Dict[str, object] = {}
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cached = {}
    if (
        isinstance(cached, dict)
        and cached.get("account_id") == account_id
        and now - float(cached.get("fetched_at") or 0) < ttl_seconds
        and isinstance(cached.get("stops"), dict)
    ):
        return {str(symbol): float(price) for symbol, price in cached["stops"].items()}

    try:
        history = loader()
    except Exception:
        if isinstance(cached, dict) and cached.get("account_id") == account_id and isinstance(cached.get("stops"), dict):
            return {str(symbol): float(price) for symbol, price in cached["stops"].items()}
        return {}

    symbols = {str(order.get("symbol") or "").strip().upper() for order in history}
    stops = {}
    for symbol in symbols:
        if not symbol:
            continue
        price = latest_unfilled_long_stop(history, symbol)
        if price:
            stops[symbol] = price
    body = {"account_id": account_id, "fetched_at": now, "stops": stops}
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(body), encoding="utf-8")
    os.replace(tmp, path)
    return stops
