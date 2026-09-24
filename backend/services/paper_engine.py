from __future__ import annotations

import copy
import fcntl
import hashlib
import logging
import math
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from backend.services.market_data_service import get_stock_snapshot, normalize_ticker
from backend.services.storage import (
    CrossProcessRLock,
    StorageCorruptionError,
    atomic_write_json,
    data_file,
    finite_number,
    read_json_strict,
)


LOGGER = logging.getLogger("plutotrade.paper_engine")

TERMINAL_ORDER_STATUSES = {"FILLED", "CANCELLED", "REJECTED", "EXPIRED"}
OPEN_ORDER_STATUSES = {"NEW", "SUBMITTED", "ACCEPTED", "WORKING", "PARTIALLY_FILLED", "OPEN"}
TERMINAL_LIFECYCLE_STATES = {"REJECTED", "CANCELLED", "EXPIRED", "CLOSED"}
EXIT_ROLES = {"STOP", "TARGET", "MANUAL_EXIT"}

MSG_FROZEN = "New entries are frozen until uncertain broker state is resolved"
MSG_AUTONOMY_OFF = "Autonomy is OFF for new entries; monitoring remains active"
MSG_PRICE_UNAVAILABLE = "Current market price is unavailable or stale; cannot validate the entry"
# Rejections a human can retry later without changing the signal itself.
RETRYABLE_REJECTIONS = {MSG_FROZEN, MSG_AUTONOMY_OFF, MSG_PRICE_UNAVAILABLE}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def _as_float(value: Any, default: float = 0.0) -> float:
    number = finite_number(value)
    return number if number is not None else default


def _as_int(value: Any, default: int = 0) -> int:
    number = finite_number(value)
    if number is None:
        return default
    try:
        return int(round(number))
    except (OverflowError, ValueError):
        return default


def _first_positive(*values: Any) -> float:
    for value in values:
        number = finite_number(value)
        if number is not None and number > 0:
            return number
    return 0.0


def _stable_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _terminal_order(order: Optional[Dict[str, Any]]) -> bool:
    return bool(order) and str(order.get("status") or "").upper() in TERMINAL_ORDER_STATUSES


def _open_order(order: Optional[Dict[str, Any]]) -> bool:
    return bool(order) and str(order.get("status") or "").upper() in OPEN_ORDER_STATUSES


def _copy_payload(payload: Any) -> Any:
    return copy.deepcopy(payload)


