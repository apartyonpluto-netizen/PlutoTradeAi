"""Live Alpaca market-data stream (quotes and trades only).

Ported from ``feature/alpaca-live-market-stream``. That branch does not
share history with current main (no merge base): it is an older preview
app whose paper broker, routes, and UI have since been replaced. This
module keeps the stream's behavior — a bounded quote cache, connection
health, an Alpaca data websocket, and a REST fallback — on top of the
current Alpaca data client.

What this module will not do:

* It never places, cancels, or amends an order. The socket is the Alpaca
  market-data endpoint (``wss://stream.data.alpaca.markets``), not the
  trading API.
* It reads ``ALPACA_API_KEY_ID`` / ``ALPACA_API_SECRET_KEY``, the same
  pair historical bars already use. The preview branch's
  ``ALPACA_API_KEY`` / ``ALPACA_API_SECRET`` names are ignored so a
  second credential channel cannot appear.
* It does not call Yahoo Finance. Main left that path after Yahoo began
  rate-limiting this deployment; the fallback is Alpaca's snapshot REST
  API (IEX, unless ``ALPACA_DATA_FEED`` is already ``sip``).
* It does not start itself when the process imports it. A background
  socket starts only when ``PLUTO_ENABLE_MARKET_STREAM`` is turned on
  (and ``PLUTO_DISABLE_MARKET_STREAM`` is not). Alpaca Basic allows one
  data websocket, and this process runs under gunicorn with several
  workers, so a file lock lets only one process hold the socket. The
  others keep serving quotes from REST.
"""

from __future__ import annotations

import fcntl
import json
import logging
import math
import os
import re
import threading
import time
from abc import ABC, abstractmethod
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple
from urllib.parse import urlparse

try:
    from websockets.exceptions import ConnectionClosed
    from websockets.sync.client import connect as _ws_connect
except Exception:  # pragma: no cover - dependency is pinned; tests also inject a fake client
    ConnectionClosed = Exception  # type: ignore[misc, assignment]
    _ws_connect = None


LOGGER = logging.getLogger(__name__)

DEFAULT_SYMBOLS = ["SPY", "QQQ", "IWM", "DIA", "TSLA", "NVDA"]
_OFFICIAL_STREAM_HOST = "stream.data.alpaca.markets"
_ALLOWED_FEEDS = {"iex", "sip"}
_ON_VALUES = {"1", "true", "yes", "on"}
_TICKER_PATTERN = re.compile(r"^[A-Z0-9.\-\^=]{1,12}$")

_SERVICE: Optional["MarketStreamService"] = None
_SERVICE_LOCK = threading.Lock()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_epoch() -> float:
    return time.time()


def _truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in _ON_VALUES


def autostart_enabled() -> bool:
    """Background socket is off unless explicitly enabled, and the disable flag wins."""
    if _truthy("PLUTO_DISABLE_MARKET_STREAM"):
        return False
    return _truthy("PLUTO_ENABLE_MARKET_STREAM")


def credentials_configured() -> bool:
    """Same data keys as ``integrations.alpaca_data``. Legacy names are not consulted."""
    return bool(os.getenv("ALPACA_API_KEY_ID", "").strip() and os.getenv("ALPACA_API_SECRET_KEY", "").strip())


def data_feed() -> str:
    feed = os.getenv("ALPACA_DATA_FEED", "iex").strip().lower() or "iex"
    return feed if feed in _ALLOWED_FEEDS else "iex"


def resolve_stream_url(feed: Optional[str] = None) -> str:
    """Official Alpaca data-stream URL for ``feed``.

    ``ALPACA_WS_URL`` is honored only when it is already that official
    host, uses ``wss``, and does not carry userinfo or a query. Anything
    else is ignored so the API secret cannot be sent to another host.
    A URL that points at the other feed (sip vs iex) is also ignored:
    ``ALPACA_DATA_FEED`` is the only switch, matching historical bars.
    """
    selected = feed or data_feed()
    official = f"wss://{_OFFICIAL_STREAM_HOST}/v2/{selected}"
    override = os.getenv("ALPACA_WS_URL", "").strip()
    if not override:
        return official
    parsed = urlparse(override)
    path = (parsed.path or "").rstrip("/")
    allowed = (
        parsed.scheme == "wss"
        and (parsed.hostname or "").lower() == _OFFICIAL_STREAM_HOST
        and path == f"/v2/{selected}"
        and not parsed.username
        and not parsed.password
        and not parsed.query
        and not parsed.fragment
    )
    return override if allowed else official


def normalize_ticker(raw_ticker: str) -> str:
    return (raw_ticker or "").strip().upper()


