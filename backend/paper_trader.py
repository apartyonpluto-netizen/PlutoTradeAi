from __future__ import annotations

import csv
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yfinance as yf

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("PLUTO_DATA_DIR", str(BASE_DIR / "data"))).resolve()
USER_DATA_ROOT = DATA_DIR / "users"


def _paper_trades_file(user_id: str) -> Path:
    if not user_id:
        raise ValueError("user_id is required.")
    return USER_DATA_ROOT / user_id / "paper_trades.csv"

FIELDNAMES = [
    "id",
    "opened_at",
    "closed_at",
    "ticker",
    "direction",
    "quantity",
    "order_type",
    "entry_price",
    "exit_price",
    "pnl",
    "status",
    "reason",
    "confidence",
]

_VALID_ORDER_TYPES = {"MARKET", "LIMIT"}

_LONG_DIRECTIONS = {"CALL", "BUY"}
_VALID_DIRECTIONS = {"CALL", "PUT", "BUY", "SELL"}


def _get_live_price(ticker: str) -> float:
    client = yf.Ticker(ticker)
    try:
        price = client.fast_info.get("lastPrice")
        if price:
            return float(price)
    except Exception:
        pass
    history = client.history(period="1d")
    if history.empty or "Close" not in history.columns:
        raise ValueError(f"No live price available for {ticker}.")
    close_prices = history["Close"].dropna()
    if close_prices.empty:
        raise ValueError(f"No live price available for {ticker}.")
    return float(close_prices.iloc[-1])


def _ensure_file(user_id: str) -> Path:
    trades_file = _paper_trades_file(user_id)
    trades_file.parent.mkdir(parents=True, exist_ok=True)
    if not trades_file.exists():
        with trades_file.open("w", newline="", encoding="utf-8") as handle:
            csv.DictWriter(handle, fieldnames=FIELDNAMES).writeheader()
    return trades_file


def _read_all(user_id: str) -> List[Dict[str, Any]]:
    trades_file = _ensure_file(user_id)
    with trades_file.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _write_all(user_id: str, rows: List[Dict[str, Any]]) -> None:
    trades_file = _ensure_file(user_id)
    with trades_file.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def open_trade(
    user_id: str,
    ticker: str,
    direction: str,
    quantity: float = 1,
    reason: str = "",
    confidence: Optional[int] = None,
    entry_price: Optional[float] = None,
    order_type: Optional[str] = None,
) -> Dict[str, Any]:
    symbol = ticker.strip().upper()
    if not symbol:
        raise ValueError("Ticker is required.")
    direction = direction.strip().upper()
    if direction not in _VALID_DIRECTIONS:
        raise ValueError("Direction must be one of CALL, PUT, BUY, SELL.")
    try:
        quantity = float(quantity)
    except (TypeError, ValueError):
        raise ValueError("Quantity must be a number.")
    if quantity <= 0:
        raise ValueError("Quantity must be positive.")

    order_type = (order_type or ("LIMIT" if entry_price is not None else "MARKET")).strip().upper()
    if order_type not in _VALID_ORDER_TYPES:
        raise ValueError("Order type must be MARKET or LIMIT.")
    if order_type == "LIMIT" and entry_price is None:
        raise ValueError("Limit orders require an entry price.")

    if entry_price is not None:
        try:
            entry_price = float(entry_price)
        except (TypeError, ValueError):
            raise ValueError("Entry price must be a number.")
        if entry_price <= 0:
            raise ValueError("Entry price must be positive.")
    else:
        entry_price = _get_live_price(symbol)
    row: Dict[str, Any] = {
        "id": uuid.uuid4().hex[:12],
        "opened_at": datetime.now(timezone.utc).isoformat(),
        "closed_at": "",
        "ticker": symbol,
        "direction": direction,
        "quantity": quantity,
        "order_type": order_type,
        "entry_price": round(entry_price, 4),
        "exit_price": "",
        "pnl": "",
        "status": "Open",
        "reason": reason,
        "confidence": confidence if confidence is not None else "",
    }
    rows = _read_all(user_id)
    rows.append(row)
    _write_all(user_id, rows)
    return row


def close_trade(user_id: str, trade_id: str, exit_price: Optional[float] = None) -> Dict[str, Any]:
    rows = _read_all(user_id)
    target = next((row for row in rows if row.get("id") == trade_id), None)
    if target is None:
        raise ValueError("Paper trade not found.")
    if target.get("status") != "Open":
        raise ValueError("Paper trade is already closed.")

    if exit_price is not None:
        try:
            exit_price = float(exit_price)
        except (TypeError, ValueError):
            raise ValueError("Exit price must be a number.")
        if exit_price <= 0:
            raise ValueError("Exit price must be positive.")
    else:
        exit_price = _get_live_price(target["ticker"])
    entry_price = float(target["entry_price"])
    quantity = float(target["quantity"])
    direction_sign = 1 if target["direction"] in _LONG_DIRECTIONS else -1
    pnl = (exit_price - entry_price) * quantity * direction_sign

    target["exit_price"] = round(exit_price, 4)
    target["pnl"] = round(pnl, 2)
    target["status"] = "Closed"
    target["closed_at"] = datetime.now(timezone.utc).isoformat()
    _write_all(user_id, rows)
    return target


def list_trades(user_id: str) -> List[Dict[str, Any]]:
    return list(reversed(_read_all(user_id)))


def get_summary(user_id: str) -> Dict[str, Any]:
    rows = _read_all(user_id)
    today = datetime.now(timezone.utc).date().isoformat()
    entries_today = sum(1 for row in rows if str(row.get("opened_at", "")).startswith(today))
    closed = [row for row in rows if row.get("status") == "Closed" and row.get("pnl") not in (None, "")]
    wins = [row for row in closed if float(row["pnl"]) > 0]
    win_rate = f"{(len(wins) / len(closed) * 100):.0f}%" if closed else "n/a"
    total_pnl = round(sum(float(row["pnl"]) for row in closed), 2) if closed else "n/a"
    return {
        "entries_today": entries_today,
        "win_rate": win_rate,
        "total_pnl": total_pnl,
        "open_count": sum(1 for row in rows if row.get("status") == "Open"),
    }
