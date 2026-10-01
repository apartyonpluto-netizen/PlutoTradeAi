"""Read-only comparison of the broker's records with the app's own.

The broker is the source of truth for positions and working orders. This
module never places, cancels or edits anything - it reports:

  matched                      open record and broker position agree on quantity
  quantity_mismatch            both exist, quantities differ
  record_only                  the app believes a position is open; the broker shows none
  broker_only                  the broker holds a position the app is not tracking
                               (a manual holding or an orphan) - left untouched
  protection_missing_at_broker the record says a protective stop is active; no
                               matching working order exists at the broker
  unprotected_at_broker        an open equity position the app tracks has no
                               working stop at the broker (and the record does
                               not claim one)

Only records from the CURRENT environment are compared (broker_env.py), each
against its own account. A failed broker read makes the whole run
"disconnected": nothing is inferred from a read that did not happen, and the
last successful comparison time is kept and shown.

Positions and orders can lag a fill by seconds, so one mismatch is a
difference to look at, not proof of an error; the monitor's own
position-absent logic corroborates across passes before acting."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import broker_env
import order_lifecycle as ol

DATA_DIR = Path(os.environ.get("PLUTO_DATA_DIR", str(Path(__file__).resolve().parents[1] / "data"))).resolve()
HISTORY_KEPT = 50

OPEN_STATES = {ol.ENTRY_PARTIALLY_FILLED, ol.ENTRY_FILLED, ol.PROTECTION_PENDING, ol.PROTECTION_CONFIRMED_ACTIVE, ol.PROTECTION_FAILED}
BROKER_STOP_EXPECTED = {ol.PROTECTION_CONFIRMED_ACTIVE}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _qty(value: Any) -> float:
    try:
        return abs(float(value or 0))
    except (TypeError, ValueError):
        return 0.0


def _record_symbol(record: Dict[str, Any]) -> str:
    if record.get("instrument_type") == "OPTION" and record.get("option_symbol"):
        return str(record["option_symbol"]).upper()
    return str(record.get("ticker") or "").upper()


def is_open(record: Dict[str, Any]) -> bool:
    state = record.get("lifecycle_state")
    if state:
        return state in OPEN_STATES
    return record.get("status") == "placed" and record.get("side") == "BUY"  # pre-lifecycle legacy long


def compare(records: List[Dict[str, Any]], positions_by_account: Dict[str, List[Dict[str, Any]]],
            open_orders_by_account: Dict[str, List[Dict[str, Any]]], *, environment: str,
            default_account: Optional[str] = None,
            history_by_account: Optional[Dict[str, List[Dict[str, Any]]]] = None) -> Dict[str, Any]:
    """Pure comparison. `records` may contain any environment; only
    `environment`'s open records for the given accounts are used."""
    items: List[Dict[str, Any]] = []
    for account_id, positions in positions_by_account.items():
        held: Dict[str, float] = {}
        quotes: Dict[str, Dict[str, Any]] = {}
        for position in positions:
            symbol = str(position.get("symbol") or "").upper()
            if symbol:
                held[symbol] = held.get(symbol, 0.0) + _qty(position.get("quantity"))
                quotes[symbol] = {"cost_price": position.get("cost_price"), "last_price": position.get("last_price")}
        mine = [r for r in broker_env.only_current(records, environment)
                if is_open(r) and (r.get("account_id") == account_id
                                   or (not r.get("account_id") and account_id == (default_account or next(iter(positions_by_account)))))]
        expected: Dict[str, List[Dict[str, Any]]] = {}
        for record in mine:
            expected.setdefault(_record_symbol(record), []).append(record)
        working = open_orders_by_account.get(account_id, [])
        for symbol in sorted(set(held) | set(expected)):
            recs = expected.get(symbol, [])
            broker_qty = held.get(symbol, 0.0)
            record_qty = sum(_qty(r.get("filled_quantity") or r.get("quantity")) for r in recs)
            base = {"account_id": account_id, "symbol": symbol, "broker_quantity": broker_qty, "record_quantity": record_qty,
                    "record_ids": [r.get("record_id") or r.get("entry_client_order_id") for r in recs],
                    "instrument_type": recs[0].get("instrument_type") if recs else None,
                    "broker_cost_price": quotes.get(symbol, {}).get("cost_price"),
                    "broker_last_price": quotes.get(symbol, {}).get("last_price")}
            if recs and broker_qty == 0:
                evidence = closing_fills(recs, (history_by_account or {}).get(account_id) or [])
                items.append({**base, "category": "record_only", "broker_close_evidence": evidence,
                              "detail": ("broker history shows the position was closed: " + "; ".join(
                                  f"{e['order_type']} {e['side']} {e['filled_quantity']:g} @ {e['filled_price']} on {e['filled_at'][:16]}" for e in evidence))
                              if evidence else
                              "the app has an open position recorded; the broker shows none and its recent history has no closing fill"})
                continue
            if broker_qty and not recs:
                items.append({**base, "category": "broker_only",
                              "detail": "the broker holds this position but the app is not tracking it (manual holding or orphan) - not touched"})
                continue
            items.append({**base, "category": "matched" if abs(broker_qty - record_qty) < 1e-9 else "quantity_mismatch",
                          "detail": None if abs(broker_qty - record_qty) < 1e-9 else f"broker {broker_qty:g} vs recorded {record_qty:g}"})
            equity = [r for r in recs if r.get("instrument_type") != "OPTION"]  # option exits are app-monitored
            if not equity or not broker_qty:
                continue
            stop_ids = {r.get("stop_client_order_id") for r in equity if r.get("stop_client_order_id")}
            has_stop = any(
                o.get("client_order_id") in stop_ids
                or (str(o.get("symbol") or "").upper() == symbol and "STOP" in str(o.get("order_type") or "").upper())
                for o in working
            )
            if has_stop:
                continue
            if any(r.get("lifecycle_state") in BROKER_STOP_EXPECTED for r in equity):
                items.append({**base, "category": "protection_missing_at_broker",
                              "detail": "record says a protective stop is active, but no matching working stop order is at the broker"})
            else:
                items.append({**base, "category": "unprotected_at_broker",
                              "detail": "open at the broker with no working stop order"})
    counts: Dict[str, int] = {}
    for item in items:
        counts[item["category"]] = counts.get(item["category"], 0) + 1
    differences = [i for i in items if i["category"] != "matched"]
    return {"environment": environment, "items": items, "counts": counts, "differences": len(differences),
            "status": "differences" if differences else "ok"}


