from __future__ import annotations

import json
import math
import os
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import yfinance as yf

try:
    import websocket  # type: ignore
except Exception:  # pragma: no cover - optional dependency fallback at runtime
    websocket = None

from backend.services.market_data_service import normalize_ticker, validate_ticker


DEFAULT_SYMBOLS = ["SPY", "QQQ", "IWM", "DIA", "TSLA", "NVDA"]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_epoch() -> float:
    return time.time()


def _to_number(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(num):
        return None
    return num


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
    def __init__(self, ttl_seconds: int = 15, stale_seconds: int = 120) -> None:
        self.ttl_seconds = max(1, ttl_seconds)
        self.stale_seconds = max(self.ttl_seconds, stale_seconds)
        self._lock = threading.Lock()
        self._rows: Dict[str, Dict[str, Any]] = {}
        self._sequence = 0

    def update(self, symbol: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        now = _now_epoch()
        with self._lock:
            self._sequence += 1
            seq = self._sequence
            row = {
                "symbol": symbol,
                "payload": payload,
                "updated_at": now,
                "timestamp": utc_now_iso(),
                "sequence": seq,
            }
            self._rows[symbol] = row
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

    def get_many(self, symbols: Sequence[str]) -> Dict[str, Tuple[Optional[Dict[str, Any]], str, Optional[int]]]:
        return {symbol: self.get(symbol) for symbol in symbols}


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


class YFinancePollingProvider(MarketDataProvider):
    name = "YFinancePollingProvider"

    def __init__(self) -> None:
        self._symbols: Set[str] = set()

    def start(self, symbols: Sequence[str]) -> None:
        self._symbols = set(symbols)

    def stop(self) -> None:
        self._symbols = set()

    def set_symbols(self, symbols: Sequence[str]) -> None:
        self._symbols = set(symbols)

    def _fetch_one(self, symbol: str) -> Optional[Dict[str, Any]]:
        try:
            ticker = yf.Ticker(symbol)
            fast = ticker.fast_info if hasattr(ticker, "fast_info") else {}
            fast = dict(fast) if fast else {}
            price = _to_number(fast.get("lastPrice")) or _to_number(fast.get("regularMarketPrice"))
            bid = _to_number(fast.get("bid"))
            ask = _to_number(fast.get("ask"))
            volume = _to_number(fast.get("lastVolume")) or _to_number(fast.get("regularMarketVolume"))
            return {
                "symbol": symbol,
                "price": round(price, 4) if price is not None else None,
                "bid": round(bid, 4) if bid is not None else None,
                "ask": round(ask, 4) if ask is not None else None,
                "volume": int(volume) if volume is not None else None,
                "timestamp": utc_now_iso(),
                "source_status": "DELAYED",
                "provider": self.name,
            }
        except Exception:
            return None

    def get_quote(self, symbol: str) -> Optional[Dict[str, Any]]:
        return self._fetch_one(symbol)

    def get_quotes(self, symbols: Sequence[str]) -> Dict[str, Dict[str, Any]]:
        rows: Dict[str, Dict[str, Any]] = {}
        for symbol in symbols:
            payload = self._fetch_one(symbol)
            if payload is not None:
                rows[symbol] = payload
        return rows

    def health(self) -> Dict[str, Any]:
        return {"state": "polling", "provider": self.name, "symbols": sorted(self._symbols)}


class AlpacaStreamingProvider(MarketDataProvider):
    name = "AlpacaStreamingProvider"

    def __init__(self, on_quote: Callable[[Dict[str, Any]], None], health: ConnectionHealthService) -> None:
        self._on_quote = on_quote
        self._health = health
        self._key = os.getenv("ALPACA_API_KEY", "").strip()
        self._secret = os.getenv("ALPACA_API_SECRET", "").strip()
        self._feed = os.getenv("ALPACA_DATA_FEED", "iex").strip().lower() or "iex"
        self._stream_url = os.getenv("ALPACA_WS_URL", "wss://stream.data.alpaca.markets/v2/sip")
        if self._feed == "iex":
            self._stream_url = os.getenv("ALPACA_WS_URL", "wss://stream.data.alpaca.markets/v2/iex")

        self._symbols: Set[str] = set()
        self._latest: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._stop = threading.Event()
        self._ws = None

    def _can_stream(self) -> Tuple[bool, Optional[str]]:
        if websocket is None:
            return False, "websocket-client library unavailable"
        if not self._key or not self._secret:
            return False, "alpaca credentials missing"
        return True, None

    def start(self, symbols: Sequence[str]) -> None:
        self.set_symbols(symbols)
        ok, reason = self._can_stream()
        if not ok:
            self._health.set_fallback(True, reason)
            return
        if self._running:
            return

        self._running = True
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="alpaca-stream")
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        self._stop.set()
        try:
            if self._ws is not None:
                self._ws.close()
        except Exception:
            pass

    def set_symbols(self, symbols: Sequence[str]) -> None:
        with self._lock:
            self._symbols = set(symbols)

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
                if msg_type == "error":
                    self._health.disconnected(str(item.get("msg") or "alpaca stream error"))
                continue
            if msg_type not in {"q", "t"}:
                continue

            symbol = normalize_ticker(item.get("S") or "")
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

            self._on_quote(quote)

    def _authenticate_and_subscribe(self, ws: Any) -> None:
        auth_payload = {"action": "auth", "key": self._key, "secret": self._secret}
        ws.send(json.dumps(auth_payload))

        symbols = self._snapshot_symbols()
        if not symbols:
            symbols = DEFAULT_SYMBOLS

        subscribe_payload = {
            "action": "subscribe",
            "quotes": symbols,
            "trades": symbols,
        }
        ws.send(json.dumps(subscribe_payload))

    def _run(self) -> None:
        backoff = 1.0
        while self._running and not self._stop.is_set():
            try:
                self._health.reconnecting()
                self._health.set_fallback(False)
                ws = websocket.WebSocketApp(  # type: ignore[union-attr]
                    self._stream_url,
                    on_open=lambda ws_ref: (self._health.connected(), self._authenticate_and_subscribe(ws_ref)),
                    on_message=self._on_message,
                    on_error=lambda _ws_ref, err: self._health.disconnected(str(err)),
                    on_close=lambda _ws_ref, _code, _msg: self._health.disconnected("alpaca socket closed"),
                )
                self._ws = ws
                ws.run_forever(ping_interval=20, ping_timeout=10)
            except Exception as err:
                self._health.disconnected(str(err))

            if self._stop.is_set() or not self._running:
                break

            time.sleep(backoff)
            backoff = min(backoff * 2, 30)

        self._health.set_fallback(not self._health.snapshot().get("connected"), self._health.snapshot().get("last_error"))

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
        return base


class MarketStreamService:
    def __init__(self, socketio: Any = None) -> None:
        self._socketio = socketio
        self._lock = threading.Lock()
        self._watch_symbols: Set[str] = set(DEFAULT_SYMBOLS)
        self._cache = MarketDataCache(
            ttl_seconds=int(os.getenv("PLUTO_MARKET_CACHE_SECONDS", "30")),
            stale_seconds=int(os.getenv("PLUTO_MARKET_STALE_SECONDS", "300")),
        )
        self._health = ConnectionHealthService()
        self._provider_key = os.getenv("PLUTO_MARKET_PROVIDER", "research").strip().lower()
        self._provider: MarketDataProvider
        self._poll_provider = YFinancePollingProvider()
        self._poll_interval = max(5, int(os.getenv("PLUTO_MARKET_POLL_SECONDS", "15")))
        self._poll_thread: Optional[threading.Thread] = None
        self._poll_stop = threading.Event()
        self._started = False
        self._last_status_state: Optional[str] = None

        if self._provider_key in {"alpaca", "streaming"}:
            self._provider = AlpacaStreamingProvider(self._ingest_provider_quote, self._health)
        else:
            self._provider = self._poll_provider
            self._health.set_fallback(True)

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
        if state != self._last_status_state:
            if state == "polling":
                self._emit("fallback_activated", status)
            if self._last_status_state in {"polling", "reconnecting"} and state == "live":
                self._emit("stream_recovered", status)
        self._last_status_state = state
        self._emit("connection_status", status)

    def _normalized_quote_from_cache_row(self, row: Dict[str, Any], cache_status: str, age_seconds: Optional[int]) -> NormalizedQuote:
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
        symbol = normalize_ticker(quote_payload.get("symbol") or "")
        if not symbol:
            return
        row = self._cache.update(symbol, quote_payload)
        cached_row, cache_status, age = self._cache.get(symbol)
        if not cached_row:
            return
        normalized = self._normalized_quote_from_cache_row(cached_row, cache_status, age)
        self._emit("market_update", {"quote": normalized.as_dict(), "timestamp": utc_now_iso()})

    def _ingest_provider_quote(self, quote_payload: Dict[str, Any]) -> None:
        self._broadcast_quote(quote_payload)

    def _poll_loop(self) -> None:
        while not self._poll_stop.is_set():
            symbols = self.symbols()
            if not symbols:
                time.sleep(self._poll_interval)
                continue

            rows = self._poll_provider.get_quotes(symbols)
            if not rows:
                self._emit("market_data_error", {"error": "No polling quotes available", "timestamp": utc_now_iso()})
                self._health.set_fallback(True, "polling quotes unavailable")
                self._emit_status_events()
                time.sleep(self._poll_interval)
                continue

            for payload in rows.values():
                self._broadcast_quote(payload)

            # Polling is a fallback stream state.
            self._health.set_fallback(True)
            self._emit_status_events()
            time.sleep(self._poll_interval)

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

        symbols = self.symbols()
        self._provider.start(symbols)
        self._ensure_polling_thread()
        self._emit_status_events()

    def stop(self) -> None:
        with self._lock:
            if not self._started:
                return
            self._started = False
        self._provider.stop()
        self._poll_stop.set()

    def add_symbol(self, symbol: str) -> Tuple[bool, Optional[str]]:
        normalized = normalize_ticker(symbol)
        is_valid, error = validate_ticker(normalized)
        if not is_valid:
            return False, error
        with self._lock:
            self._watch_symbols.add(normalized)
            symbols = sorted(self._watch_symbols)
        self._provider.set_symbols(symbols)
        return True, None

    def symbols(self) -> List[str]:
        with self._lock:
            return sorted(self._watch_symbols)

    def connection_status(self) -> Dict[str, Any]:
        status = self._health.snapshot()
        status.update(
            {
                "provider": self._provider.name,
                "active_provider": self._provider_key,
                "watch_symbols": self.symbols(),
                "cache_ttl_seconds": self._cache.ttl_seconds,
                "stale_ttl_seconds": self._cache.stale_seconds,
                "timestamp": utc_now_iso(),
            }
        )
        return status

    def get_quote(self, symbol: str) -> Dict[str, Any]:
        normalized = normalize_ticker(symbol)
        is_valid, error = validate_ticker(normalized)
        if not is_valid:
            return {
                "success": False,
                "error": error,
                "data": {},
                "timestamp": utc_now_iso(),
                "data_status": "unavailable",
                "provider": self._provider.name,
            }

        self.add_symbol(normalized)

        fresh = self._provider.get_quote(normalized)
        if fresh is not None:
            row = self._cache.update(normalized, fresh)
            normalized_quote = self._normalized_quote_from_cache_row(row, "live", 0)
            return {
                "success": True,
                "error": None,
                "data": normalized_quote.as_dict(),
                "timestamp": utc_now_iso(),
                "data_status": normalized_quote.cache_status.lower(),
                "provider": normalized_quote.provider,
            }

        cached_row, cache_status, age = self._cache.get(normalized)
        if cached_row:
            normalized_quote = self._normalized_quote_from_cache_row(cached_row, cache_status, age)
            data_status = "unavailable" if cache_status == "unavailable" else normalized_quote.cache_status.lower()
            return {
                "success": cache_status != "unavailable",
                "error": None if cache_status != "unavailable" else "Quote unavailable",
                "data": normalized_quote.as_dict(),
                "timestamp": utc_now_iso(),
                "data_status": data_status,
                "provider": normalized_quote.provider,
            }

        return {
            "success": False,
            "error": "Quote unavailable",
            "data": {},
            "timestamp": utc_now_iso(),
            "data_status": "unavailable",
            "provider": self._provider.name,
        }

    def get_quotes(self, symbols: Optional[Iterable[str]] = None) -> Dict[str, Any]:
        requested = [normalize_ticker(item) for item in (symbols or self.symbols()) if item]
        cleaned: List[str] = []
        validation_errors: Dict[str, str] = {}
        for item in requested:
            valid, err = validate_ticker(item)
            if valid:
                cleaned.append(item)
            else:
                validation_errors[item] = err or "invalid symbol"

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

        fresh_rows = self._provider.get_quotes(cleaned)
        for symbol, payload in fresh_rows.items():
            self._cache.update(symbol, payload)

        quotes: List[Dict[str, Any]] = []
        status_rank = {"live": 3, "stale": 2, "unavailable": 1}
        best = "unavailable"
        for symbol in cleaned:
            row, cache_status, age = self._cache.get(symbol)
            if not row:
                continue
            normalized_quote = self._normalized_quote_from_cache_row(row, cache_status, age)
            quotes.append(normalized_quote.as_dict())
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
            "provider": self._provider.name,
        }
