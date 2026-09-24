from __future__ import annotations

import csv
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

NASDAQ_URLS = [
    "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt",
    "https://ftp.nasdaqtrader.com/SymbolDirectory/nasdaqlisted.txt",
]
OTHER_URLS = [
    "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt",
    "https://ftp.nasdaqtrader.com/SymbolDirectory/otherlisted.txt",
]
SEC_COMPANY_EXCHANGE_JSON = "https://www.sec.gov/files/company_tickers_exchange.json"

DATA_PATH = Path("data/symbol_universe.json")
REFRESH_SECONDS = int(os.getenv("PLUTO_UNIVERSE_REFRESH_SECONDS", "43200"))
REQUEST_TIMEOUT_SECONDS = float(os.getenv("PLUTO_UNIVERSE_REQUEST_TIMEOUT", "8"))

FALLBACK_SYMBOLS = [
    {"symbol": "AAPL", "name": "Apple Inc.", "exchange": "NASDAQ", "asset_type": "stock"},
    {"symbol": "TSLA", "name": "Tesla Inc.", "exchange": "NASDAQ", "asset_type": "stock"},
    {"symbol": "NVDA", "name": "NVIDIA Corporation", "exchange": "NASDAQ", "asset_type": "stock"},
    {"symbol": "MSFT", "name": "Microsoft Corporation", "exchange": "NASDAQ", "asset_type": "stock"},
    {"symbol": "QQQ", "name": "Invesco QQQ Trust", "exchange": "NASDAQ", "asset_type": "etf"},
    {"symbol": "SPY", "name": "SPDR S&P 500 ETF Trust", "exchange": "NYSE Arca", "asset_type": "etf"},
    {"symbol": "IWM", "name": "iShares Russell 2000 ETF", "exchange": "NYSE Arca", "asset_type": "etf"},
    {"symbol": "TQQQ", "name": "ProShares UltraPro QQQ", "exchange": "NASDAQ", "asset_type": "etf"},
]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_epoch() -> float:
    return datetime.now(timezone.utc).timestamp()


def _safe_str(value: Any) -> str:
    return str(value or "").strip()


def _to_bool_flag(value: Any) -> bool:
    return _safe_str(value).upper() in {"Y", "YES", "TRUE", "1"}


def _normalize_symbol(symbol: str) -> str:
    return _safe_str(symbol).upper()


def _display_name(name: str, symbol: str) -> str:
    clean_name = _safe_str(name)
    return clean_name or _normalize_symbol(symbol)