def _entry_time(record: Dict[str, Any]) -> str:
    history = record.get("lifecycle_history") or []
    return str((history[0] or {}).get("at") if history else record.get("logged_at") or "")


def closing_fills(records: List[Dict[str, Any]], history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Filled broker orders that would have closed these records' position:
    same symbol, the opposite side, filled after the entry."""
    out = []
    for record in records:
        symbol = _record_symbol(record)
        closing_side = "BUY" if record.get("direction") == "short" else "SELL"
        since = _entry_time(record)
        for order in history:
            if (str(order.get("symbol") or "").upper() == symbol and str(order.get("side") or "").upper() == closing_side
                    and str(order.get("status") or "").upper() == "FILLED" and str(order.get("filled_time_at") or "") > since):
                out.append({"order_type": order.get("order_type"), "side": closing_side, "filled_quantity": _qty(order.get("filled_quantity")),
                            "filled_price": order.get("filled_price"), "filled_at": str(order.get("filled_time_at") or ""),
                            "client_order_id": order.get("client_order_id")})
    return sorted(out, key=lambda e: e["filled_at"])


def _file(user_id: str) -> Path:
    path = DATA_DIR / "users" / user_id
    path.mkdir(parents=True, exist_ok=True)
    return path / "broker_reconciliation.json"


def load(user_id: str) -> Dict[str, Any]:
    try:
        return json.loads(_file(user_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"latest": None, "last_success_at": None, "history": []}


def _save(user_id: str, report: Dict[str, Any]) -> Dict[str, Any]:
    stored = load(user_id)
    if report["status"] != "disconnected":
        stored["last_success_at"] = report["checked_at"]
        stored["last_success"] = report
    stored["latest"] = report
    stored["history"] = ([{k: report.get(k) for k in ("run_id", "checked_at", "status", "differences", "counts", "environment")}]
                         + list(stored.get("history") or []))[:HISTORY_KEPT]
    path = _file(user_id)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}")
    tmp.write_text(json.dumps(stored, indent=1, default=str), encoding="utf-8")
    os.replace(tmp, path)
    return stored


def run(user_id: str, *, accounts: List[str], records: List[Dict[str, Any]],
        read_positions: Callable[[str], List[Dict[str, Any]]], read_open_orders: Callable[[str], List[Dict[str, Any]]],
        account_error: Optional[str] = None, read_history: Optional[Callable[[str], List[Dict[str, Any]]]] = None,
        read_balance: Optional[Callable[[str], Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Reads the broker for each account (read-only), compares, persists."""
    environment = broker_env.current()["environment"]
    checked_at = _now()
    positions: Dict[str, List[Dict[str, Any]]] = {}
    orders: Dict[str, List[Dict[str, Any]]] = {}
    histories: Dict[str, List[Dict[str, Any]]] = {}
    balances: Dict[str, Dict[str, Any]] = {}
    history_notes: List[str] = []
    errors: List[str] = [f"could not list broker accounts: {account_error}"] if account_error else []
    for account_id in accounts:
        try:
            positions[account_id] = read_positions(account_id)
            orders[account_id] = read_open_orders(account_id)
        except Exception as error:  # noqa: BLE001 - reported as disconnected, never guessed
            errors.append(f"account ...{str(account_id)[-4:]}: {type(error).__name__}: {str(error)[:160]}")
            continue
        if read_balance is not None:
            try:
                raw = read_balance(account_id) or {}
                balances[f"...{str(account_id)[-4:]}"] = {
                    "net_liquidation": raw.get("total_net_liquidation_value"),
                    "cash": raw.get("total_cash_balance") or raw.get("cash_balance"),
                    "day_profit_loss": raw.get("total_day_profit_loss"),
                    "currency": raw.get("currency") or "USD",
                }
            except Exception as error:  # noqa: BLE001 - balances are informational here
                history_notes.append(f"balance unavailable for ...{str(account_id)[-4:]}: {type(error).__name__}")
        if read_history is not None:
            try:
                histories[account_id] = read_history(account_id)
            except Exception as error:  # noqa: BLE001 - history only explains differences; its absence is noted
                history_notes.append(f"order history unavailable for ...{str(account_id)[-4:]}: {type(error).__name__}")
    if errors or not accounts:
        report = {"environment": environment, "status": "disconnected", "items": [], "counts": {}, "differences": None,
                  "errors": errors or ["no broker account found for this environment"]}
    else:
        report = compare(records, positions, orders, environment=environment, default_account=accounts[0], history_by_account=histories)
        report["errors"] = []
        report["notes"] = history_notes
        report["balances"] = balances
    report.update({"run_id": uuid.uuid4().hex[:12], "checked_at": checked_at, "accounts": [f"...{str(a)[-4:]}" for a in accounts],
                   "broker": "webull"})
    return _save(user_id, report)