def validate_ticker(ticker: str) -> Tuple[bool, Optional[str]]:
    if not ticker:
        return False, "Ticker is required"
    if not _TICKER_PATTERN.match(ticker):
        return False, "Ticker format is invalid"
    return True, None


def _bounded_int(name: str, default: int, lower: int, upper: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return min(upper, max(lower, value))


def _to_number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _provider_mode() -> Tuple[str, bool, Optional[str]]:
    raw = os.getenv("PLUTO_MARKET_PROVIDER", "rest").strip().lower() or "rest"
    ignored = raw if raw in {"yfinance", "yahoo"} else None
    return raw, raw in {"alpaca", "streaming"}, ignored


class _WebSocketApp:
    """Small blocking client with the callback shape the provider already uses.

    Tests replace ``websocket.WebSocketApp`` with a fake. Production uses
    the ``websockets`` sync client already pinned in requirements.
    """

    def __init__(self, url: str, on_open=None, on_message=None, on_error=None, on_close=None) -> None:
        self.url = url
        self.on_open = on_open
        self.on_message = on_message
        self.on_error = on_error
        self.on_close = on_close
        self._conn = None
        self._closed = False

    def send(self, message: str) -> None:
        conn = self._conn
        if conn is None:
            raise RuntimeError("socket is not open")
        conn.send(message)

    def close(self) -> None:
        self._closed = True
        conn = self._conn
        if conn is not None:
            try:
                conn.close()
            except Exception:
                return

    def run_forever(self, ping_interval: int = 20, ping_timeout: int = 10) -> None:
        if _ws_connect is None:
            raise RuntimeError("websockets library unavailable")
        if self._closed:
            return
        try:
            with _ws_connect(
                self.url,
                open_timeout=10,
                ping_interval=ping_interval,
                ping_timeout=ping_timeout,
                proxy=None,
            ) as conn:
                self._conn = conn
                if self._closed:
                    return
                if self.on_open:
                    self.on_open(self)
                while not self._closed:
                    raw = conn.recv()
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8", errors="replace")
                    if self.on_message:
                        self.on_message(self, raw)
        except ConnectionClosed as err:
            if self.on_close:
                self.on_close(self, getattr(err, "code", None), str(err))
        except Exception as err:
            if self.on_error:
                self.on_error(self, err)
            if self.on_close:
                self.on_close(self, None, str(err))
        finally:
            self._conn = None


websocket = None if _ws_connect is None else type("websocket", (), {"WebSocketApp": _WebSocketApp})()


@dataclass
class NormalizedQuote:
    symbol: str
    price: Optional[float]
    bid: Optional[float]
    ask: Optional[float]
    volume: Optional[float]
    provider: str
    source_status: str
    timestamp: str
    sequence: int
    age_seconds: int
    cache_status: str

    def as_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "price": self.price,
            "bid": self.bid,
            "ask": self.ask,
            "volume": self.volume,
            "provider": self.provider,
            "source_status": self.source_status,
            "timestamp": self.timestamp,
            "sequence": self.sequence,
            "age_seconds": self.age_seconds,
            "cache_status": self.cache_status,
        }


class MarketDataProvider(ABC):
    name: str

    @abstractmethod
    def start(self, symbols: Sequence[str]) -> None:
        ...

    @abstractmethod
    def stop(self) -> None:
        ...

    @abstractmethod
    def set_symbols(self, symbols: Sequence[str]) -> None:
        ...

    @abstractmethod
    def get_quote(self, symbol: str) -> Optional[Dict[str, Any]]:
        ...

    @abstractmethod
    def get_quotes(self, symbols: Sequence[str]) -> Dict[str, Dict[str, Any]]:
        ...

    @abstractmethod
    def health(self) -> Dict[str, Any]:
        ...