def _env_flag(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


class BrokerStateError(RuntimeError):
    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


class SimulatedPaperBroker:
    """File-backed simulated broker.

    Orders and positions are persisted (when state_path is given) so they survive restarts and
    are shared safely between processes. Resting exit orders are evaluated against the market
    price every time state is fetched: MARKET orders fill immediately, LIMIT (target) orders fill
    at their limit once price reaches it, STOP orders fill at the market once price crosses them.
    A fill can only close an existing position of the opposite side - it can never open or flip one.
    """

    def __init__(
        self,
        immediate_fill: bool = True,
        *,
        state_path: Optional[Any] = None,
        price_provider: Optional[Callable[[str], Optional[float]]] = None,
        fee_per_share: float = 0.0,
    ) -> None:
        self._immediate_fill = immediate_fill
        self._state_path = Path(state_path) if state_path else None
        self._price_provider = price_provider
        self._fee_per_share = max(0.0, _as_float(fee_per_share))
        if self._state_path:
            self._lock: Any = CrossProcessRLock(self._state_path.with_name(f"{self._state_path.name}.lock"))
        else:
            self._lock = threading.RLock()
        self._memory: Dict[str, Any] = self._empty_state()
        with self._lock:
            if self._state_path and not self._state_path.exists():
                self._save(self._memory)

    @staticmethod
    def _empty_state() -> Dict[str, Any]:
        return {"next_order_id": 1, "orders": {}, "positions": {}}

    def _load(self) -> Dict[str, Any]:
        if not self._state_path:
            return self._memory
        state = read_json_strict(self._state_path, self._empty_state)
        if not isinstance(state, dict):
            raise StorageCorruptionError(f"{self._state_path} does not contain a JSON object")
        state.setdefault("next_order_id", 1)
        state.setdefault("orders", {})
        state.setdefault("positions", {})
        return state

    def _save(self, state: Dict[str, Any]) -> None:
        if not self._state_path:
            self._memory = state
            return
        atomic_write_json(self._state_path, state)

    def _market_price(self, symbol: str) -> Optional[float]:
        if not self._price_provider:
            return None
        try:
            value = self._price_provider(symbol)
        except Exception:  # noqa: BLE001 - a price outage must never break order handling
            return None
        number = finite_number(value)
        return number if number is not None and number > 0 else None

    # ------------------------------------------------------------------ orders

    def submit_entry_order(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            state = self._load()
            existing = self._find_by_client_order_id(state, payload.get("client_order_id"))
            if existing:
                return _copy_payload(existing)
            qty = max(0, _as_int(payload.get("quantity")))
            price = _as_float(payload.get("price"), 0.0)
            direction = str(payload.get("direction") or "LONG").upper()
            filled = self._immediate_fill and qty > 0 and price > 0
            order = self._new_order(
                state,
                symbol=payload.get("symbol"),
                client_order_id=payload.get("client_order_id"),
                role="ENTRY",
                side="BUY" if direction == "LONG" else "SELL",
                quantity=qty,
                price=price,
                status="SUBMITTED",
            )
            if filled:
                self._fill_order(state, order, qty, price)
            elif qty <= 0 or price <= 0:
                order["status"] = "REJECTED"
            self._save(state)
            return _copy_payload(order)

    def submit_exit_order(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            state = self._load()
            existing = self._find_by_client_order_id(state, payload.get("client_order_id"))
            if existing:
                return _copy_payload(existing)
            order = self._new_order(
                state,
                symbol=payload.get("symbol"),
                client_order_id=payload.get("client_order_id"),
                role=str(payload.get("role") or "EXIT").upper(),
                side=str(payload.get("side") or "SELL").upper(),
                quantity=max(0, _as_int(payload.get("quantity"))),
                price=_as_float(payload.get("price"), 0.0),
                status="SUBMITTED",
                order_type=str(payload.get("order_type") or "LIMIT").upper(),
            )
            if order["requested_qty"] <= 0:
                order["status"] = "REJECTED"
            else:
                self._match_order(state, order)
            self._save(state)
            return _copy_payload(order)

    def cancel_order(self, order_id: str) -> Dict[str, Any]:
        with self._lock:
            state = self._load()
            order = state["orders"].get(order_id)
            if not order:
                raise BrokerStateError("MISSING_ORDER_ID", f"Unknown order_id {order_id}")
            if not _terminal_order(order):
                order["status"] = "CANCELLED"
                order["updated_at"] = utc_now_iso()
                self._save(state)
            return _copy_payload(order)

    def fetch_state(self, lifecycle: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            state = self._load()
            symbol = normalize_ticker(lifecycle.get("symbol") or "")
            changed = self._process_resting_orders(state, symbol)
            if changed:
                self._save(state)
            trade_id = str(lifecycle.get("trade_id") or "")
            entry_id = (lifecycle.get("entry") or {}).get("order_id")
            stop_id = ((lifecycle.get("protection") or {}).get("stop") or {}).get("order_id")
            target_id = ((lifecycle.get("protection") or {}).get("target") or {}).get("order_id")
            manual_exit_id = (lifecycle.get("manual_exit") or {}).get("order_id")
            orders_by_id = state["orders"]
            orders = [order for order in orders_by_id.values() if order.get("symbol") == symbol]
            open_orders = [order for order in orders if _open_order(order)]
            terminals = [order for order in orders if _terminal_order(order)]
            mine = [order for order in terminals if trade_id and str(order.get("client_order_id") or "").startswith(f"{trade_id}:")]
            others = [order for order in terminals if order not in mine][-10:]
            position = state["positions"].get(symbol) or {"symbol": symbol, "quantity": 0, "avg_price": None}
            protection: Dict[str, Any] = {
                "stop": _copy_payload(orders_by_id.get(stop_id)) if stop_id else None,
                "target": _copy_payload(orders_by_id.get(target_id)) if target_id else None,
            }
            if manual_exit_id:
                protection["manual_exit"] = _copy_payload(orders_by_id.get(manual_exit_id))
            return {
                "entry_order": _copy_payload(orders_by_id.get(entry_id)) if entry_id else None,
                "position_qty": _as_int(position.get("quantity")),
                "position_avg_price": position.get("avg_price"),
                "open_orders": [_copy_payload(order) for order in open_orders],
                "recent_terminal_orders": [_copy_payload(order) for order in others + mine],
                "protection": protection,
            }

    def list_positions(self) -> List[Dict[str, Any]]:
        with self._lock:
            state = self._load()
            return [_copy_payload(row) for row in state["positions"].values() if _as_int(row.get("quantity")) != 0]

    # ----------------------------------------------------------------- helpers

    def _new_order(
        self,
        state: Dict[str, Any],
        *,
        symbol: Any,
        client_order_id: Any,
        role: str,
        side: str,
        quantity: int,
        price: float,
        status: str,
        order_type: str = "LIMIT",
    ) -> Dict[str, Any]:
        order_id = f"SIM-{state['next_order_id']}"
        state["next_order_id"] += 1
        now = utc_now_iso()
        order = {
            "order_id": order_id,
            "client_order_id": client_order_id,
            "symbol": normalize_ticker(symbol or ""),
            "role": role,
            "side": side,
            "order_type": order_type,
            "status": status,
            "requested_qty": quantity,
            "filled_qty": 0,
            "remaining_qty": quantity,
            "avg_fill_price": None,
            "fees": 0.0,
            "price": round(price, 4) if price else price,
            "created_at": now,
            "updated_at": now,
        }
        state["orders"][order_id] = order
        return order

    @staticmethod
    def _find_by_client_order_id(state: Dict[str, Any], client_order_id: Any) -> Optional[Dict[str, Any]]:
        if client_order_id is None:
            return None
        for order in state["orders"].values():
            if str(order.get("client_order_id")) == str(client_order_id):
                return order
        return None

    def _fill_order(self, state: Dict[str, Any], order: Dict[str, Any], quantity: int, price: float) -> None:
        """Apply a fill of `quantity` at `price` to the order and to the symbol's position."""
        quantity = min(quantity, _as_int(order.get("remaining_qty")))
        if quantity <= 0:
            return
        sign = 1 if order["side"] == "BUY" else -1
        symbol = order["symbol"]
        position = state["positions"].get(symbol) or {"symbol": symbol, "quantity": 0, "avg_price": None}
        held = _as_int(position.get("quantity"))
        next_qty = held + sign * quantity
        if held == 0 or (held > 0) == (sign > 0):
            previous_notional = abs(held) * _as_float(position.get("avg_price"))
            position["avg_price"] = round((previous_notional + quantity * price) / abs(next_qty), 4) if next_qty else None
        elif next_qty == 0:
            position["avg_price"] = None
        position["quantity"] = next_qty
        state["positions"][symbol] = position

        previous_filled = _as_int(order.get("filled_qty"))
        previous_avg = _as_float(order.get("avg_fill_price"))
        total_filled = previous_filled + quantity
        order["avg_fill_price"] = round((previous_filled * previous_avg + quantity * price) / total_filled, 4)
        order["filled_qty"] = total_filled
        order["remaining_qty"] = max(0, _as_int(order.get("requested_qty")) - total_filled)
        order["fees"] = round(_as_float(order.get("fees")) + quantity * self._fee_per_share, 6)
        order["status"] = "FILLED" if order["remaining_qty"] == 0 else "PARTIALLY_FILLED"
        order["updated_at"] = utc_now_iso()

    def _match_order(self, state: Dict[str, Any], order: Dict[str, Any]) -> bool:
        """Try to fill a resting exit order. Returns True when the order changed."""
        if not _open_order(order) or order.get("role") == "ENTRY":
            return False
        symbol = order["symbol"]
        position = state["positions"].get(symbol) or {}
        held = _as_int(position.get("quantity"))
        closes_long = order["side"] == "SELL" and held > 0
        closes_short = order["side"] == "BUY" and held < 0
        if not (closes_long or closes_short):
            # Nothing to close on this side: the order can never be valid again.
            order["status"] = "CANCELLED" if order.get("role") in {"STOP", "TARGET"} else "REJECTED"
            order["updated_at"] = utc_now_iso()
            return True
        mark = self._market_price(symbol)
        order_type = str(order.get("order_type") or "LIMIT").upper()
        limit_price = _as_float(order.get("price"))
        fill_price: Optional[float] = None
        if order_type == "MARKET":
            fill_price = mark if mark is not None else (limit_price or None)
        elif mark is not None and limit_price > 0:
            if order_type == "STOP":
                if order["side"] == "SELL" and mark <= limit_price:
                    fill_price = mark
                elif order["side"] == "BUY" and mark >= limit_price:
                    fill_price = mark
            else:
                if order["side"] == "SELL" and mark >= limit_price:
                    fill_price = limit_price
                elif order["side"] == "BUY" and mark <= limit_price:
                    fill_price = limit_price
        if fill_price is None or not self._immediate_fill:
            return False
        self._fill_order(state, order, min(_as_int(order.get("remaining_qty")), abs(held)), fill_price)
        return True

    def _process_resting_orders(self, state: Dict[str, Any], symbol: str) -> bool:
        changed = False
        for order in sorted(state["orders"].values(), key=lambda row: _as_int(str(row.get("order_id", "0")).split("-")[-1])):
            if order.get("symbol") == symbol and _open_order(order):
                changed = self._match_order(state, order) or changed
        return changed


class InternalPaperEngine:
    def __init__(
        self,
        ledger_path: Optional[str] = None,
        broker: Optional[Any] = None,
        *,
        autonomy_enabled: Optional[bool] = None,
        monitor_interval_seconds: Optional[int] = None,
    ) -> None:
        self._ledger_path = Path(ledger_path) if ledger_path else data_file("PLUTO_PAPER_LEDGER_FILE", "paper_ledger.json")
        self._starting_balance = max(100.0, _as_float(os.getenv("PLUTO_PAPER_STARTING_BALANCE", "100000"), 100000.0))
        self._max_risk_per_trade = min(0.2, max(0.0, _as_float(os.getenv("PLUTO_MAX_RISK_PER_TRADE", "0.01"), 0.01)))
        self._max_position_percent = min(1.0, max(0.01, _as_float(os.getenv("PLUTO_MAX_POSITION_PERCENT", "0.10"), 0.10)))
        self._max_entry_deviation_percent = max(0.0, _as_float(os.getenv("PLUTO_MAX_ENTRY_DEVIATION_PERCENT", "3.0"), 3.0))
        self._auto_execution = _env_flag("PLUTO_AUTO_PAPER_EXECUTION", "false")
        self._autonomy_enabled = autonomy_enabled if autonomy_enabled is not None else _env_flag("PLUTO_AUTONOMY_ENABLED", "true")
        try:
            interval = int(monitor_interval_seconds if monitor_interval_seconds is not None else float(os.getenv("PLUTO_ORDER_MONITOR_INTERVAL_SECONDS", "45")))
        except (TypeError, ValueError):
            interval = 45
        self._monitor_interval_seconds = max(30, interval)
        if broker is None:
            explicit_broker_file = os.getenv("PLUTO_PAPER_BROKER_FILE")
            broker_path = Path(explicit_broker_file) if explicit_broker_file and ledger_path is None else self._ledger_path.with_name(f"{self._ledger_path.stem}.broker.json")
            broker = SimulatedPaperBroker(
                immediate_fill=True,
                state_path=broker_path,
                price_provider=self._broker_price,
                fee_per_share=_as_float(os.getenv("PLUTO_PAPER_FEE_PER_SHARE", "0"), 0.0),
            )
        self._broker = broker
        self._lock = CrossProcessRLock(self._ledger_path.with_name(f"{self._ledger_path.name}.lock"))
        self._monitor_run_lock = threading.Lock()
        self._monitor_lock_path = self._ledger_path.with_name(f"{self._ledger_path.stem}.monitor.lock")
        self._storage_error: Optional[str] = None
        try:
            self._ensure_ledger()
            self.resume_nonterminal_lifecycles()
        except StorageCorruptionError as exc:
            self._storage_error = str(exc)
            LOGGER.critical("Paper ledger is unusable, paper trading is disabled until it is repaired: %s", exc)

    # ---------------------------------------------------------------- storage

    def storage_health(self) -> Dict[str, Any]:
        if self._storage_error:
            return {"ok": False, "error": self._storage_error}
        try:
            with self._lock:
                self._load()
        except StorageCorruptionError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "error": None}

    def _default_ledger(self) -> Dict[str, Any]:
        now = utc_now_iso()
        return {
            "account": {
                "starting_balance": self._starting_balance,
                "cash": self._starting_balance,
                "realized_pl": 0.0,
                "updated_at": now,
            },
            "positions": [],
            "trades": [],
            "lifecycles": [],
            "alerts": [],
            "monitor": {
                "freeze_new_entries": False,
                "freeze_reason": None,
                "last_started_at": None,
                "last_completed_at": None,
                "duration_ms": None,
                "entries_checked": 0,
                "transitional_entries": 0,
                "failures_by_account": {},
                "missed_run_alert_id": None,
                "unhealthy": False,
                "overlap_skips": 0,
            },
            "equity_curve": [{"timestamp": now, "equity": self._starting_balance}],
        }

    def _normalize_ledger(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        defaults = self._default_ledger()
        merged = dict(defaults)
        merged.update(payload)
        merged["account"] = {**defaults["account"], **(payload.get("account") or {})}
        merged["monitor"] = {**defaults["monitor"], **(payload.get("monitor") or {})}
        for key in ("positions", "trades", "lifecycles", "alerts"):
            merged[key] = payload.get(key) or []
        merged["equity_curve"] = payload.get("equity_curve") or defaults["equity_curve"]
        return merged

    def _ensure_ledger(self) -> None:
        with self._lock:
            payload = read_json_strict(self._ledger_path, lambda: None)
            if payload is None:
                self._save(self._default_ledger())
                return
            if not isinstance(payload, dict):
                raise StorageCorruptionError(f"{self._ledger_path} does not contain a JSON object")
            self._save(self._normalize_ledger(payload))

    def _load(self) -> Dict[str, Any]:
        payload = read_json_strict(self._ledger_path, lambda: None)
        if payload is None:
            # The ledger vanished after startup. Recreating an empty account would silently erase
            # the financial history, so refuse unless this is genuinely a brand-new deployment.
            if self._storage_error is None and getattr(self, "_ledger_seen", False):
                raise StorageCorruptionError(f"{self._ledger_path} disappeared while the app was running")
            return self._default_ledger()
        if not isinstance(payload, dict):
            raise StorageCorruptionError(f"{self._ledger_path} does not contain a JSON object")
        self._ledger_seen = True
        return self._normalize_ledger(payload)

    def _save(self, payload: Dict[str, Any]) -> None:
        atomic_write_json(self._ledger_path, payload)
        self._ledger_seen = True

    # ---------------------------------------------------------------- prices

    def _latest_price(self, symbol: str, fallback: Optional[float] = None) -> Optional[float]:
        snapshot = get_stock_snapshot(symbol)
        if snapshot.get("success"):
            value = finite_number((snapshot.get("data") or {}).get("current_price"))
            if value is not None and value > 0:
                return value
        return fallback

    def _broker_price(self, symbol: str) -> Optional[float]:
        return self._latest_price(symbol)

    def _fresh_market_price(self, symbol: str) -> Optional[float]:
        snapshot = get_stock_snapshot(symbol)
        if not snapshot.get("success"):
            return None
        data = snapshot.get("data") or {}
        status = str(data.get("market_data_status") or snapshot.get("data_status") or "").upper()
        if status in {"STALE", "UNAVAILABLE"}:
            return None
        value = finite_number(data.get("current_price"))
        return value if value is not None and value > 0 else None

    def _equity_with_prices(self, ledger: Dict[str, Any]) -> Tuple[float, float]:
        cash = _as_float(ledger.get("account", {}).get("cash"))
        unrealized = 0.0
        market_value = 0.0
        for row in ledger.get("positions", []):
            if row.get("status") != "OPEN":
                row["unrealized_pl"] = 0.0
                continue
            signed_qty = _as_int(row.get("quantity"))
            direction = str(row.get("direction") or "LONG").upper()
            if direction == "SHORT" and signed_qty > 0:
                signed_qty = -signed_qty
            entry = _as_float(row.get("entry_price"))
            fallback = _as_float(row.get("last_mark_price")) or entry
            mark = _as_float(self._latest_price(row.get("symbol"), fallback=fallback), entry)
            row["last_mark_price"] = mark
            row["unrealized_pl"] = round((mark - entry) * signed_qty, 2)
            unrealized += row["unrealized_pl"]
            market_value += mark * signed_qty
        return round(cash + market_value, 2), round(unrealized, 2)

    def _short_market_value(self, ledger: Dict[str, Any]) -> float:
        total = 0.0
        for row in ledger.get("positions", []):
            if row.get("status") != "OPEN" or str(row.get("direction") or "LONG").upper() != "SHORT":
                continue
            mark = _as_float(row.get("last_mark_price")) or _as_float(row.get("entry_price"))
            total += mark * abs(_as_int(row.get("quantity")))
        return total

    def _buying_power(self, ledger: Dict[str, Any]) -> float:
        """Cash account, no leverage: short proceeds sit in cash but are collateral for the buy-back."""
        cash = _as_float(ledger.get("account", {}).get("cash"))
        return round(max(0.0, cash - self._short_market_value(ledger)), 2)

    def _record_equity(self, ledger: Dict[str, Any]) -> None:
        equity, _ = self._equity_with_prices(ledger)
        curve = ledger.get("equity_curve", [])
        curve.append({"timestamp": utc_now_iso(), "equity": equity})
        ledger["equity_curve"] = curve[-5000:]

    def _calc_quantity(self, equity: float, entry_price: float, stop_loss: Optional[float], buying_power: Optional[float] = None) -> int:
        if entry_price <= 0:
            return 0
        max_position_value = equity * self._max_position_percent
        max_qty = int(max_position_value // entry_price)
        if buying_power is not None:
            max_qty = min(max_qty, int(max(0.0, buying_power) // entry_price))
        if max_qty <= 0:
            return 0
        if stop_loss is None:
            return max_qty
        risk_per_share = abs(entry_price - stop_loss)
        if risk_per_share <= 0:
            return max_qty
        risk_budget = equity * self._max_risk_per_trade
        if risk_budget < 1.0:
            return 0
        max_qty_by_risk = int(risk_budget // risk_per_share)
        if max_qty_by_risk <= 0:
            return 0
        return min(max_qty, max_qty_by_risk)

    # ------------------------------------------------------------ identifiers

    def _trade_id(self, signal: Dict[str, Any]) -> str:
        symbol = normalize_ticker(signal.get("symbol") or "")
        direction = str(signal.get("direction") or "LONG").upper()
        strategy = str(signal.get("strategy") or "manual")
        signal_id = str(signal.get("signal_id") or "manual")
        return f"TRD-{symbol}-{_stable_hash(f'{signal_id}|{direction}|{strategy}')}"

    def _entry_client_order_id(self, trade_id: str) -> str:
        return f"{trade_id}:ENTRY:v1"

    def _exit_client_order_id(self, trade_id: str) -> str:
        return f"{trade_id}:MANUAL_EXIT:v1"

    def _protection_client_order_id(self, trade_id: str, kind: str, version: int) -> str:
        return f"{trade_id}:{kind.upper()}:v{version}"

    def _find_lifecycle(self, ledger: Dict[str, Any], trade_id: str) -> Optional[Dict[str, Any]]:
        for row in ledger.get("lifecycles", []):
            if row.get("trade_id") == trade_id:
                return row
        return None

    def _find_open_lifecycle_by_symbol(self, ledger: Dict[str, Any], symbol: str) -> Optional[Dict[str, Any]]:
        normalized = normalize_ticker(symbol)
        for row in ledger.get("lifecycles", []):
            if normalize_ticker(row.get("symbol") or "") != normalized:
                continue
            if row.get("state") not in TERMINAL_LIFECYCLE_STATES:
                return row
        return None

    def lifecycle_for_signal(self, signal_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            for row in self._load().get("lifecycles", []):
                if str(row.get("signal_id")) == str(signal_id):
                    return _copy_payload(row)
        return None

    # ----------------------------------------------------------------- alerts

    def _upsert_alert(self, ledger: Dict[str, Any], trade_id: str, category: str, message: str, *, freeze: bool) -> str:
        key = f"{trade_id}:{category}"
        now = utc_now_iso()
        for row in ledger.get("alerts", []):
            if row.get("alert_key") == key and row.get("status") != "RESOLVED":
                row["last_seen_at"] = now
                row["message"] = message
                row["dismissed"] = False
                row["freezes_entries"] = bool(row.get("freezes_entries")) or freeze
                if freeze:
                    ledger["monitor"]["freeze_new_entries"] = True
                    ledger["monitor"]["freeze_reason"] = message
                return str(row.get("alert_id"))
        alert_id = f"ALT-{_stable_hash(key)}-{_stable_hash(now)[:6]}"
        ledger.setdefault("alerts", []).append(
            {
                "alert_id": alert_id,
                "alert_key": key,
                "trade_id": trade_id,
                "category": category,
                "message": message,
                "status": "OPEN",
                "dismissed": False,
                "freezes_entries": freeze,
                "created_at": now,
                "last_seen_at": now,
            }
        )
        if freeze:
            ledger["monitor"]["freeze_new_entries"] = True
            ledger["monitor"]["freeze_reason"] = message
        return alert_id

    def _resolve_alert(self, ledger: Dict[str, Any], trade_id: str, category: str) -> None:
        key = f"{trade_id}:{category}"
        for row in ledger.get("alerts", []):
            if row.get("alert_key") == key and row.get("status") != "RESOLVED":
                row["status"] = "RESOLVED"
                row["resolved_at"] = utc_now_iso()

    def _refresh_freeze(self, ledger: Dict[str, Any]) -> None:
        """Entries stay frozen exactly as long as an unresolved freezing alert exists."""
        monitor = ledger.setdefault("monitor", {})
        blocking = [row for row in ledger.get("alerts", []) if row.get("status") != "RESOLVED" and row.get("freezes_entries", True)]
        if blocking:
            monitor["freeze_new_entries"] = True
            monitor["freeze_reason"] = monitor.get("freeze_reason") or blocking[-1].get("message")
        else:
            monitor["freeze_new_entries"] = False
            monitor["freeze_reason"] = None

    def dismiss_alert(self, alert_id: str) -> bool:
        with self._lock:
            ledger = self._load()
            for row in ledger.get("alerts", []):
                if row.get("alert_id") == alert_id:
                    row["dismissed"] = True
                    row["dismissed_at"] = utc_now_iso()
                    self._save(ledger)
                    return True
        return False

    def resolve_alert(self, alert_id: str, resolved_by: str = "operator", note: str = "") -> bool:
        """Operator acknowledgement that the underlying condition was fixed; recomputes the entry freeze."""
        with self._lock:
            ledger = self._load()
            for row in ledger.get("alerts", []):
                if row.get("alert_id") == alert_id and row.get("status") != "RESOLVED":
                    row["status"] = "RESOLVED"
                    row["resolved_at"] = utc_now_iso()
                    row["resolved_by"] = resolved_by
                    row["resolution_note"] = note[:500]
                    monitor = ledger.setdefault("monitor", {})
                    monitor["freeze_reason"] = None
                    self._refresh_freeze(ledger)
                    self._save(ledger)
                    return True
        return False

    def _upsert_trade_row(self, ledger: Dict[str, Any], row: Dict[str, Any]) -> None:
        trades = ledger.setdefault("trades", [])
        for idx, existing in enumerate(trades):
            if existing.get("trade_id") == row.get("trade_id"):
                trades[idx] = row
                return
        trades.append(row)

    def _upsert_position_row(self, ledger: Dict[str, Any], row: Dict[str, Any]) -> None:
        positions = ledger.setdefault("positions", [])
        for idx, existing in enumerate(positions):
            if existing.get("position_id") == row.get("position_id"):
                row["last_mark_price"] = existing.get("last_mark_price") or row.get("last_mark_price")
                positions[idx] = row
                return
        positions.append(row)

    def _archive_position_if_closed(self, ledger: Dict[str, Any], trade_id: str) -> None:
        for row in ledger.get("positions", []):
            if row.get("trade_id") == trade_id and row.get("status") == "OPEN":
                row["status"] = "CLOSED"
                row["closed_at"] = utc_now_iso()
                row["unrealized_pl"] = 0.0

    # ------------------------------------------------------------------ reads

    def account(self) -> Dict[str, Any]:
        with self._lock:
            ledger = self._load()
            equity, unrealized = self._equity_with_prices(ledger)
            account = ledger.get("account", {})
            open_rows = [p for p in ledger.get("positions", []) if p.get("status") == "OPEN"]
            return {
                "starting_balance": round(_as_float(account.get("starting_balance"), self._starting_balance), 2),
                "cash": round(_as_float(account.get("cash"), self._starting_balance), 2),
                "realized_pl": round(_as_float(account.get("realized_pl"), 0.0), 2),
                "unrealized_pl": unrealized,
                "equity": equity,
                "buying_power": self._buying_power(ledger),
                "short_market_value": round(self._short_market_value(ledger), 2),
                "open_positions": len(open_rows),
                "updated_at": utc_now_iso(),
            }

    def positions(self) -> List[Dict[str, Any]]:
        with self._lock:
            ledger = self._load()
            self._equity_with_prices(ledger)
            rows = list(ledger.get("positions", []))
            rows.sort(key=lambda row: row.get("updated_at") or row.get("created_at") or "", reverse=True)
            return rows

    def trades(self) -> List[Dict[str, Any]]:
        with self._lock:
            rows = list(self._load().get("trades", []))
            rows.sort(key=lambda row: row.get("updated_at") or row.get("created_at") or "", reverse=True)
            return rows

    def alerts(self) -> List[Dict[str, Any]]:
        with self._lock:
            rows = list(self._load().get("alerts", []))
            rows.sort(key=lambda row: row.get("last_seen_at") or row.get("created_at") or "", reverse=True)
            return rows

    def monitor_status(self) -> Dict[str, Any]:
        with self._lock:
            ledger = self._load()
            monitor = dict(ledger.get("monitor") or {})
            last_completed = monitor.get("last_completed_at")
            unhealthy = False
            if last_completed:
                try:
                    last = datetime.fromisoformat(str(last_completed).replace("Z", "+00:00"))
                    if last.tzinfo is None:
                        last = last.replace(tzinfo=timezone.utc)
                    unhealthy = (utc_now() - last).total_seconds() > (self._monitor_interval_seconds * 2.5)
                except ValueError:
                    unhealthy = True
            monitor["interval_seconds"] = self._monitor_interval_seconds
            monitor["unhealthy"] = unhealthy
            if unhealthy:
                self._mark_monitor_unhealthy(ledger)
                self._save(ledger)
                monitor = dict(ledger.get("monitor") or {})
                monitor["interval_seconds"] = self._monitor_interval_seconds
            return monitor

    def _monitor_lock_handle(self):
        self._monitor_lock_path.parent.mkdir(parents=True, exist_ok=True)
        return self._monitor_lock_path.open("a+", encoding="utf-8")

    def _mark_monitor_unhealthy(self, ledger: Dict[str, Any]) -> None:
        ledger.setdefault("monitor", {})["unhealthy"] = True
        if not ledger["monitor"].get("missed_run_alert_id"):
            alert_id = self._upsert_alert(
                ledger,
                "SYSTEM",
                "MONITOR_HEARTBEAT_MISSED",
                "Order monitor heartbeat missed expected cadence",
                freeze=True,
            )
            ledger["monitor"]["missed_run_alert_id"] = alert_id

    @staticmethod
    def _trade_net(row: Dict[str, Any]) -> float:
        value = row.get("net_realized_pl")
        return _as_float(value if value is not None else row.get("realized_pl"))

    def performance(self) -> Dict[str, Any]:
        with self._lock:
            ledger = self._load()
            closed = [row for row in ledger.get("trades", []) if row.get("status") == "CLOSED"]
            wins = [row for row in closed if self._trade_net(row) > 0]
            losses = [row for row in closed if self._trade_net(row) < 0]
            avg_win = sum(self._trade_net(row) for row in wins) / len(wins) if wins else 0.0
            avg_loss = sum(self._trade_net(row) for row in losses) / len(losses) if losses else 0.0
            win_rate = (len(wins) / len(closed)) if closed else 0.0
            loss_rate = (len(losses) / len(closed)) if closed else 0.0
            expectancy = (win_rate * avg_win) + (loss_rate * avg_loss)

            max_equity = 0.0
            max_drawdown = 0.0
            for point in ledger.get("equity_curve", []):
                eq = _as_float(point.get("equity"))
                max_equity = max(max_equity, eq)
                if max_equity > 0:
                    max_drawdown = max(max_drawdown, (max_equity - eq) / max_equity)

            account = self.account()
            return {
                "trades_total": len(closed),
                "wins": len(wins),
                "losses": len(losses),
                "win_rate": round(win_rate, 4),
                "loss_rate": round(loss_rate, 4),
                "average_win": round(avg_win, 2),
                "average_loss": round(avg_loss, 2),
                "expectancy": round(expectancy, 2),
                "maximum_drawdown": round(max_drawdown, 4),
                "equity": account.get("equity"),
                "realized_pl": account.get("realized_pl"),
                "unrealized_pl": account.get("unrealized_pl"),
                "equity_curve": ledger.get("equity_curve", [])[-200:],
            }

    # -------------------------------------------------------------- execution

    def execute_approved_signal(
        self,
        signal: Dict[str, Any],
        approved_by: str = "manual",
        *,
        validate_market: bool = False,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        direction = str(signal.get("direction") or "").upper()
        symbol = normalize_ticker(signal.get("symbol") or "")
        if direction not in {"LONG", "SHORT", "EXIT", "WATCH"}:
            return False, "Unsupported direction", None
        if direction == "WATCH":
            return False, "WATCH signals do not create positions", None
        if direction == "EXIT":
            return self._submit_manual_exit(signal, approved_by=approved_by)

        with self._lock:
            ledger = self._load()
            trade_id = self._trade_id(signal)
            existing = self._find_lifecycle(ledger, trade_id)
            if existing:
                return True, "Lifecycle already exists", _copy_payload(existing)
            if ledger.get("monitor", {}).get("freeze_new_entries"):
                return False, MSG_FROZEN, None
            if not self._autonomy_enabled:
                return False, MSG_AUTONOMY_OFF, None
            if self._find_open_lifecycle_by_symbol(ledger, symbol):
                return False, "Open position or lifecycle already exists for symbol", None
            tracked = {row.get("trade_id") for row in ledger.get("lifecycles", [])}
            if any(
                row.get("status") == "OPEN" and normalize_ticker(row.get("symbol") or "") == symbol and row.get("trade_id") not in tracked
                for row in ledger.get("positions", [])
            ):
                return False, "An untracked legacy paper position already exists for symbol; close it before opening a new one", None

            analysis = signal.get("analysis") or {}
            entry_price = _first_positive(analysis.get("entry_zone"), signal.get("price"))
            if entry_price <= 0:
                return False, "Entry price is required", None
            stop_raw = analysis.get("stop_loss") if analysis.get("stop_loss") is not None else signal.get("stop_loss")
            target_raw = analysis.get("primary_target") if analysis.get("primary_target") is not None else signal.get("take_profit")
            stop_loss_value = _as_float(stop_raw, 0.0) if stop_raw is not None else None
            target_value = _as_float(target_raw, 0.0) if target_raw is not None else None
            if stop_loss_value is None:
                return False, "A protective stop loss is required for every new entry", None
            if stop_loss_value is not None and (stop_loss_value <= 0 or (stop_loss_value >= entry_price if direction == "LONG" else stop_loss_value <= entry_price)):
                return False, "Stop loss must be positive and on the loss side of entry", None
            if target_value is not None and (target_value <= 0 or (target_value <= entry_price if direction == "LONG" else target_value >= entry_price)):
                return False, "Target must be positive and on the profit side of entry", None

            if validate_market and self._max_entry_deviation_percent > 0:
                market = self._fresh_market_price(symbol)
                if market is None:
                    return False, MSG_PRICE_UNAVAILABLE, None
                deviation = abs(market - entry_price) / entry_price * 100.0
                if deviation > self._max_entry_deviation_percent:
                    return False, (
                        f"Entry {entry_price:.2f} is {deviation:.1f}% away from the current market price {market:.2f} "
                        f"(limit {self._max_entry_deviation_percent:.1f}%); the signal is stale"
                    ), None

            equity, _ = self._equity_with_prices(ledger)
            quantity = self._calc_quantity(equity=equity, entry_price=entry_price, stop_loss=stop_loss_value, buying_power=self._buying_power(ledger))
            if quantity <= 0:
                return False, "Risk limits or buying power rejected this trade", None

            lifecycle = self._build_lifecycle(
                signal=signal,
                approved_by=approved_by,
                trade_id=trade_id,
                direction=direction,
                symbol=symbol,
                entry_price=entry_price,
                stop_loss=stop_loss_value,
                target=target_value,
                quantity=quantity,
            )
            # Persist the intent BEFORE talking to the broker: a crash right after submission then
            # leaves a lifecycle that the monitor can reconcile instead of an untracked broker order.
            ledger.setdefault("lifecycles", []).append(lifecycle)
            self._upsert_trade_row(ledger, self._trade_row_from_lifecycle(lifecycle))
            self._save(ledger)

            entry_order = self._broker.submit_entry_order(
                {
                    "symbol": symbol,
                    "direction": direction,
                    "quantity": quantity,
                    "price": entry_price,
                    "client_order_id": lifecycle["entry"]["client_order_id"],
                }
            )
            lifecycle["entry"]["order_id"] = entry_order.get("order_id")
            lifecycle["entry"]["status"] = str(entry_order.get("status") or "SUBMITTED").upper()
            lifecycle["entry"]["broker_state"] = _copy_payload(entry_order)
            self._upsert_trade_row(ledger, self._trade_row_from_lifecycle(lifecycle))
            self._save(ledger)

        self.monitor_once()

        with self._lock:
            updated = self._find_lifecycle(self._load(), trade_id)
            return True, "Entry submitted for lifecycle monitoring", _copy_payload(updated)

    def _submit_manual_exit(self, signal: Dict[str, Any], approved_by: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        symbol = normalize_ticker(signal.get("symbol") or "")
        with self._lock:
            ledger = self._load()
            lifecycle = self._find_open_lifecycle_by_symbol(ledger, symbol)
            if not lifecycle:
                if any(row.get("status") == "OPEN" and normalize_ticker(row.get("symbol") or "") == symbol for row in ledger.get("positions", [])):
                    return False, "Position pre-dates lifecycle tracking and cannot be exited through the broker; close it manually", None
                return False, "No open position to exit", None
            if lifecycle.get("manual_exit", {}).get("order_id"):
                return True, "Manual exit already submitted", _copy_payload(lifecycle)
            qty = abs(_as_int(lifecycle.get("position", {}).get("current_qty"))) or abs(_as_int(lifecycle.get("entry", {}).get("filled_qty")))
            if qty <= 0:
                return False, "No open quantity to exit", None
            direction = str(lifecycle.get("direction") or "LONG").upper()
            price = _first_positive(signal.get("price"), lifecycle.get("entry", {}).get("avg_fill_price"), lifecycle.get("entry", {}).get("price"))
            order = self._broker.submit_exit_order(
                {
                    "symbol": symbol,
                    "side": "SELL" if direction == "LONG" else "BUY",
                    "quantity": qty,
                    "price": price,
                    "client_order_id": self._exit_client_order_id(lifecycle["trade_id"]),
                    "role": "MANUAL_EXIT",
                    "order_type": "MARKET",
                }
            )
            lifecycle["manual_exit"] = {
                "order_id": order.get("order_id"),
                "client_order_id": order.get("client_order_id"),
                "submitted_at": utc_now_iso(),
                "approved_by": approved_by,
            }
            lifecycle["updated_at"] = utc_now_iso()
            # Any still-working remainder of the entry must not fill after the exit was requested.
            entry_order_id = lifecycle.get("entry", {}).get("order_id")
            if entry_order_id and _open_order(lifecycle.get("entry", {}).get("broker_state")):
                try:
                    self._broker.cancel_order(entry_order_id)
                except BrokerStateError:
                    pass
            self._save(ledger)

        self.monitor_once()
        with self._lock:
            ledger = self._load()
            updated = self._find_lifecycle(ledger, lifecycle["trade_id"])
            return True, "Manual exit submitted", _copy_payload(updated)

    def _build_lifecycle(
        self,
        *,
        signal: Dict[str, Any],
        approved_by: str,
        trade_id: str,
        direction: str,
        symbol: str,
        entry_price: float,
        stop_loss: Optional[float],
        target: Optional[float],
        quantity: int,
    ) -> Dict[str, Any]:
        now = utc_now_iso()
        return {
            "trade_id": trade_id,
            "position_id": f"POS-{trade_id}",
            "signal_id": signal.get("signal_id"),
            "symbol": symbol,
            "direction": direction,
            "state": "ENTRY_SUBMITTED",
            "approved_by": approved_by,
            "approved_at": now,
            "created_at": now,
            "updated_at": now,
            "strategy": signal.get("strategy"),
            "model_version": (signal.get("analysis") or {}).get("model_version") or signal.get("strategy"),
            "entry": {
                "order_id": None,
                "client_order_id": self._entry_client_order_id(trade_id),
                "requested_qty": quantity,
                "filled_qty": 0,
                "remaining_qty": quantity,
                "avg_fill_price": None,
                "status": "SUBMITTED",
                "price": round(entry_price, 4),
                "submitted_at": now,
                "broker_state": None,
            },
            "protection": {
                "desired_qty": 0,
                "confirmed": False,
                "status": "PENDING",
                "stop": {"order_id": None, "client_order_id": None, "price": round(stop_loss, 4) if stop_loss is not None else None, "version": 0, "filled_qty_seen": 0},
                "target": {"order_id": None, "client_order_id": None, "price": round(target, 4) if target is not None else None, "version": 0, "filled_qty_seen": 0},
            },
            "position": {"current_qty": 0, "avg_entry_price": None, "opened_at": None, "closed_at": None},
            "exit": {"type": None, "exited_qty": 0, "avg_exit_price": None, "filled_order_id": None, "filled_at": None},
            "accounting": self._new_accounting(),
            "monitoring": {
                "first_failed_at": None,
                "last_attempt_at": None,
                "attempt_count": 0,
                "last_error_category": None,
                "last_error_message": None,
                "last_confirmed_broker_state": None,
                "protection_confirmed": False,
                "new_entries_frozen": False,
            },
            "close_reason": None,
            "broker_evidence": {},
        }

    @staticmethod
    def _new_accounting() -> Dict[str, Any]:
        return {
            "entry_qty": 0,
            "entry_notional": 0.0,
            "entry_fees": 0.0,
            "exit_qty": 0,
            "exit_notional": 0.0,
            "exit_fees": 0.0,
            "realized_gross": 0.0,
            "exit_orders": {},
        }

    def _accounting(self, lifecycle: Dict[str, Any]) -> Dict[str, Any]:
        acct = lifecycle.get("accounting")
        if not isinstance(acct, dict):
            acct = self._new_accounting()
            legacy_qty = _as_int(lifecycle.get("booked_entry_qty"))
            if legacy_qty > 0:  # lifecycle created before per-fill accounting existed
                avg = _as_float(lifecycle.get("position", {}).get("avg_entry_price") or lifecycle.get("entry", {}).get("avg_fill_price"))
                acct["entry_qty"] = legacy_qty
                acct["entry_notional"] = legacy_qty * avg
            lifecycle["accounting"] = acct
        for key, value in self._new_accounting().items():
            acct.setdefault(key, value)
        return acct

    def _trade_row_from_lifecycle(self, lifecycle: Dict[str, Any]) -> Dict[str, Any]:
        acct = lifecycle.get("accounting") or {}
        return {
            "trade_id": lifecycle.get("trade_id"),
            "position_id": lifecycle.get("position_id"),
            "symbol": lifecycle.get("symbol"),
            "direction": lifecycle.get("direction"),
            "quantity": lifecycle.get("position", {}).get("current_qty") or lifecycle.get("entry", {}).get("filled_qty") or lifecycle.get("entry", {}).get("requested_qty"),
            "requested_entry_qty": lifecycle.get("entry", {}).get("requested_qty"),
            "filled_entry_qty": lifecycle.get("entry", {}).get("filled_qty"),
            "entry_price": lifecycle.get("entry", {}).get("avg_fill_price") or lifecycle.get("entry", {}).get("price"),
            "stop_loss": lifecycle.get("protection", {}).get("stop", {}).get("price"),
            "take_profit": lifecycle.get("protection", {}).get("target", {}).get("price"),
            "status": "CLOSED" if lifecycle.get("state") == "CLOSED" else "OPEN",
            "created_at": lifecycle.get("created_at"),
            "updated_at": lifecycle.get("updated_at"),
            "signal_id": lifecycle.get("signal_id"),
            "approval_status": "APPROVED",
            "auto_execution": False,
            "realized_pl": lifecycle.get("realized_pl", round(_as_float(acct.get("realized_gross")), 2)),
            "net_realized_pl": lifecycle.get("net_realized_pl", round(_as_float(acct.get("realized_gross")) - _as_float(acct.get("entry_fees")) - _as_float(acct.get("exit_fees")), 2)),
            "fees": round(_as_float(acct.get("entry_fees")) + _as_float(acct.get("exit_fees")), 2),
            "exit_type": lifecycle.get("exit", {}).get("type"),
            "avg_exit_price": lifecycle.get("exit", {}).get("avg_exit_price"),
            "entry_order_id": lifecycle.get("entry", {}).get("order_id"),
            "entry_client_order_id": lifecycle.get("entry", {}).get("client_order_id"),
            "stop_order_id": lifecycle.get("protection", {}).get("stop", {}).get("order_id"),
            "target_order_id": lifecycle.get("protection", {}).get("target", {}).get("order_id"),
            "closed_at": lifecycle.get("position", {}).get("closed_at"),
            "close_reason": lifecycle.get("close_reason"),
            "broker_evidence": lifecycle.get("broker_evidence"),
            "lifecycle_state": lifecycle.get("state"),
        }

    # ---------------------------------------------------------------- monitor

    def monitor_once(self) -> Dict[str, Any]:
        if not self._monitor_run_lock.acquire(blocking=False):
            self._bump_overlap_skips()
            return {"skipped": True, "reason": "overlap"}

        lock_handle = self._monitor_lock_handle()
        started_at = utc_now()
        try:
            try:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                self._bump_overlap_skips()
                return {"skipped": True, "reason": "cross-process-overlap"}

            with self._lock:
                ledger = self._load()
                monitor = ledger.setdefault("monitor", {})
                monitor["last_started_at"] = started_at.isoformat()
                monitor["entries_checked"] = 0
                monitor["failures_by_account"] = {"paper": 0}
                self._save(ledger)

            checked = 0
            for trade_id in self._active_trade_ids():
                checked += 1
                self._reconcile_trade_guarded(trade_id)

            completed_at = utc_now()
            with self._lock:
                ledger = self._load()
                self._reconcile_broker_positions(ledger)
                monitor = ledger.setdefault("monitor", {})
                monitor["last_completed_at"] = completed_at.isoformat()
                monitor["duration_ms"] = round((completed_at - started_at).total_seconds() * 1000, 2)
                monitor["entries_checked"] = checked
                monitor["transitional_entries"] = len([row for row in ledger.get("lifecycles", []) if row.get("state") not in TERMINAL_LIFECYCLE_STATES])
                monitor["unhealthy"] = False
                if monitor.get("missed_run_alert_id"):
                    self._resolve_alert(ledger, "SYSTEM", "MONITOR_HEARTBEAT_MISSED")
                    monitor["missed_run_alert_id"] = None
                self._refresh_freeze(ledger)
                self._record_equity(ledger)
                self._save(ledger)
                return _copy_payload(monitor)
        finally:
            try:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            lock_handle.close()
            self._monitor_run_lock.release()

    def _bump_overlap_skips(self) -> None:
        with self._lock:
            ledger = self._load()
            ledger["monitor"]["overlap_skips"] = _as_int(ledger.get("monitor", {}).get("overlap_skips")) + 1
            self._save(ledger)

    def _active_trade_ids(self) -> List[str]:
        with self._lock:
            return [row.get("trade_id") for row in self._load().get("lifecycles", []) if row.get("state") not in TERMINAL_LIFECYCLE_STATES]

    def _reconcile_trade_guarded(self, trade_id: str) -> None:
        """One misbehaving trade must never stop the others from being monitored."""
        try:
            self._reconcile_trade(trade_id)
        except StorageCorruptionError:
            raise
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("reconcile failed for %s", trade_id)
            with self._lock:
                ledger = self._load()
                lifecycle = self._find_lifecycle(ledger, trade_id)
                if lifecycle and lifecycle.get("state") not in TERMINAL_LIFECYCLE_STATES:
                    self._record_monitor_failure(lifecycle, "INTERNAL_ERROR", f"{type(exc).__name__}: {exc}", None)
                    self._upsert_alert(ledger, trade_id, "INTERNAL_ERROR", f"Reconcile failed: {type(exc).__name__}: {exc}", freeze=True)
                    self._save(ledger)

    def _validate_broker_state(self, state: Dict[str, Any]) -> None:
        required = {"entry_order", "position_qty", "open_orders", "recent_terminal_orders", "protection"}
        if not isinstance(state, dict) or not required.issubset(set(state.keys())):
            raise BrokerStateError("INVALID_PAYLOAD", "Broker response is incomplete")
        if state.get("entry_order") is None:
            raise BrokerStateError("MISSING_ORDER_ID", "Entry order cannot be linked to broker state")
        if not isinstance(state.get("open_orders"), list) or not isinstance(state.get("recent_terminal_orders"), list):
            raise BrokerStateError("INVALID_PAYLOAD", "Broker response order collections are malformed")
        if not isinstance(state.get("protection"), dict):
            raise BrokerStateError("INVALID_PAYLOAD", "Broker response protection payload is malformed")

    def _record_monitor_failure(self, lifecycle: Dict[str, Any], category: str, message: str, broker_state: Any) -> None:
        monitoring = lifecycle.setdefault("monitoring", {})
        now = utc_now_iso()
        if not monitoring.get("first_failed_at"):
            monitoring["first_failed_at"] = now
        monitoring["last_attempt_at"] = now
        monitoring["attempt_count"] = _as_int(monitoring.get("attempt_count")) + 1
        monitoring["last_error_category"] = category
        monitoring["last_error_message"] = message[:500]
        monitoring["last_confirmed_broker_state"] = broker_state
        monitoring["new_entries_frozen"] = True

    def _record_monitor_success(self, lifecycle: Dict[str, Any], broker_state: Any) -> None:
        monitoring = lifecycle.setdefault("monitoring", {})
        monitoring["last_attempt_at"] = utc_now_iso()
        monitoring["attempt_count"] = _as_int(monitoring.get("attempt_count")) + 1
        monitoring["last_confirmed_broker_state"] = broker_state
        monitoring["protection_confirmed"] = bool(lifecycle.get("protection", {}).get("confirmed"))

    def _reconcile_trade(self, trade_id: str) -> None:
        with self._lock:
            ledger = self._load()
            lifecycle = self._find_lifecycle(ledger, trade_id)
            if not lifecycle or lifecycle.get("state") in TERMINAL_LIFECYCLE_STATES:
                return
            entry = lifecycle.setdefault("entry", {})
            if lifecycle.get("state") == "ENTRY_SUBMITTED" and not entry.get("order_id"):
                # The intent was persisted but the broker call never recorded a result (crash or
                # error mid-submission). The client order id is deterministic, so re-sending is
                # idempotent: the broker returns the original order if it already exists.
                order = self._broker.submit_entry_order(
                    {
                        "symbol": lifecycle.get("symbol"),
                        "direction": lifecycle.get("direction"),
                        "quantity": entry.get("requested_qty"),
                        "price": entry.get("price"),
                        "client_order_id": entry.get("client_order_id"),
                    }
                )
                entry["order_id"] = order.get("order_id")
                entry["status"] = str(order.get("status") or "SUBMITTED").upper()
                entry["broker_state"] = _copy_payload(order)
                self._save(ledger)
            snapshot = _copy_payload(lifecycle)

        try:
            state = self._broker.fetch_state(snapshot)
            self._validate_broker_state(state)
        except BrokerStateError as exc:
            with self._lock:
                ledger = self._load()
                lifecycle = self._find_lifecycle(ledger, trade_id)
                if not lifecycle:
                    return
                lifecycle["state"] = "UNKNOWN_SUBMISSION_STATE" if lifecycle.get("state") == "ENTRY_SUBMITTED" else "PROTECTION_FAILED"
                self._record_monitor_failure(lifecycle, exc.category, str(exc), None)
                self._upsert_alert(ledger, trade_id, exc.category, str(exc), freeze=True)
                ledger["monitor"]["failures_by_account"] = {"paper": _as_int(ledger.get("monitor", {}).get("failures_by_account", {}).get("paper")) + 1}
                lifecycle["updated_at"] = utc_now_iso()
                self._upsert_trade_row(ledger, self._trade_row_from_lifecycle(lifecycle))
                self._save(ledger)
            return

        with self._lock:
            ledger = self._load()
            lifecycle = self._find_lifecycle(ledger, trade_id)
            if not lifecycle or lifecycle.get("state") in TERMINAL_LIFECYCLE_STATES:
                return
            for category in ("INVALID_PAYLOAD", "MISSING_ORDER_ID", "INTERNAL_ERROR"):
                self._resolve_alert(ledger, trade_id, category)
            self._record_monitor_success(lifecycle, _copy_payload(state))
            lifecycle.setdefault("broker_evidence", {})["last_state"] = _copy_payload(state)
            entry = lifecycle.setdefault("entry", {})
            broker_entry = state.get("entry_order") or {}
            entry["status"] = str(broker_entry.get("status") or entry.get("status") or "SUBMITTED").upper()
            entry["filled_qty"] = _as_int(broker_entry.get("filled_qty"), _as_int(entry.get("filled_qty")))
            entry["requested_qty"] = _as_int(broker_entry.get("requested_qty"), _as_int(entry.get("requested_qty")))
            entry["remaining_qty"] = _as_int(broker_entry.get("remaining_qty"), max(0, entry["requested_qty"] - entry["filled_qty"]))
            entry["avg_fill_price"] = broker_entry.get("avg_fill_price") if broker_entry.get("avg_fill_price") is not None else entry.get("avg_fill_price")
            entry["broker_state"] = _copy_payload(broker_entry)

            position_qty = abs(_as_int(state.get("position_qty")))
            previous_qty = abs(_as_int(lifecycle.get("position", {}).get("current_qty")))
            position = lifecycle.setdefault("position", {})
            position["current_qty"] = position_qty
            position["avg_entry_price"] = state.get("position_avg_price") if state.get("position_avg_price") is not None else entry.get("avg_fill_price")
            if position_qty > 0 and not position.get("opened_at"):
                position["opened_at"] = utc_now_iso()

            if entry["status"] in {"REJECTED", "CANCELLED", "EXPIRED"} and entry["filled_qty"] == 0 and position_qty == 0:
                lifecycle["state"] = entry["status"]
                lifecycle["close_reason"] = entry["status"].lower()
                lifecycle["updated_at"] = utc_now_iso()
                self._upsert_trade_row(ledger, self._trade_row_from_lifecycle(lifecycle))
                self._save(ledger)
                return

            if entry["filled_qty"] > 0:
                lifecycle["state"] = "ENTRY_FILLED" if entry["filled_qty"] >= entry["requested_qty"] else "ENTRY_PARTIALLY_FILLED"
                self._book_entry_fills(ledger, lifecycle)
                fills = self._book_exit_fills(ledger, lifecycle, state)
                self._sync_position_row(ledger, lifecycle)
                if position_qty == 0 and lifecycle.get("exit", {}).get("filled_order_id") and lifecycle.get("state") != "CLOSED":
                    self._retry_pending_close(ledger, lifecycle, state)
                self._reconcile_exit_progress(ledger, lifecycle, state, previous_qty=previous_qty, fills=fills)
                if lifecycle.get("state") != "CLOSED":
                    self._ensure_protection(ledger, lifecycle, position_qty, state)
                    self._check_position_consistency(ledger, lifecycle, position_qty)
            else:
                lifecycle["state"] = "ENTRY_SUBMITTED"

            lifecycle["updated_at"] = utc_now_iso()
            self._upsert_trade_row(ledger, self._trade_row_from_lifecycle(lifecycle))
            self._save(ledger)

    # -------------------------------------------------------------- accounting

    def _book_entry_fills(self, ledger: Dict[str, Any], lifecycle: Dict[str, Any]) -> None:
        """Book entry fills into cash exactly once, at the price each fill actually happened."""
        acct = self._accounting(lifecycle)
        entry = lifecycle.get("entry", {})
        broker_entry = entry.get("broker_state") or {}
        direction = str(lifecycle.get("direction") or "LONG").upper()
        account = ledger.setdefault("account", {})
        cash = _as_float(account.get("cash"), self._starting_balance)
        realized = _as_float(account.get("realized_pl"))
        filled = _as_int(entry.get("filled_qty"))
        avg_price = _as_float(entry.get("avg_fill_price") or lifecycle.get("position", {}).get("avg_entry_price") or entry.get("price"))
        if filled > acct["entry_qty"] and avg_price > 0:
            total_notional = filled * avg_price
            delta_notional = total_notional - _as_float(acct["entry_notional"])
            cash += -delta_notional if direction == "LONG" else delta_notional
            acct["entry_qty"] = filled
            acct["entry_notional"] = total_notional
        fees_total = _as_float(broker_entry.get("fees"))
        delta_fees = fees_total - _as_float(acct["entry_fees"])
        if delta_fees > 0:
            cash -= delta_fees
            realized -= delta_fees
            acct["entry_fees"] = fees_total
        account["cash"] = round(cash, 2)
        account["realized_pl"] = round(realized, 2)
        account["updated_at"] = utc_now_iso()

    def _exit_orders_from_state(self, lifecycle: Dict[str, Any], state: Dict[str, Any]) -> List[Dict[str, Any]]:
        trade_prefix = f"{lifecycle.get('trade_id')}:"
        found: Dict[str, Dict[str, Any]] = {}
        protection = state.get("protection") or {}
        pools: List[Any] = [protection.get("stop"), protection.get("target"), protection.get("manual_exit")]
        pools.extend(state.get("open_orders") or [])
        pools.extend(state.get("recent_terminal_orders") or [])
        for order in pools:
            if not isinstance(order, dict) or not order.get("order_id"):
                continue
            role = str(order.get("role") or "").upper()
            client_id = str(order.get("client_order_id") or "")
            if role == "ENTRY" or client_id.endswith(":ENTRY:v1"):
                continue
            if role in EXIT_ROLES or client_id.startswith(trade_prefix):
                found[str(order["order_id"])] = order
        return list(found.values())

    def _book_exit_fills(self, ledger: Dict[str, Any], lifecycle: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, int]:
        """Book every new exit fill (partial or full, any order, any price) exactly once.

        Returns {order_id: newly filled qty} so the caller can attribute the exit."""
        acct = self._accounting(lifecycle)
        direction = str(lifecycle.get("direction") or "LONG").upper()
        account = ledger.setdefault("account", {})
        cash = _as_float(account.get("cash"), self._starting_balance)
        realized = _as_float(account.get("realized_pl"))
        new_fills: Dict[str, int] = {}
        for order in sorted(self._exit_orders_from_state(lifecycle, state), key=lambda row: str(row.get("order_id"))):
            order_id = str(order["order_id"])
            previous = acct["exit_orders"].get(order_id) or {"qty": 0, "notional": 0.0, "fees": 0.0}
            filled = _as_int(order.get("filled_qty"))
            price = _as_float(order.get("avg_fill_price") or order.get("price"))
            if filled > previous["qty"] and price > 0:
                room = max(0, acct["entry_qty"] - acct["exit_qty"])
                delta_qty = min(filled - previous["qty"], room)
                if delta_qty < filled - previous["qty"]:
                    LOGGER.error("exit order %s filled %s but only %s shares were open", order_id, filled - previous["qty"], room)
                if delta_qty > 0:
                    notional = filled * price
                    delta_notional = notional - _as_float(previous["notional"])
                    if delta_qty != filled - previous["qty"]:
                        delta_notional = delta_qty * price
                    entry_avg = _as_float(acct["entry_notional"]) / acct["entry_qty"] if acct["entry_qty"] else 0.0
                    if direction == "LONG":
                        gross = delta_notional - delta_qty * entry_avg
                        cash += delta_notional
                    else:
                        gross = delta_qty * entry_avg - delta_notional
                        cash -= delta_notional
                    acct["exit_qty"] += delta_qty
                    acct["exit_notional"] = _as_float(acct["exit_notional"]) + delta_notional
                    acct["realized_gross"] = _as_float(acct["realized_gross"]) + gross
                    realized += gross
                    previous = {"qty": previous["qty"] + delta_qty, "notional": _as_float(previous["notional"]) + delta_notional, "fees": previous["fees"]}
                    new_fills[order_id] = delta_qty
            fees_total = _as_float(order.get("fees"))
            delta_fees = fees_total - _as_float(previous["fees"])
            if delta_fees > 0:
                cash -= delta_fees
                realized -= delta_fees
                acct["exit_fees"] = _as_float(acct["exit_fees"]) + delta_fees
                previous["fees"] = fees_total
            acct["exit_orders"][order_id] = previous
        account["cash"] = round(cash, 2)
        account["realized_pl"] = round(realized, 2)
        account["updated_at"] = utc_now_iso()
        return new_fills

    def _sync_position_row(self, ledger: Dict[str, Any], lifecycle: Dict[str, Any]) -> None:
        acct = self._accounting(lifecycle)
        current_qty = abs(_as_int(lifecycle.get("position", {}).get("current_qty")))
        direction = str(lifecycle.get("direction") or "LONG").upper()
        sign = 1 if direction == "LONG" else -1
        avg_entry = _as_float(acct["entry_notional"]) / acct["entry_qty"] if acct["entry_qty"] else _as_float(lifecycle.get("position", {}).get("avg_entry_price"))
        row = {
            "position_id": lifecycle.get("position_id"),
            "trade_id": lifecycle.get("trade_id"),
            "symbol": lifecycle.get("symbol"),
            "direction": direction,
            "quantity": current_qty * sign,
            "entry_price": round(avg_entry, 4) if avg_entry else None,
            "stop_loss": lifecycle.get("protection", {}).get("stop", {}).get("price"),
            "take_profit": lifecycle.get("protection", {}).get("target", {}).get("price"),
            "status": "OPEN" if current_qty > 0 else "CLOSED",
            "created_at": lifecycle.get("created_at"),
            "updated_at": utc_now_iso(),
            "signal_id": lifecycle.get("signal_id"),
            "approved_by": lifecycle.get("approved_by"),
            "last_mark_price": round(avg_entry, 4) if avg_entry else None,
            "unrealized_pl": 0.0,
        }
        self._upsert_position_row(ledger, row)

    def _check_position_consistency(self, ledger: Dict[str, Any], lifecycle: Dict[str, Any], position_qty: int) -> None:
        acct = self._accounting(lifecycle)
        expected = acct["entry_qty"] - acct["exit_qty"]
        if expected != position_qty and lifecycle.get("state") not in {"MANUAL_LINK_IN_PROGRESS"}:
            message = f"Ledger expects {expected} open shares of {lifecycle.get('symbol')} but the broker reports {position_qty}"
            self._upsert_alert(ledger, lifecycle["trade_id"], "POSITION_MISMATCH", message, freeze=True)
        else:
            self._resolve_alert(ledger, lifecycle["trade_id"], "POSITION_MISMATCH")

    def _reconcile_broker_positions(self, ledger: Dict[str, Any]) -> None:
        """A position at the broker that no live lifecycle accounts for is an orphan and blocks new entries."""
        lister = getattr(self._broker, "list_positions", None)
        if not callable(lister):
            return
        try:
            broker_positions = lister()
        except Exception:  # noqa: BLE001
            return
        tracked = {normalize_ticker(row.get("symbol") or "") for row in ledger.get("lifecycles", []) if row.get("state") not in TERMINAL_LIFECYCLE_STATES}
        for row in broker_positions:
            symbol = normalize_ticker(row.get("symbol") or "")
            key = f"ORPHAN-{symbol}"
            if symbol in tracked:
                self._resolve_alert(ledger, "SYSTEM", key)
            else:
                self._upsert_alert(ledger, "SYSTEM", key, f"Broker holds {row.get('quantity')} {symbol} that no active lifecycle accounts for", freeze=True)

    # ------------------------------------------------------------- protection

    def _find_order_by_id(self, state: Dict[str, Any], order_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not order_id:
            return None
        candidates = [state.get("entry_order"), state.get("protection", {}).get("stop"), state.get("protection", {}).get("target"), state.get("protection", {}).get("manual_exit")]
        candidates.extend(state.get("open_orders") or [])
        candidates.extend(state.get("recent_terminal_orders") or [])
        for row in candidates:
            if row and row.get("order_id") == order_id:
                return row
        return None

    def _requires_replacement(self, order: Optional[Dict[str, Any]], desired_qty: int) -> bool:
        if not order:
            return False
        if _terminal_order(order):
            return desired_qty > 0
        return _as_int(order.get("requested_qty")) != desired_qty

    def _create_protection_order(self, lifecycle: Dict[str, Any], node: Dict[str, Any], *, kind: str, desired_qty: int, price: Any) -> Dict[str, Any]:
        version = _as_int(node.get("version")) + 1
        direction = str(lifecycle.get("direction") or "LONG").upper()
        side = "SELL" if direction == "LONG" else "BUY"
        order = self._broker.submit_exit_order(
            {
                "symbol": lifecycle.get("symbol"),
                "side": side,
                "quantity": desired_qty,
                "price": _as_float(price, 0.0),
                "client_order_id": self._protection_client_order_id(lifecycle.get("trade_id"), kind, version),
                "role": kind,
                "order_type": "STOP" if kind == "STOP" else "LIMIT",
            }
        )
        node["version"] = version
        node["client_order_id"] = order.get("client_order_id")
        node["order_id"] = order.get("order_id")
        return order

    def _replace_protection_order(self, lifecycle: Dict[str, Any], node: Dict[str, Any], *, kind: str, desired_qty: int, price: Any, state: Dict[str, Any]) -> Dict[str, Any]:
        existing = self._find_order_by_id(state, node.get("order_id"))
        if existing and _open_order(existing):
            cancelled = self._broker.cancel_order(existing.get("order_id"))
            if str(cancelled.get("status") or "").upper() not in {"CANCELLED", "REJECTED", "EXPIRED"}:
                raise BrokerStateError("PROTECTION_CANCEL_UNCERTAIN", f"Unable to confirm {kind.lower()} cancellation before replace")
        return self._create_protection_order(lifecycle, node, kind=kind, desired_qty=desired_qty, price=price)

    def _ensure_protection(self, ledger: Dict[str, Any], lifecycle: Dict[str, Any], position_qty: int, state: Dict[str, Any]) -> None:
        if position_qty <= 0:
            lifecycle["protection"]["desired_qty"] = 0
            lifecycle["protection"]["confirmed"] = False
            return
        protection = lifecycle.setdefault("protection", {})
        protection["desired_qty"] = position_qty
        stop_row = protection.setdefault("stop", {})
        target_row = protection.setdefault("target", {})
        stop_order = self._find_order_by_id(state, stop_row.get("order_id"))
        target_order = self._find_order_by_id(state, target_row.get("order_id"))
        if (stop_row.get("order_id") and not stop_order) or (target_row.get("order_id") and not target_order):
            refreshed_state = self._broker.fetch_state(_copy_payload(lifecycle))
            stop_order = self._find_order_by_id(refreshed_state, stop_row.get("order_id"))
            target_order = self._find_order_by_id(refreshed_state, target_row.get("order_id"))
        desired = position_qty

        try:
            if self._requires_replacement(stop_order, desired):
                stop_order = self._replace_protection_order(lifecycle, stop_row, kind="STOP", desired_qty=desired, price=stop_row.get("price"), state=state)
            elif not stop_order and stop_row.get("price") is not None:
                stop_order = self._create_protection_order(lifecycle, stop_row, kind="STOP", desired_qty=desired, price=stop_row.get("price"))

            if self._requires_replacement(target_order, desired):
                target_order = self._replace_protection_order(lifecycle, target_row, kind="TARGET", desired_qty=desired, price=target_row.get("price"), state=state)
            elif not target_order and target_row.get("price") is not None:
                target_order = self._create_protection_order(lifecycle, target_row, kind="TARGET", desired_qty=desired, price=target_row.get("price"))
        except BrokerStateError as exc:
            lifecycle["state"] = "PROTECTION_FAILED"
            self._record_monitor_failure(lifecycle, exc.category, str(exc), _copy_payload(state))
            self._upsert_alert(ledger, lifecycle["trade_id"], exc.category, str(exc), freeze=True)
            return

        stop_live_qty = _as_int((stop_order or {}).get("requested_qty")) if stop_order and _open_order(stop_order) else 0
        target_live_qty = _as_int((target_order or {}).get("requested_qty")) if target_order and _open_order(target_order) else 0
        # Confirmed = a stop is live for the whole position and every target that was configured is too.
        protection["confirmed"] = (
            stop_row.get("price") is not None
            and stop_live_qty == desired
            and (target_row.get("price") is None or target_live_qty == desired)
        )
        protection["status"] = "CONFIRMED" if protection["confirmed"] else "PENDING"
        if not protection["confirmed"]:
            lifecycle["state"] = "PROTECTION_PENDING"
        elif lifecycle.get("entry", {}).get("filled_qty") >= lifecycle.get("entry", {}).get("requested_qty"):
            lifecycle["state"] = "ENTRY_FILLED"
        lifecycle.setdefault("monitoring", {})["protection_confirmed"] = protection["confirmed"]
        if protection["confirmed"]:
            for category in ("PROTECTION_FAILED", "PROTECTION_CANCEL_UNCERTAIN"):
                self._resolve_alert(ledger, lifecycle["trade_id"], category)

    def _cancel_open_protection(self, ledger: Dict[str, Any], lifecycle: Dict[str, Any], state: Dict[str, Any], *, except_order_id: Optional[str]) -> bool:
        """Cancel every still-open stop/target so nothing can fill after the position is gone."""
        protection = lifecycle.get("protection", {})
        for kind in ("stop", "target"):
            node = protection.get(kind, {})
            order = self._find_order_by_id(state, node.get("order_id"))
            if not order or not _open_order(order) or order.get("order_id") == except_order_id:
                continue
            cancelled = self._broker.cancel_order(order.get("order_id"))
            if str(cancelled.get("status") or "").upper() not in {"CANCELLED", "REJECTED", "EXPIRED"}:
                lifecycle["state"] = "MANUAL_LINK_IN_PROGRESS"
                self._record_monitor_failure(lifecycle, "SIBLING_CANCEL_UNCERTAIN", "Sibling exit cancellation could not be confirmed", _copy_payload(state))
                self._upsert_alert(ledger, lifecycle["trade_id"], "SIBLING_CANCEL_UNCERTAIN", "Sibling exit cancellation could not be confirmed", freeze=True)
                return False
        return True

    def _reconcile_exit_progress(self, ledger: Dict[str, Any], lifecycle: Dict[str, Any], state: Dict[str, Any], *, previous_qty: int, fills: Dict[str, int]) -> None:
        current_qty = abs(_as_int(lifecycle.get("position", {}).get("current_qty")))
        if previous_qty <= current_qty and not fills:
            return
        if previous_qty <= current_qty:
            return
        protection = lifecycle.get("protection", {})
        candidates = [
            ("stop", self._find_order_by_id(state, protection.get("stop", {}).get("order_id"))),
            ("target", self._find_order_by_id(state, protection.get("target", {}).get("order_id"))),
            ("manual", self._find_order_by_id(state, lifecycle.get("manual_exit", {}).get("order_id"))),
        ]
        matched = [(name, order) for name, order in candidates if order and fills.get(str(order.get("order_id")), 0) > 0]

        if len(matched) == 1:
            exit_kind, evidence_order = matched[0]
        elif len(matched) == 0 and current_qty == 0:
            lifecycle["state"] = "MANUAL_LINK_IN_PROGRESS"
            self._record_monitor_failure(lifecycle, "EXIT_EVIDENCE_MISSING", "Position disappeared without conclusive exit order evidence", _copy_payload(state))
            self._upsert_alert(ledger, lifecycle["trade_id"], "EXIT_EVIDENCE_MISSING", "Position disappeared without conclusive exit order evidence", freeze=True)
            return
        else:
            lifecycle["state"] = "MANUAL_LINK_IN_PROGRESS"
            self._record_monitor_failure(lifecycle, "EXIT_EVIDENCE_CONFLICT", "Contradictory exit evidence detected", _copy_payload(state))
            self._upsert_alert(ledger, lifecycle["trade_id"], "EXIT_EVIDENCE_CONFLICT", "Contradictory exit evidence detected", freeze=True)
            return

        acct = self._accounting(lifecycle)
        if exit_kind in {"stop", "target"}:
            protection[exit_kind]["filled_qty_seen"] = _as_int((evidence_order or {}).get("filled_qty"))
        lifecycle["exit"]["type"] = exit_kind
        lifecycle["exit"]["exited_qty"] = acct["exit_qty"]
        lifecycle["exit"]["avg_exit_price"] = round(_as_float(acct["exit_notional"]) / acct["exit_qty"], 4) if acct["exit_qty"] else None
        lifecycle["exit"]["filled_order_id"] = (evidence_order or {}).get("order_id")
        lifecycle["exit"]["filled_at"] = (evidence_order or {}).get("updated_at") or utc_now_iso()

        if current_qty > 0:
            # Partial exit: resize whatever protection is still working to the shares that remain.
            sibling_kind = "target" if exit_kind == "stop" else ("stop" if exit_kind == "target" else None)
            if sibling_kind:
                sibling = protection.get(sibling_kind, {})
                sibling_order = self._find_order_by_id(state, sibling.get("order_id"))
                if sibling_order and _open_order(sibling_order) and _as_int(sibling_order.get("requested_qty")) != current_qty:
                    try:
                        self._replace_protection_order(lifecycle, sibling, kind=sibling_kind.upper(), desired_qty=current_qty, price=sibling.get("price"), state=state)
                    except BrokerStateError as exc:
                        lifecycle["state"] = "PROTECTION_FAILED"
                        self._record_monitor_failure(lifecycle, exc.category, str(exc), _copy_payload(state))
                        self._upsert_alert(ledger, lifecycle["trade_id"], exc.category, str(exc), freeze=True)
            return

        if not self._cancel_open_protection(ledger, lifecycle, state, except_order_id=(evidence_order or {}).get("order_id")):
            return
        self._close_trade(ledger, lifecycle, evidence_order)

    def _retry_pending_close(self, ledger: Dict[str, Any], lifecycle: Dict[str, Any], state: Dict[str, Any]) -> None:
        exit_order_id = lifecycle.get("exit", {}).get("filled_order_id")
        evidence_order = self._find_order_by_id(state, exit_order_id)
        if not evidence_order:
            lifecycle["state"] = "MANUAL_LINK_IN_PROGRESS"
            self._record_monitor_failure(lifecycle, "EXIT_EVIDENCE_MISSING", "Unable to refetch exit evidence for pending close", _copy_payload(state))
            self._upsert_alert(ledger, lifecycle["trade_id"], "EXIT_EVIDENCE_MISSING", "Unable to refetch exit evidence for pending close", freeze=True)
            return
        if not self._cancel_open_protection(ledger, lifecycle, state, except_order_id=exit_order_id):
            return
        self._close_trade(ledger, lifecycle, evidence_order)

    def _close_trade(self, ledger: Dict[str, Any], lifecycle: Dict[str, Any], evidence_order: Optional[Dict[str, Any]]) -> None:
        """Finalise a flat trade. Cash and realised P&L were already booked fill-by-fill."""
        acct = self._accounting(lifecycle)
        fees = round(_as_float(acct["entry_fees"]) + _as_float(acct["exit_fees"]), 2)
        gross = round(_as_float(acct["realized_gross"]), 2)
        lifecycle["realized_pl"] = gross
        lifecycle["net_realized_pl"] = round(gross - fees, 2)
        lifecycle["fees"] = fees
        lifecycle["exit"]["exited_qty"] = acct["exit_qty"]
        lifecycle["exit"]["avg_exit_price"] = round(_as_float(acct["exit_notional"]) / acct["exit_qty"], 4) if acct["exit_qty"] else lifecycle["exit"].get("avg_exit_price")
        lifecycle["position"]["current_qty"] = 0
        lifecycle["position"]["closed_at"] = (evidence_order or {}).get("updated_at") or utc_now_iso()
        lifecycle["close_reason"] = lifecycle.get("exit", {}).get("type") or "other"
        lifecycle["state"] = "CLOSED"
        lifecycle["updated_at"] = utc_now_iso()
        lifecycle.setdefault("broker_evidence", {})["close_order"] = _copy_payload(evidence_order)
        ledger.setdefault("account", {})["updated_at"] = utc_now_iso()
        self._archive_position_if_closed(ledger, lifecycle["trade_id"])
        for category in ("SIBLING_CANCEL_UNCERTAIN", "EXIT_EVIDENCE_MISSING", "EXIT_EVIDENCE_CONFLICT", "POSITION_MISMATCH", "PROTECTION_FAILED"):
            self._resolve_alert(ledger, lifecycle["trade_id"], category)

    # ------------------------------------------------------------- lifecycle

    def resume_nonterminal_lifecycles(self) -> None:
        with self._lock:
            active = [row for row in self._load().get("lifecycles", []) if row.get("state") not in TERMINAL_LIFECYCLE_STATES]
        # Always reconcile on startup: even with no active lifecycle the broker file may hold
        # positions the ledger does not know about, which must be surfaced.
        if active or getattr(self._broker, "list_positions", None):
            self.monitor_once()

    def inject_lifecycle_for_test(self, lifecycle: Dict[str, Any]) -> None:
        with self._lock:
            ledger = self._load()
            ledger.setdefault("lifecycles", []).append(_copy_payload(lifecycle))
            self._save(ledger)

    def lifecycle_records(self) -> List[Dict[str, Any]]:
        with self._lock:
            return _copy_payload(self._load().get("lifecycles", []))

    def auto_execution_enabled(self) -> bool:
        return self._auto_execution

    def autonomy_enabled(self) -> bool:
        return self._autonomy_enabled


class OrderMonitorWorker:
    def __init__(self, engine: InternalPaperEngine, interval_seconds: int) -> None:
        self._engine = engine
        self._interval_seconds = max(30, interval_seconds)
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, name="paper-order-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def _loop(self) -> None:
        while not self._stop_event.wait(self._interval_seconds):
            try:
                self._engine.monitor_once()
            except Exception:  # noqa: BLE001 - a failed pass must never end monitoring for good
                LOGGER.exception("order monitor pass failed; will retry next interval")