class SymbolUniverseService:
    """Maintains a refreshable US symbol universe used by search and scanners."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._symbols_by_key: Dict[str, Dict[str, Any]] = {}
        self._last_refresh_epoch = 0.0
        self._last_refresh_iso: Optional[str] = None
        self._last_source = "fallback"
        self._last_error: Optional[str] = None
        self._load_cached()

    def ensure_fresh(self, force: bool = False) -> Dict[str, Any]:
        with self._lock:
            age = _now_epoch() - self._last_refresh_epoch
            if not force and self._symbols_by_key and age <= REFRESH_SECONDS and self._last_source != "fallback":
                return self.status()

        try:
            payload = self._download_universe()
        except Exception as exc:  # pragma: no cover - external network behavior
            with self._lock:
                self._last_error = str(exc)
                if not self._symbols_by_key:
                    self._load_fallback_only()
                return self.status()

        with self._lock:
            self._symbols_by_key = payload["symbols_by_key"]
            self._last_refresh_epoch = _now_epoch()
            self._last_refresh_iso = utc_now_iso()
            self._last_source = payload["source"]
            self._last_error = None
            self._save_cached()
            return self.status()

    def status(self) -> Dict[str, Any]:
        with self._lock:
            age = int(max(_now_epoch() - self._last_refresh_epoch, 0)) if self._last_refresh_epoch else None
            return {
                "symbol_count": len(self._symbols_by_key),
                "last_refresh": self._last_refresh_iso,
                "refresh_age_seconds": age,
                "source": self._last_source,
                "refresh_interval_seconds": REFRESH_SECONDS,
                "error": self._last_error,
            }

    def resolve(self, symbol: str) -> Optional[Dict[str, Any]]:
        key = _normalize_symbol(symbol)
        if not key:
            return None

        with self._lock:
            item = self._symbols_by_key.get(key)
            return dict(item) if item else None

    def search(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        clean = _normalize_symbol(query)
        if limit <= 0:
            limit = 20

        with self._lock:
            rows = list(self._symbols_by_key.values())

        if not clean:
            rows.sort(key=lambda row: (row.get("symbol", ""), row.get("name", "")))
            return rows[:limit]

        prefix_matches = []
        contains_matches = []
        for row in rows:
            symbol = row.get("symbol", "")
            name = str(row.get("name", "")).upper()
            if symbol.startswith(clean) or name.startswith(clean):
                prefix_matches.append(row)
            elif clean in symbol or clean in name:
                contains_matches.append(row)

        ordered = prefix_matches + contains_matches
        return ordered[:limit]

    def symbols(self, max_symbols: int = 0, exchanges: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        with self._lock:
            rows = list(self._symbols_by_key.values())

        if exchanges:
            exchange_set = {item.upper() for item in exchanges}
            rows = [row for row in rows if str(row.get("exchange", "")).upper() in exchange_set]

        rows.sort(key=lambda row: row.get("symbol", ""))
        if max_symbols > 0:
            return rows[:max_symbols]
        return rows

    def _load_cached(self) -> None:
        if not DATA_PATH.exists():
            self._load_fallback_only()
            return

        try:
            payload = json.loads(DATA_PATH.read_text(encoding="utf-8"))
        except Exception:
            self._load_fallback_only()
            return

        symbols = payload.get("symbols") if isinstance(payload, dict) else None
        if not isinstance(symbols, list):
            self._load_fallback_only()
            return

        symbol_map: Dict[str, Dict[str, Any]] = {}
        for row in symbols:
            if not isinstance(row, dict):
                continue
            symbol = _normalize_symbol(row.get("symbol", ""))
            if not symbol:
                continue
            row["symbol"] = symbol
            symbol_map[symbol] = row

        if not symbol_map:
            self._load_fallback_only()
            return

        self._symbols_by_key = symbol_map
        self._last_refresh_iso = payload.get("last_refresh")
        self._last_refresh_epoch = payload.get("last_refresh_epoch") or 0.0
        self._last_source = payload.get("source") or "cache"
        self._last_error = payload.get("error")

    def _save_cached(self) -> None:
        DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "last_refresh": self._last_refresh_iso,
            "last_refresh_epoch": self._last_refresh_epoch,
            "source": self._last_source,
            "error": self._last_error,
            "symbols": list(self._symbols_by_key.values()),
        }
        DATA_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _load_fallback_only(self) -> None:
        self._symbols_by_key = {
            _normalize_symbol(row["symbol"]): {
                "symbol": _normalize_symbol(row["symbol"]),
                "name": row["name"],
                "exchange": row["exchange"],
                "asset_type": row["asset_type"],
                "is_test_issue": False,
                "is_active": True,
                "source": "fallback",
            }
            for row in FALLBACK_SYMBOLS
        }
        self._last_source = "fallback"
        if not self._last_refresh_iso:
            self._last_refresh_iso = utc_now_iso()
            self._last_refresh_epoch = _now_epoch()

    def _download_universe(self) -> Dict[str, Any]:
        try:
            nasdaq_rows = self._download_pipe_table(NASDAQ_URLS)
            other_rows = self._download_pipe_table(OTHER_URLS)
            return self._build_from_nasdaq_rows(nasdaq_rows, other_rows)
        except Exception:
            sec_map = self._download_sec_universe()
            if sec_map:
                # Keep key ETFs represented even when SEC feed excludes them.
                for row in FALLBACK_SYMBOLS:
                    key = _normalize_symbol(row["symbol"])
                    sec_map.setdefault(
                        key,
                        {
                            "symbol": key,
                            "name": row["name"],
                            "exchange": row["exchange"],
                            "asset_type": row["asset_type"],
                            "is_test_issue": False,
                            "is_active": True,
                            "source": "fallback",
                        },
                    )
                return {"symbols_by_key": sec_map, "source": "sec"}
            raise

    def _build_from_nasdaq_rows(self, nasdaq_rows: List[Dict[str, Any]], other_rows: List[Dict[str, Any]]) -> Dict[str, Any]:

        symbol_map: Dict[str, Dict[str, Any]] = {}

        for row in nasdaq_rows:
            symbol = _normalize_symbol(row.get("Symbol", ""))
            if not symbol:
                continue
            if _to_bool_flag(row.get("Test Issue")):
                continue
            if _safe_str(row.get("Financial Status", "")).upper() in {"D", "E"}:
                continue

            etf = _to_bool_flag(row.get("ETF"))
            symbol_map[symbol] = {
                "symbol": symbol,
                "name": _display_name(row.get("Security Name", ""), symbol),
                "exchange": "NASDAQ",
                "asset_type": "etf" if etf else "stock",
                "is_test_issue": False,
                "is_active": True,
                "source": "nasdaqtrader",
            }

        for row in other_rows:
            symbol = _normalize_symbol(row.get("ACT Symbol", ""))
            if not symbol:
                continue
            if _to_bool_flag(row.get("Test Issue")):
                continue

            exchange_code = _safe_str(row.get("Exchange", "")).upper()
            exchange = {
                "A": "NYSE American",
                "N": "NYSE",
                "P": "NYSE Arca",
                "Q": "NASDAQ",
                "V": "IEX",
                "Z": "BATS",
            }.get(exchange_code, "OTHER")
            etf = _to_bool_flag(row.get("ETF"))

            symbol_map[symbol] = {
                "symbol": symbol,
                "name": _display_name(row.get("Security Name", ""), symbol),
                "exchange": exchange,
                "asset_type": "etf" if etf else "stock",
                "is_test_issue": False,
                "is_active": True,
                "source": "nasdaqtrader",
            }

        # Ensure fallback critical symbols are still represented if remote format changes.
        for row in FALLBACK_SYMBOLS:
            key = _normalize_symbol(row["symbol"])
            if key not in symbol_map:
                symbol_map[key] = {
                    "symbol": key,
                    "name": row["name"],
                    "exchange": row["exchange"],
                    "asset_type": row["asset_type"],
                    "is_test_issue": False,
                    "is_active": True,
                    "source": "fallback",
                }

        return {"symbols_by_key": symbol_map, "source": "nasdaqtrader"}

    def _download_sec_universe(self) -> Dict[str, Dict[str, Any]]:
        headers = {
            "User-Agent": "PlutoTradeAI/1.0 support@plutotrade.local",
            "Accept": "application/json",
        }
        response = requests.get(SEC_COMPANY_EXCHANGE_JSON, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        payload = response.json()

        rows = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            return {}

        symbol_map: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, list) or len(row) < 4:
                continue

            ticker = _normalize_symbol(row[2])
            name = _safe_str(row[1])
            exchange = _safe_str(row[3]).upper()
            if not ticker or not exchange:
                continue

            if exchange not in {"NASDAQ", "NYSE", "NYSE AMERICAN", "NYSE ARCA"}:
                continue

            symbol_map[ticker] = {
                "symbol": ticker,
                "name": _display_name(name, ticker),
                "exchange": exchange,
                "asset_type": "stock",
                "is_test_issue": False,
                "is_active": True,
                "source": "sec",
            }

        return symbol_map

    def _download_pipe_table(self, urls: List[str]) -> List[Dict[str, Any]]:
        last_error: Optional[Exception] = None
        text = None

        for url in urls:
            try:
                response = requests.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
                response.raise_for_status()
                text = response.text
                break
            except Exception as exc:  # pragma: no cover - network behavior
                last_error = exc
                continue

        if text is None:
            raise RuntimeError(f"Unable to fetch symbol directory from {urls}: {last_error}")

        rows = []
        reader = csv.DictReader(text.splitlines(), delimiter="|")
        for row in reader:
            if row is None:
                continue
            if row.get("Symbol") == "File Creation Time" or row.get("ACT Symbol") == "File Creation Time":
                continue
            if row.get("Symbol") == "":
                continue
            rows.append(row)
        return rows