class MarketDataCache:
    def __init__(self, ttl_seconds: int = 15, stale_seconds: int = 120, max_symbols: int = 256) -> None:
        self.ttl_seconds = max(1, ttl_seconds)
        self.stale_seconds = max(self.ttl_seconds, stale_seconds)
        self.max_symbols = max(1, max_symbols)
        self._lock = threading.Lock()
        self._rows: OrderedDict[str, Dict[str, Any]] = OrderedDict()
        self._sequence = 0

    def update(self, symbol: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        now = _now_epoch()
        with self._lock:
            self._sequence += 1
            row = {
                "symbol": symbol,
                "payload": payload,
                "updated_at": now,
                "timestamp": utc_now_iso(),
                "sequence": self._sequence,
            }
            self._rows.pop(symbol, None)
            self._rows[symbol] = row
            while len(self._rows) > self.max_symbols:
                self._rows.popitem(last=False)
            return dict(row)

    def get(self, symbol: str) -> Tuple[Optional[Dict[str, Any]], str, Optional[int]]:
        now = _now_epoch()
        with self._lock:
            row = self._rows.get(symbol)
            if not row:
                return None, "unavailable", None
            age = int(max(now - row["updated_at"], 0))
            if age <= self.ttl_seconds:
                return dict(row), "live", age
            if age <= self.stale_seconds:
                return dict(row), "stale", age
            return dict(row), "unavailable", age

    def cleanup_stale(self) -> int:
        now = _now_epoch()
        removed = 0
        with self._lock:
            for key in list(self._rows.keys()):
                row = self._rows.get(key)
                if not row:
                    continue
                if now - row.get("updated_at", 0) > self.stale_seconds:
                    self._rows.pop(key, None)
                    removed += 1
        return removed

    def size(self) -> int:
        with self._lock:
            return len(self._rows)


class ConnectionHealthService:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._connected = False
        self._fallback_active = False
        self._last_error: Optional[str] = None
        self._last_connect_at: Optional[str] = None
        self._last_disconnect_at: Optional[str] = None
        self._reconnect_attempts = 0

    def connected(self) -> None:
        with self._lock:
            self._connected = True
            self._last_connect_at = utc_now_iso()
            self._last_error = None
            self._reconnect_attempts = 0

    def disconnected(self, error: Optional[str] = None) -> None:
        with self._lock:
            self._connected = False
            self._last_disconnect_at = utc_now_iso()
            if error:
                self._last_error = error

    def reconnecting(self) -> int:
        with self._lock:
            self._reconnect_attempts += 1
            return self._reconnect_attempts

    def set_fallback(self, active: bool, error: Optional[str] = None) -> None:
        with self._lock:
            self._fallback_active = active
            if error:
                self._last_error = error

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            state = "live" if self._connected else "reconnecting"
            if self._fallback_active:
                state = "polling"
            return {
                "state": state,
                "connected": self._connected,
                "fallback_active": self._fallback_active,
                "last_error": self._last_error,
                "last_connect_at": self._last_connect_at,
                "last_disconnect_at": self._last_disconnect_at,
                "reconnect_attempts": self._reconnect_attempts,
            }

    def is_connected(self) -> bool:
        with self._lock:
            return self._connected


def _quote_from_snapshot(symbol: str, snapshot: Dict[str, Any], provider: str) -> Optional[Dict[str, Any]]:
    trade = snapshot.get("latestTrade") if isinstance(snapshot.get("latestTrade"), dict) else {}
    quote = snapshot.get("latestQuote") if isinstance(snapshot.get("latestQuote"), dict) else {}
    daily = snapshot.get("dailyBar") if isinstance(snapshot.get("dailyBar"), dict) else {}
    price = _to_number(trade.get("p")) or _to_number(quote.get("ap")) or _to_number(quote.get("bp"))
    bid = _to_number(quote.get("bp"))
    ask = _to_number(quote.get("ap"))
    volume = _to_number(daily.get("v"))
    if volume is None:
        volume = _to_number(trade.get("s"))
    if price is None and bid is None and ask is None:
        return None
    timestamp = str(trade.get("t") or quote.get("t") or utc_now_iso())
    return {
        "symbol": symbol,
        "price": round(price, 4) if price is not None else None,
        "bid": round(bid, 4) if bid is not None else None,
        "ask": round(ask, 4) if ask is not None else None,
        "volume": int(volume) if volume is not None else None,
        "timestamp": timestamp,
        "source_status": "LIVE",
        "provider": provider,
    }


class AlpacaRestPollingProvider(MarketDataProvider):
    """On-demand Alpaca snapshots. No background thread and no websocket."""

    name = "AlpacaRestPollingProvider"

    def __init__(self) -> None:
        self._symbols: Set[str] = set()

    def start(self, symbols: Sequence[str]) -> None:
        self._symbols = set(symbols)

    def stop(self) -> None:
        self._symbols = set()

    def set_symbols(self, symbols: Sequence[str]) -> None:
        self._symbols = set(symbols)

    def get_quote(self, symbol: str) -> Optional[Dict[str, Any]]:
        return self.get_quotes([symbol]).get(symbol)

    def get_quotes(self, symbols: Sequence[str]) -> Dict[str, Dict[str, Any]]:
        cleaned = [normalize_ticker(symbol) for symbol in symbols if symbol]
        if not cleaned or not credentials_configured():
            return {}
        try:
            from integrations import alpaca_data

            snapshots = alpaca_data.get_snapshots(cleaned)
        except Exception:
            LOGGER.warning("Alpaca snapshot poll failed: %s", "request_error")
            return {}
        rows: Dict[str, Dict[str, Any]] = {}
        for symbol in cleaned:
            snapshot = snapshots.get(symbol) if isinstance(snapshots, dict) else None
            if not isinstance(snapshot, dict):
                continue
            payload = _quote_from_snapshot(symbol, snapshot, self.name)
            if payload is not None:
                rows[symbol] = payload
        return rows

    def health(self) -> Dict[str, Any]:
        return {"state": "polling", "provider": self.name, "symbols": sorted(self._symbols)}


class AlpacaStreamingProvider(MarketDataProvider):
    """Alpaca market-data websocket. Subscribes to quotes and trades. Never orders."""

    name = "AlpacaStreamingProvider"

    def __repr__(self) -> str:
        configured = bool(self._key and self._secret) if hasattr(self, "_key") else False
        return f"AlpacaStreamingProvider(feed={getattr(self, '_feed', '')}, configured={configured})"

    def __init__(
        self,
        on_quote: Callable[[Dict[str, Any]], None],
        health: ConnectionHealthService,
        on_event: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> None:
        self._on_quote = on_quote
        self._health = health
        self._on_event = on_event
        self._key = os.getenv("ALPACA_API_KEY_ID", "").strip()
        self._secret = os.getenv("ALPACA_API_SECRET_KEY", "").strip()
        self._feed = data_feed()
        self._stream_url = resolve_stream_url(self._feed)
        self._symbols: Set[str] = set()
        self._latest: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._stop = threading.Event()
        self._ws: Any = None
        self._last_auth_at: Optional[str] = None
        self._last_subscribe_at: Optional[str] = None
        self._last_message_at: Optional[str] = None
        self._message_counts: Dict[str, int] = {"quote": 0, "trade": 0}

    def _redact(self, text: str) -> str:
        redacted = text
        for secret in (self._key, self._secret):
            if secret:
                redacted = redacted.replace(secret, "[redacted]")
        return redacted

    def _emit_event(self, name: str, payload: Optional[Dict[str, Any]] = None) -> None:
        if not self._on_event:
            return
        safe: Dict[str, Any] = {}
        for key, value in (payload or {}).items():
            safe[key] = self._redact(value) if isinstance(value, str) else value
        try:
            self._on_event(name, safe)
        except Exception:
            return

    def _can_stream(self) -> Tuple[bool, Optional[str]]:
        if not self._key or not self._secret:
            return False, "alpaca credentials missing"
        if websocket is None:
            return False, "websocket library unavailable"
        return True, None

    def start(self, symbols: Sequence[str]) -> None:
        self.set_symbols(symbols)
        ok, reason = self._can_stream()
        if not ok:
            self._health.set_fallback(True, reason)
            LOGGER.warning("Alpaca data stream unavailable; REST fallback enabled: %s", reason)
            self._emit_event("provider_error", {"error": reason or "alpaca stream unavailable"})
            return
        if self._running:
            return
        self._running = True
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="alpaca-data-stream")
        self._thread.start()
        self._emit_event("provider_start", {"provider": self.name, "feed": self._feed})

    def stop(self) -> None:
        self._running = False
        self._stop.set()
        try:
            if self._ws is not None:
                self._ws.close()
        except Exception:
            pass
        self._emit_event("provider_stop", {"provider": self.name})
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2.0)

    def set_symbols(self, symbols: Sequence[str]) -> None:
        cleaned = []
        for symbol in symbols:
            normalized = normalize_ticker(symbol)
            valid, _error = validate_ticker(normalized)
            if valid:
                cleaned.append(normalized)
        with self._lock:
            self._symbols = set(cleaned)
            ws = self._ws if self._health.is_connected() else None
        if ws is not None:
            self._send_subscribe(ws)

    def _snapshot_symbols(self) -> List[str]:
        with self._lock:
            return sorted(self._symbols)

    def _on_message(self, _ws: Any, raw_message: str) -> None:
        try:
            payload = json.loads(raw_message)
        except Exception:
            return
        if not isinstance(payload, list):
            payload = [payload]
        for item in payload:
            if not isinstance(item, dict):
                continue
            msg_type = item.get("T")
            if msg_type in {"success", "subscription", "error"}:
                self._last_message_at = utc_now_iso()
                if msg_type == "error":
                    error_msg = self._redact(str(item.get("msg") or "alpaca stream error"))
                    self._health.disconnected(error_msg)
                    LOGGER.warning("Alpaca data stream error: %s", error_msg)
                    self._emit_event("provider_error", {"error": error_msg})
                if msg_type == "subscription":
                    self._emit_event("provider_subscription", {"symbols": self._snapshot_symbols()})
                continue
            if msg_type not in {"q", "t"}:
                continue
            symbol = normalize_ticker(str(item.get("S") or ""))
            if not symbol:
                continue
            price = _to_number(item.get("ap")) or _to_number(item.get("bp")) or _to_number(item.get("p"))
            bid = _to_number(item.get("bp"))
            ask = _to_number(item.get("ap"))
            volume = _to_number(item.get("s")) or _to_number(item.get("bs")) or _to_number(item.get("as"))
            quote = {
                "symbol": symbol,
                "price": round(price, 4) if price is not None else None,
                "bid": round(bid, 4) if bid is not None else None,
                "ask": round(ask, 4) if ask is not None else None,
                "volume": int(volume) if volume is not None else None,
                "timestamp": utc_now_iso(),
                "source_status": "LIVE",
                "provider": self.name,
            }
            with self._lock:
                self._latest[symbol] = quote
                self._last_message_at = quote["timestamp"]
                if msg_type == "q":
                    self._message_counts["quote"] += 1
                if msg_type == "t":
                    self._message_counts["trade"] += 1
            self._on_quote(quote)
            self._emit_event("provider_message", {"type": msg_type, "symbol": symbol})

    def _send_subscribe(self, ws: Any) -> None:
        symbols = self._snapshot_symbols() or list(DEFAULT_SYMBOLS)
        ws.send(json.dumps({"action": "subscribe", "quotes": symbols, "trades": symbols}))
        self._last_subscribe_at = utc_now_iso()
        self._emit_event("provider_subscribe", {"timestamp": self._last_subscribe_at, "symbols": symbols})

    def _authenticate_and_subscribe(self, ws: Any) -> None:
        # The secret leaves the process only inside this socket frame. It is not logged.
        ws.send(json.dumps({"action": "auth", "key": self._key, "secret": self._secret}))
        self._last_auth_at = utc_now_iso()
        self._emit_event("provider_auth", {"timestamp": self._last_auth_at})
        self._send_subscribe(ws)

    def _run(self) -> None:
        backoff = 1.0
        while self._running and not self._stop.is_set():
            try:
                attempts = self._health.reconnecting()
                self._emit_event("provider_reconnecting", {"attempt": attempts, "backoff_seconds": backoff})
                ws_module = websocket
                if ws_module is None:
                    self._health.set_fallback(True, "websocket library unavailable")
                    break

                def _on_open(ws_ref: Any) -> None:
                    self._health.connected()
                    self._health.set_fallback(False)
                    self._emit_event("provider_connected", {"timestamp": utc_now_iso(), "feed": self._feed})
                    self._authenticate_and_subscribe(ws_ref)

                ws = ws_module.WebSocketApp(
                    self._stream_url,
                    on_open=_on_open,
                    on_message=self._on_message,
                    on_error=lambda _ws_ref, err: (
                        self._health.disconnected(self._redact(str(err))),
                        self._emit_event("provider_error", {"error": self._redact(str(err))}),
                    ),
                    on_close=lambda _ws_ref, _code, _msg: (
                        self._health.disconnected("alpaca socket closed"),
                        self._emit_event("provider_disconnected", {"reason": "alpaca socket closed"}),
                    ),
                )
                self._ws = ws
                ws.run_forever(ping_interval=20, ping_timeout=10)
            except Exception as err:
                redacted = self._redact(str(err))
                self._health.disconnected(redacted)
                LOGGER.warning("Alpaca data stream error: %s", redacted)
                self._emit_event("provider_error", {"error": redacted})
            finally:
                self._ws = None

            if self._stop.is_set() or not self._running:
                break
            self._stop.wait(backoff)
            backoff = min(backoff * 2, 30)

        connected = self._health.snapshot().get("connected")
        self._health.set_fallback(not connected, None if connected else self._health.snapshot().get("last_error"))

    def get_quote(self, symbol: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._latest.get(symbol)
            return dict(row) if row else None

    def get_quotes(self, symbols: Sequence[str]) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            return {symbol: dict(self._latest[symbol]) for symbol in symbols if symbol in self._latest}

    def health(self) -> Dict[str, Any]:
        base = self._health.snapshot()
        base["provider"] = self.name
        base["symbols"] = self._snapshot_symbols()
        base["feed"] = self._feed
        base["stream_url"] = self._stream_url
        base["last_auth_at"] = self._last_auth_at
        base["last_subscribe_at"] = self._last_subscribe_at
        base["last_message_at"] = self._last_message_at
        base["message_counts"] = dict(self._message_counts)
        return base


class MarketStreamService:
    def __init__(self, socketio: Any = None, streaming: Optional[bool] = None) -> None:
        self._socketio = socketio
        self._lock = threading.Lock()
        self._max_symbols = _bounded_int("PLUTO_MARKET_MAX_SYMBOLS", 256, 1, 512)
        self._watch_symbols: OrderedDict[str, None] = OrderedDict()
        for symbol in DEFAULT_SYMBOLS:
            self._watch_symbols[symbol] = None
            if len(self._watch_symbols) >= self._max_symbols:
                break
        self._cache = MarketDataCache(
            ttl_seconds=_bounded_int("PLUTO_MARKET_CACHE_SECONDS", 30, 1, 3600),
            stale_seconds=_bounded_int("PLUTO_MARKET_STALE_SECONDS", 300, 1, 86400),
            max_symbols=self._max_symbols,
        )
        self._health = ConnectionHealthService()
        _raw, default_streaming, ignored = _provider_mode()
        self._ignored_provider = ignored
        self._streaming_mode = default_streaming if streaming is None else bool(streaming)
        self._provider_key = "alpaca" if self._streaming_mode else "rest"
        self._poll_provider = AlpacaRestPollingProvider()
        self._poll_interval = _bounded_int("PLUTO_MARKET_POLL_SECONDS", 15, 5, 300)
        self._poll_thread: Optional[threading.Thread] = None
        self._poll_stop = threading.Event()
        self._started = False
        self._lock_fh = None
        self._last_status_state: Optional[str] = None
        self._last_status_error: Optional[str] = None
        if self._streaming_mode:
            self._provider: MarketDataProvider = AlpacaStreamingProvider(
                self._ingest_provider_quote, self._health, on_event=self._provider_event
            )
        else:
            self._provider = self._poll_provider
            self._health.set_fallback(True)

    def _data_dir(self) -> Path:
        return Path(os.environ.get("PLUTO_DATA_DIR", "data"))

    def _acquire_stream_lock(self) -> bool:
        if self._lock_fh is not None:
            return True
        try:
            directory = self._data_dir()
            directory.mkdir(parents=True, exist_ok=True)
            handle = open(directory / "alpaca-market-stream.lock", "a+")
        except OSError:
            LOGGER.warning("Alpaca data stream lock unavailable; not opening a data socket")
            return False
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        self._lock_fh = handle
        return True

    def _release_stream_lock(self) -> None:
        handle = self._lock_fh
        self._lock_fh = None
        if handle is None:
            return
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            handle.close()
        except OSError:
            pass

    def _provider_event(self, event_name: str, payload: Dict[str, Any]) -> None:
        if event_name == "provider_error":
            self._emit("market_data_error", {"error": payload.get("error", "provider error"), "timestamp": utc_now_iso()})
        self._emit_status_events()

    def bind_socketio(self, socketio: Any) -> None:
        self._socketio = socketio

    def _emit(self, event: str, payload: Dict[str, Any]) -> None:
        if not self._socketio:
            return
        try:
            self._socketio.emit(event, payload)
        except Exception:
            return

    def _emit_status_events(self) -> None:
        status = self.connection_status()
        state = status.get("state")
        error_text = status.get("last_error")
        if state != self._last_status_state:
            if state == "polling":
                self._emit("fallback_activated", status)
            if self._last_status_state in {"polling", "reconnecting"} and state == "live":
                self._emit("stream_recovered", status)
        if error_text and error_text != self._last_status_error:
            self._emit("market_data_error", {"error": error_text, "timestamp": utc_now_iso()})
        self._last_status_state = state
        self._last_status_error = error_text
        self._emit("connection_status", status)

    def _normalized_quote_from_cache_row(
        self, row: Dict[str, Any], cache_status: str, age_seconds: Optional[int]
    ) -> NormalizedQuote:
        payload = row.get("payload", {})
        return NormalizedQuote(
            symbol=payload.get("symbol") or row.get("symbol"),
            price=payload.get("price"),
            bid=payload.get("bid"),
            ask=payload.get("ask"),
            volume=payload.get("volume"),
            provider=payload.get("provider") or self._provider.name,
            source_status=payload.get("source_status") or "UNAVAILABLE",
            timestamp=payload.get("timestamp") or row.get("timestamp") or utc_now_iso(),
            sequence=int(row.get("sequence") or 0),
            age_seconds=int(age_seconds or 0),
            cache_status=cache_status.upper(),
        )

    def _broadcast_quote(self, quote_payload: Dict[str, Any]) -> None:
        symbol = normalize_ticker(str(quote_payload.get("symbol") or ""))
        if not symbol:
            return
        self._cache.update(symbol, quote_payload)
        cached_row, cache_status, age = self._cache.get(symbol)
        if not cached_row:
            return
        normalized = self._normalized_quote_from_cache_row(cached_row, cache_status, age)
        self._emit("market_update", {"quote": normalized.as_dict(), "timestamp": utc_now_iso()})

    def _ingest_provider_quote(self, quote_payload: Dict[str, Any]) -> None:
        self._broadcast_quote(quote_payload)

    def _maybe_claim_stream(self) -> None:
        if not self._streaming_mode or self._lock_fh is not None:
            return
        if not self._acquire_stream_lock():
            self._health.set_fallback(True, "alpaca data stream already held by another process")
            return
        self._provider.start(self.symbols())

    def _poll_loop(self) -> None:
        while not self._poll_stop.is_set():
            self._cache.cleanup_stale()
            if self._streaming_mode:
                self._maybe_claim_stream()
            if self._streaming_mode and self._health.is_connected():
                self._health.set_fallback(False)
                self._emit_status_events()
                if self._poll_stop.wait(self._poll_interval):
                    break
                continue

            symbols = self.symbols()
            if not symbols:
                if self._poll_stop.wait(self._poll_interval):
                    break
                continue

            rows = self._poll_provider.get_quotes(symbols)
            if not rows:
                if credentials_configured():
                    self._health.set_fallback(True, "alpaca quotes unavailable")
                else:
                    self._health.set_fallback(True)
                self._emit(
                    "market_data_error",
                    {"error": self._health.snapshot().get("last_error") or "alpaca quotes unavailable", "timestamp": utc_now_iso()},
                )
                self._emit_status_events()
                if self._poll_stop.wait(self._poll_interval):
                    break
                continue

            for payload in rows.values():
                self._broadcast_quote(payload)
            self._health.set_fallback(True)
            self._emit_status_events()
            if self._poll_stop.wait(self._poll_interval):
                break

    def _ensure_polling_thread(self) -> None:
        if self._poll_thread and self._poll_thread.is_alive():
            return
        self._poll_stop.clear()
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True, name="market-poll-loop")
        self._poll_thread.start()

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
        if self._streaming_mode:
            if self._acquire_stream_lock():
                self._provider.start(self.symbols())
            else:
                self._health.set_fallback(True, "alpaca data stream already held by another process")
        else:
            self._provider.start(self.symbols())
        self._ensure_polling_thread()
        self._emit_status_events()

    def stop(self) -> None:
        with self._lock:
            if not self._started:
                self._release_stream_lock()
                return
            self._started = False
        self._provider.stop()
        self._poll_provider.stop()
        self._poll_stop.set()
        poll_thread = self._poll_thread
        if poll_thread and poll_thread.is_alive() and poll_thread is not threading.current_thread():
            poll_thread.join(timeout=2.0)
        self._release_stream_lock()

    def add_symbol(self, symbol: str) -> Tuple[bool, Optional[str]]:
        normalized = normalize_ticker(symbol)
        is_valid, error = validate_ticker(normalized)
        if not is_valid:
            return False, error
        with self._lock:
            if normalized in self._watch_symbols:
                self._watch_symbols.move_to_end(normalized)
            else:
                self._watch_symbols[normalized] = None
            while len(self._watch_symbols) > self._max_symbols:
                self._watch_symbols.popitem(last=False)
            symbols = list(self._watch_symbols.keys())
        self._provider.set_symbols(symbols)
        self._poll_provider.set_symbols(symbols)
        return True, None

    def symbols(self) -> List[str]:
        with self._lock:
            return list(self._watch_symbols.keys())

    def connection_status(self) -> Dict[str, Any]:
        status = self._health.snapshot()
        effective_provider = self._poll_provider.name if status.get("fallback_active") else self._provider.name
        status.update(
            {
                "provider": self._provider.name,
                "effective_provider": effective_provider,
                "active_provider": self._provider_key,
                "watch_symbols": self.symbols(),
                "max_symbols": self._max_symbols,
                "cache_ttl_seconds": self._cache.ttl_seconds,
                "stale_ttl_seconds": self._cache.stale_seconds,
                "cache_size": self._cache.size(),
                "timestamp": utc_now_iso(),
                "feed": data_feed(),
                "started": self._started,
                "autostart": autostart_enabled(),
                "orders_enabled": False,
                "ignored_provider": self._ignored_provider,
            }
        )
        if isinstance(self._provider, AlpacaStreamingProvider):
            status["stream_url"] = self._provider._stream_url
        return status

    def _success(self, quote: NormalizedQuote) -> Dict[str, Any]:
        return {
            "success": True,
            "error": None,
            "data": quote.as_dict(),
            "timestamp": utc_now_iso(),
            "data_status": quote.cache_status.lower(),
            "provider": quote.provider,
        }

    def _failure(self, error: str, provider: Optional[str] = None) -> Dict[str, Any]:
        return {
            "success": False,
            "error": error,
            "data": {},
            "timestamp": utc_now_iso(),
            "data_status": "unavailable",
            "provider": provider or self._provider.name,
        }

    def get_quote(self, symbol: str) -> Dict[str, Any]:
        normalized = normalize_ticker(symbol)
        is_valid, error = validate_ticker(normalized)
        if not is_valid:
            return self._failure(error or "Ticker format is invalid")
        self.add_symbol(normalized)

        fresh = None
        if self._streaming_mode and self._health.is_connected():
            fresh = self._provider.get_quote(normalized)
        if fresh is None:
            cached_row, cache_status, _age = self._cache.get(normalized)
            if cached_row and cache_status == "live":
                return self._success(self._normalized_quote_from_cache_row(cached_row, cache_status, _age))
            fresh = self._poll_provider.get_quote(normalized)
        if fresh is not None:
            row = self._cache.update(normalized, fresh)
            return self._success(self._normalized_quote_from_cache_row(row, "live", 0))

        cached_row, cache_status, age = self._cache.get(normalized)
        if cached_row and cache_status != "unavailable":
            quote = self._normalized_quote_from_cache_row(cached_row, cache_status, age)
            return self._success(quote)
        if cached_row:
            return self._failure("Quote unavailable", cached_row.get("payload", {}).get("provider"))
        return self._failure("Quote unavailable")

    def get_quotes(self, symbols: Optional[Iterable[str]] = None) -> Dict[str, Any]:
        requested = [normalize_ticker(str(item)) for item in (symbols or self.symbols()) if item]
        cleaned: List[str] = []
        validation_errors: Dict[str, str] = {}
        for item in requested:
            valid, err = validate_ticker(item)
            if valid:
                cleaned.append(item)
            else:
                validation_errors[item] = err or "invalid symbol"
        if len(cleaned) > self._max_symbols:
            cleaned = cleaned[: self._max_symbols]
        if not cleaned:
            return {
                "success": False,
                "error": "No valid symbols requested",
                "data": {"quotes": [], "validation_errors": validation_errors},
                "timestamp": utc_now_iso(),
                "data_status": "unavailable",
                "provider": self._provider.name,
            }
        for symbol in cleaned:
            self.add_symbol(symbol)

        if self._streaming_mode and self._health.is_connected():
            fetched = self._provider.get_quotes(cleaned)
        else:
            missing = []
            for symbol in cleaned:
                row, cache_status, _age = self._cache.get(symbol)
                if not (row and cache_status == "live"):
                    missing.append(symbol)
            fetched = self._poll_provider.get_quotes(missing) if missing else {}
        for symbol, payload in fetched.items():
            if isinstance(payload, dict):
                self._cache.update(symbol, payload)

        quotes: List[Dict[str, Any]] = []
        status_rank = {"live": 3, "stale": 2, "unavailable": 1}
        best = "unavailable"
        for symbol in cleaned:
            row, cache_status, age = self._cache.get(symbol)
            if not row or cache_status == "unavailable":
                continue
            quote = self._normalized_quote_from_cache_row(row, cache_status, age)
            quotes.append(quote.as_dict())
            if status_rank.get(cache_status, 0) > status_rank.get(best, 0):
                best = cache_status
        if not quotes:
            return {
                "success": False,
                "error": "No quotes available",
                "data": {"quotes": [], "validation_errors": validation_errors},
                "timestamp": utc_now_iso(),
                "data_status": "unavailable",
                "provider": self._provider.name,
            }
        return {
            "success": True,
            "error": None,
            "data": {"quotes": quotes, "validation_errors": validation_errors},
            "timestamp": utc_now_iso(),
            "data_status": best,
            "provider": quotes[0].get("provider") or self._provider.name,
        }


def get_service() -> MarketStreamService:
    """Process-local service. Opens the data socket only when autostart is on."""
    global _SERVICE
    with _SERVICE_LOCK:
        if _SERVICE is None:
            _SERVICE = MarketStreamService(streaming=True if autostart_enabled() else None)
        service = _SERVICE
    if autostart_enabled():
        service.start()
    return service


def reset_service_for_tests() -> None:
    global _SERVICE
    with _SERVICE_LOCK:
        service = _SERVICE
        _SERVICE = None
    if service is not None:
        service.stop()
