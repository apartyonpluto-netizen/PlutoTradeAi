"""Deterministic test doubles: a controllable market price feed and a scripted broker."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, Optional
from unittest.mock import patch


class PriceFeed:
    """Stands in for backend.services.market_data_service.get_stock_snapshot."""

    def __init__(self, **prices: float) -> None:
        self.prices: Dict[str, float] = {symbol.upper(): price for symbol, price in prices.items()}
        self.status = "delayed"
        self.available = True

    def set(self, symbol: str, price: float) -> None:
        self.prices[symbol.upper()] = price

    def __call__(self, symbol: str, *_args: Any, **_kwargs: Any) -> Dict[str, Any]:
        price = self.prices.get(str(symbol).upper())
        if not self.available or price is None:
            return {"success": False, "data": {}, "data_status": "unavailable"}
        return {"success": True, "data": {"current_price": price, "market_data_status": self.status.upper()}, "data_status": self.status}


@contextmanager
def patched_prices(feed: PriceFeed) -> Iterator[PriceFeed]:
    with patch("backend.services.paper_engine.get_stock_snapshot", side_effect=feed), patch("backend.app.get_stock_snapshot", side_effect=feed):
        yield feed


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ScriptedBroker:
    """Broker whose fills are dictated by the test, including partial fills at different prices."""

    def __init__(self) -> None:
        self.orders: Dict[str, Dict[str, Any]] = {}
        self.positions: Dict[str, Dict[str, Any]] = {}
        self.next_id = 1
        self.entry_submissions = 0

    # -- broker API used by the engine ------------------------------------
    def submit_entry_order(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self.entry_submissions += 1
        existing = self._by_client_id(payload["client_order_id"])
        if existing:
            return dict(existing)
        order = self._new(payload["symbol"], payload["client_order_id"], "ENTRY", "BUY" if payload.get("direction") == "LONG" else "SELL", int(payload["quantity"]), float(payload["price"]))
        self.positions.setdefault(order["symbol"], {"quantity": 0, "avg_price": None})
        return dict(order)

    def submit_exit_order(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        existing = self._by_client_id(payload["client_order_id"])
        if existing:
            return dict(existing)
        return dict(self._new(payload["symbol"], payload["client_order_id"], payload.get("role", "EXIT"), payload["side"], int(payload["quantity"]), float(payload["price"]), payload.get("order_type", "LIMIT")))

    def cancel_order(self, order_id: str) -> Dict[str, Any]:
        order = self.orders[order_id]
        if order["status"] not in {"FILLED", "CANCELLED", "REJECTED", "EXPIRED"}:
            order["status"] = "CANCELLED"
        return dict(order)

    def fetch_state(self, lifecycle: Dict[str, Any]) -> Dict[str, Any]:
        symbol = lifecycle["symbol"]
        get = lambda oid: dict(self.orders[oid]) if oid in self.orders else None  # noqa: E731
        position = self.positions.get(symbol, {"quantity": 0, "avg_price": None})
        rows = [dict(o) for o in self.orders.values() if o["symbol"] == symbol]
        protection = {
            "stop": get(lifecycle.get("protection", {}).get("stop", {}).get("order_id")),
            "target": get(lifecycle.get("protection", {}).get("target", {}).get("order_id")),
        }
        if lifecycle.get("manual_exit", {}).get("order_id"):
            protection["manual_exit"] = get(lifecycle["manual_exit"]["order_id"])
        return {
            "entry_order": get(lifecycle.get("entry", {}).get("order_id")),
            "position_qty": position["quantity"],
            "position_avg_price": position["avg_price"],
            "open_orders": [o for o in rows if o["status"] in {"SUBMITTED", "WORKING", "PARTIALLY_FILLED", "OPEN"}],
            "recent_terminal_orders": [o for o in rows if o["status"] in {"FILLED", "CANCELLED", "REJECTED", "EXPIRED"}],
            "protection": protection,
        }

    # -- scripting helpers -----------------------------------------------
    def fill_entry(self, symbol: str, *, filled_qty: int, avg_price: float, fees: float = 0.0) -> None:
        order = self.latest(symbol, "ENTRY")
        order.update(filled_qty=filled_qty, remaining_qty=order["requested_qty"] - filled_qty, avg_fill_price=avg_price, fees=fees,
                     status="FILLED" if filled_qty >= order["requested_qty"] else "PARTIALLY_FILLED")
        self.positions[symbol] = {"quantity": filled_qty, "avg_price": avg_price}

    def fill_exit(self, symbol: str, role: str, *, filled_qty: int, avg_price: float, position_after: int, fees: float = 0.0) -> None:
        order = self.latest(symbol, role)
        order.update(filled_qty=filled_qty, remaining_qty=order["requested_qty"] - filled_qty, avg_fill_price=avg_price, fees=fees,
                     status="FILLED" if filled_qty >= order["requested_qty"] else "PARTIALLY_FILLED")
        self.positions[symbol] = {"quantity": position_after, "avg_price": self.positions[symbol]["avg_price"]}

    def latest(self, symbol: str, role: str) -> Dict[str, Any]:
        rows = [o for o in self.orders.values() if o["symbol"] == symbol and o["role"] == role]
        return max(rows, key=lambda o: int(o["order_id"].split("-")[1]))

    def live(self, symbol: str, role: Optional[str] = None) -> list:
        return [o for o in self.orders.values() if o["symbol"] == symbol and (role is None or o["role"] == role)
                and o["status"] in {"SUBMITTED", "WORKING", "PARTIALLY_FILLED", "OPEN"}]

    def _by_client_id(self, client_order_id: str) -> Optional[Dict[str, Any]]:
        return next((o for o in self.orders.values() if o["client_order_id"] == client_order_id), None)

    def _new(self, symbol: str, client_order_id: str, role: str, side: str, qty: int, price: float, order_type: str = "LIMIT") -> Dict[str, Any]:
        order = {
            "order_id": f"S-{self.next_id}", "client_order_id": client_order_id, "symbol": symbol, "role": role, "side": side,
            "order_type": order_type, "status": "SUBMITTED", "requested_qty": qty, "filled_qty": 0, "remaining_qty": qty,
            "avg_fill_price": None, "fees": 0.0, "price": price, "created_at": now_iso(), "updated_at": now_iso(),
        }
        self.next_id += 1
        self.orders[order["order_id"]] = order
        return order


# ---------------------------------------------------------------------------
# Flask app helpers
# ---------------------------------------------------------------------------
import json as _json
import os as _os


def stub_analyzer(signal: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "market_direction": "BULLISH" if signal.get("direction") == "LONG" else "BEARISH",
        "confidence_score": 70,
        "entry_zone": signal.get("price"),
        "stop_loss": signal.get("stop_loss"),
        "primary_target": signal.get("take_profit"),
        "warnings": [],
        "data_freshness": "DELAYED",
    }


def make_app(tmpdir: str, extra_env: Optional[Dict[str, str]] = None, analyzer: Any = stub_analyzer):
    """Build an app whose every state file lives in tmpdir. Returns (app, env_patcher)."""
    from backend.app import create_app

    env = {
        "TRADINGVIEW_WEBHOOK_SECRET": "unit-secret",
        "TRADINGVIEW_WEBHOOK_MAX_AGE_SECONDS": "300",
        "PLUTO_SIGNALS_FILE": _os.path.join(tmpdir, "signals.json"),
        "PLUTO_SIGNAL_APPROVALS_FILE": _os.path.join(tmpdir, "approvals.json"),
        "PLUTO_PAPER_LEDGER_FILE": _os.path.join(tmpdir, "paper_ledger.json"),
        "PLUTO_PAPER_STARTING_BALANCE": "100000",
        "PLUTO_MAX_RISK_PER_TRADE": "0.01",
        "PLUTO_MAX_POSITION_PERCENT": "0.10",
        "PLUTO_APPROVER_USERS": "",
        "PLUTO_ADMIN_MODE": "false",
    }
    env.update(extra_env or {})
    patcher = patch.dict(_os.environ, env, clear=False)
    patcher.start()
    app = create_app()
    app.config.update(TESTING=True)
    if analyzer is not None:
        app.extensions["signal_service"]._analyzer = analyzer
    return app, patcher


def login(client: Any, username: str = "tester") -> None:
    client.post("/auth/register", data={"username": username, "password": "correct-horse", "confirm_password": "correct-horse"})
    client.post("/auth/login", data={"username": username, "password": "correct-horse"})


def webhook_payload(**overrides: Any) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    payload: Dict[str, Any] = {
        "signal_id": f"TSLA-60-{now.strftime('%Y%m%dT%H%M%S%f')}-v1",
        "symbol": "TSLA", "exchange": "NASDAQ", "asset_type": "STOCK", "strategy": "pluto-test-v1", "direction": "LONG",
        "timeframe": "60", "price": 100.0, "stop_loss": 95.0, "take_profit": 110.0, "bar_time": now.isoformat(),
    }
    payload.update(overrides)
    return payload


def post_webhook(client: Any, payload: Dict[str, Any], secret: Optional[str] = "unit-secret", raw: Optional[str] = None):
    headers = {"Content-Type": "application/json"}
    if secret is not None:
        headers["X-Pluto-Webhook-Secret"] = secret
    return client.post("/api/webhooks/tradingview", data=raw if raw is not None else _json.dumps(payload), headers=headers)
