from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from backend.services.research_service import ResearchService
from backend.services.symbol_universe_service import SymbolUniverseService

SNAPSHOT_REFRESH_SECONDS = int(os.getenv("PLUTO_SCANNER_REFRESH_SECONDS", "300"))
SCANNER_UNIVERSE_LIMIT = int(os.getenv("PLUTO_SCANNER_UNIVERSE_LIMIT", "350"))
SCANNER_MAX_WORKERS = int(os.getenv("PLUTO_SCANNER_MAX_WORKERS", "8"))


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_epoch() -> float:
    return datetime.now(timezone.utc).timestamp()


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _status_label_from_data_status(data_status: str) -> str:
    value = str(data_status or "unavailable").upper()
    if value in {"LIVE", "DELAYED", "CACHED", "STALE", "PARTIAL"}:
        return value
    return "UNAVAILABLE"


class MarketScannerEngine:
    """Market-wide scanner snapshot engine with controlled concurrency and caching."""

    def __init__(self, universe: SymbolUniverseService, research: ResearchService) -> None:
        self._universe = universe
        self._research = research
        self._lock = threading.RLock()
        self._snapshot: Dict[str, Any] = {
            "rows": [],
            "generated_at": None,
            "source": "empty",
            "universe_size": 0,
            "errors": [],
        }
        self._last_refresh_epoch = 0.0

    def snapshot(self, force: bool = False) -> Dict[str, Any]:
        with self._lock:
            age = _now_epoch() - self._last_refresh_epoch
            if not force and self._snapshot.get("rows") and age <= SNAPSHOT_REFRESH_SECONDS:
                return self._build_snapshot_response(from_cache=True, age_seconds=int(age))

        rows = self._compute_rows()
        with self._lock:
            self._snapshot = {
                "rows": rows,
                "generated_at": utc_now_iso(),
                "source": "scanner-refresh",
                "universe_size": len(rows),
                "errors": [],
            }
            self._last_refresh_epoch = _now_epoch()
            return self._build_snapshot_response(from_cache=False, age_seconds=0)

    def scanners(self, force: bool = False) -> Dict[str, Any]:
        snap = self.snapshot(force=force)
        rows = snap.get("data", {}).get("rows", [])

        scanners = {
            "top_gainers": self._select(rows, lambda row: _to_float(row.get("percent_change")) > 0, key="percent_change", reverse=True, reason="Top % gain"),
            "top_losers": self._select(rows, lambda row: _to_float(row.get("percent_change")) < 0, key="percent_change", reverse=False, reason="Largest % drop"),
            "most_active": self._select(rows, lambda row: _to_float(row.get("volume")) > 0, key="volume", reverse=True, reason="Highest volume"),
            "unusual_relative_volume": self._select(rows, lambda row: _to_float(row.get("relative_volume")) >= 1.6, key="relative_volume", reverse=True, reason="Relative volume spike"),
            "gap_up": self._select(rows, lambda row: row.get("gap_status") == "GAP_UP", key="percent_change", reverse=True, reason="Gap up"),
            "gap_down": self._select(rows, lambda row: row.get("gap_status") == "GAP_DOWN", key="percent_change", reverse=False, reason="Gap down"),
            "momentum_leaders": self._select(rows, lambda row: _to_float(row.get("momentum")) > 0, key="momentum", reverse=True, reason="Positive momentum"),
            "breakout_candidates": self._select(rows, lambda row: row.get("breakout_status") == "BREAKOUT", key="confidence", reverse=True, reason="Trading above breakout level"),
            "reversal_candidates": self._select(rows, lambda row: _to_float(row.get("percent_change")) <= -3 and _to_float(row.get("relative_volume")) >= 1.3, key="confidence", reverse=True, reason="Potential oversold reversal"),
            "near_support": self._select(rows, lambda row: _to_float(row.get("distance_to_support_percent")) <= 2.5, key="distance_to_support_percent", reverse=False, reason="Near support"),
            "near_resistance": self._select(rows, lambda row: _to_float(row.get("distance_to_resistance_percent")) <= 2.5, key="distance_to_resistance_percent", reverse=False, reason="Near resistance"),
            "high_volatility": self._select(rows, lambda row: _to_float(row.get("volatility")) >= 2.5, key="volatility", reverse=True, reason="High realized volatility"),
            "plutotrade_high_confidence": self._select(rows, lambda row: _to_float(row.get("confidence")) >= 70, key="confidence", reverse=True, reason="Highest Pluto confidence"),
        }

        data = {
            "generated_at": snap.get("data", {}).get("generated_at"),
            "freshness": snap.get("data", {}).get("freshness"),
            "universe": snap.get("data", {}).get("universe"),
            "scanners": scanners,
        }
        return {
            "success": True,
            "data": data,
            "error": None,
            "timestamp": utc_now_iso(),
            "data_status": snap.get("data_status", "cached"),
            "provider": "PlutoScannerEngine",
        }

    def scanner_by_name(self, scanner_name: str, force: bool = False) -> Dict[str, Any]:
        response = self.scanners(force=force)
        if not response.get("success"):
            return response

        name = str(scanner_name or "").strip().lower().replace("-", "_")
        scanner_rows = response.get("data", {}).get("scanners", {}).get(name)
        if scanner_rows is None:
            return {
                "success": False,
                "data": {},
                "error": "Unknown scanner",
                "timestamp": utc_now_iso(),
                "data_status": "unavailable",
                "provider": "PlutoScannerEngine",
            }

        return {
            "success": True,
            "data": {
                "scanner": name,
                "rows": scanner_rows,
                "generated_at": response.get("data", {}).get("generated_at"),
                "freshness": response.get("data", {}).get("freshness"),
            },
            "error": None,
            "timestamp": utc_now_iso(),
            "data_status": response.get("data_status", "cached"),
            "provider": "PlutoScannerEngine",
        }

    def movers(self, force: bool = False) -> Dict[str, Any]:
        response = self.scanners(force=force)
        if not response.get("success"):
            return response
        scanners = response.get("data", {}).get("scanners", {})
        return {
            "success": True,
            "data": {
                "top_gainers": scanners.get("top_gainers", []),
                "top_losers": scanners.get("top_losers", []),
                "most_active": scanners.get("most_active", []),
                "generated_at": response.get("data", {}).get("generated_at"),
                "freshness": response.get("data", {}).get("freshness"),
            },
            "error": None,
            "timestamp": utc_now_iso(),
            "data_status": response.get("data_status", "cached"),
            "provider": "PlutoScannerEngine",
        }

    def _compute_rows(self) -> List[Dict[str, Any]]:
        self._universe.ensure_fresh()
        symbols = self._universe.symbols(max_symbols=SCANNER_UNIVERSE_LIMIT)
        symbol_rows = [row for row in symbols if row.get("asset_type") in {"stock", "etf"}]

        rows: List[Dict[str, Any]] = []
        workers = max(2, SCANNER_MAX_WORKERS)

        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_map = {executor.submit(self._build_row, row): row for row in symbol_rows}
            for future in as_completed(future_map):
                try:
                    row = future.result()
                except Exception:
                    continue
                if row:
                    rows.append(row)

        rows.sort(key=lambda item: _to_float(item.get("confidence")), reverse=True)
        return rows

    def _build_row(self, symbol_row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        symbol = symbol_row.get("symbol")
        if not symbol:
            return None

        quote = self._research.quote(symbol)
        technicals = self._research.technicals(symbol)
        analysis = self._research.analysis(symbol)

        if not quote.get("success"):
            return None

        q = quote.get("data", {})
        t = technicals.get("data", {}) if technicals.get("success") else {}
        a = analysis.get("data", {}) if analysis.get("success") else {}

        distance_to_support = None
        support = t.get("support_levels", {}).get("major_support") if isinstance(t.get("support_levels"), dict) else None
        resistance = t.get("resistance_levels", {}).get("major_resistance") if isinstance(t.get("resistance_levels"), dict) else None
        price = q.get("price")

        if price not in (None, 0) and support is not None:
            distance_to_support = abs(((price - support) / price) * 100)

        distance_to_resistance = None
        if price not in (None, 0) and resistance is not None:
            distance_to_resistance = abs(((resistance - price) / price) * 100)

        gap_status = t.get("gap_status")
        confidence = _to_float(a.get("confidence_score"))
        change = _to_float(q.get("change_percent"))
        rel_vol = _to_float(q.get("relative_volume"))
        momentum = _to_float(t.get("momentum"))

        if confidence <= 0:
            confidence = min(95.0, max(10.0, abs(change) * 8 + rel_vol * 16 + max(momentum, 0)))

        freshness = q.get("freshness", {})
        data_status = freshness.get("status") or quote.get("data_status")

        scanner_reason = "Balanced signal"
        if change >= 4:
            scanner_reason = "Strong price breakout"
        elif change <= -4:
            scanner_reason = "Heavy downside move"
        elif rel_vol >= 2:
            scanner_reason = "Abnormal relative volume"

        return {
            "symbol": symbol,
            "company_name": symbol_row.get("name"),
            "exchange": symbol_row.get("exchange"),
            "asset_type": symbol_row.get("asset_type"),
            "price": price,
            "percent_change": q.get("change_percent"),
            "volume": q.get("volume"),
            "relative_volume": q.get("relative_volume"),
            "market_cap": q.get("market_cap"),
            "trend": t.get("trend"),
            "momentum": t.get("momentum"),
            "volatility": t.get("volatility"),
            "breakout_status": t.get("breakout_status"),
            "gap_status": gap_status,
            "distance_to_support_percent": distance_to_support,
            "distance_to_resistance_percent": distance_to_resistance,
            "scanner_reason": scanner_reason,
            "confidence": round(confidence, 2),
            "freshness": {
                "status": _status_label_from_data_status(data_status),
                "age_seconds": freshness.get("age_seconds"),
                "last_updated": freshness.get("last_updated"),
            },
            "workspace_link": f"/stock/{symbol}",
        }

    def _select(
        self,
        rows: List[Dict[str, Any]],
        predicate,
        key: str,
        reverse: bool,
        reason: str,
        limit: int = 25,
    ) -> List[Dict[str, Any]]:
        filtered = [row for row in rows if predicate(row)]
        filtered.sort(key=lambda row: _to_float(row.get(key)), reverse=reverse)

        output = []
        for row in filtered[:limit]:
            output.append(
                {
                    "symbol": row.get("symbol"),
                    "company_name": row.get("company_name"),
                    "price": row.get("price"),
                    "percent_change": row.get("percent_change"),
                    "volume": row.get("volume"),
                    "relative_volume": row.get("relative_volume"),
                    "scanner_reason": row.get("scanner_reason") or reason,
                    "confidence": row.get("confidence"),
                    "freshness": row.get("freshness"),
                    "workspace_link": row.get("workspace_link"),
                }
            )
        return output

    def _build_snapshot_response(self, from_cache: bool, age_seconds: int) -> Dict[str, Any]:
        rows = list(self._snapshot.get("rows", []))
        data_status = "cached" if from_cache else "delayed"
        if from_cache and age_seconds > SNAPSHOT_REFRESH_SECONDS:
            data_status = "stale"

        return {
            "success": True,
            "data": {
                "rows": rows,
                "generated_at": self._snapshot.get("generated_at"),
                "freshness": {
                    "from_cache": from_cache,
                    "age_seconds": age_seconds,
                    "refresh_interval_seconds": SNAPSHOT_REFRESH_SECONDS,
                    "status": "STALE" if data_status == "stale" else "CACHED" if from_cache else "DELAYED",
                },
                "universe": {
                    "configured_limit": SCANNER_UNIVERSE_LIMIT,
                    "snapshot_size": len(rows),
                    "source": self._snapshot.get("source"),
                },
                "providers": self._research.provider_map(),
            },
            "error": None,
            "timestamp": utc_now_iso(),
            "data_status": data_status,
            "provider": "PlutoScannerEngine",
        }
