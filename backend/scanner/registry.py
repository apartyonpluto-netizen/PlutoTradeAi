from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from backend.scanner.market_scanner import SCAN_LIST, analyze_stock, scan_market
from backend.scanner.watchlist import get_watchlist


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_float(value: Any, fallback: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


class ScannerRegistry:
    def __init__(self) -> None:
        self.registry = {
            "overview": self._overview,
            "movers": self._movers,
            "volume": self._volume,
            "reversals": self._reversals,
            "breakouts": self._breakouts,
            "extended-hours": self._extended_hours,
            "futures": self._futures,
            "watchlist": self._watchlist,
            "upcoming": self._upcoming,
        }

    def run(self, scanner_name: str) -> Dict[str, Any]:
        if scanner_name not in self.registry:
            return {
                "success": False,
                "error": "Unknown scanner",
                "data": {},
                "timestamp": _utc_now_iso(),
            }

        return {
            "success": True,
            "error": None,
            "data": self.registry[scanner_name](),
            "timestamp": _utc_now_iso(),
        }

    def list_scanners(self) -> List[str]:
        return list(self.registry.keys())

    def _movers(self) -> Dict[str, Any]:
        rows = scan_market()
        biggest = rows[0] if rows else None
        return {
            "scanner": "Market Movers",
            "rows": rows,
            "largest_mover": biggest,
            "data_health": "ok" if rows else "degraded",
            "last_scan_time": _utc_now_iso(),
        }

    def _volume(self) -> Dict[str, Any]:
        rows = [item for item in scan_market() if _safe_float(item.get("relative_volume")) >= 1.2]
        rows.sort(key=lambda x: _safe_float(x.get("relative_volume")), reverse=True)
        return {
            "scanner": "Volume Scanner",
            "rows": rows,
            "strongest_relative_volume": rows[0] if rows else None,
            "data_health": "ok" if rows else "degraded",
            "last_scan_time": _utc_now_iso(),
        }

    def _reversals(self) -> Dict[str, Any]:
        rows = [item for item in scan_market() if abs(_safe_float(item.get("percent_change"))) >= 4]
        rows.sort(key=lambda x: abs(_safe_float(x.get("percent_change"))), reverse=True)
        return {
            "scanner": "Reversal Scanner",
            "rows": rows,
            "best_reversal_candidate": rows[0] if rows else None,
            "data_health": "ok" if rows else "degraded",
            "last_scan_time": _utc_now_iso(),
        }

    def _breakouts(self) -> Dict[str, Any]:
        rows = [
            item
            for item in scan_market()
            if _safe_float(item.get("percent_change")) > 2 and _safe_float(item.get("relative_volume")) > 1.4
        ]
        rows.sort(key=lambda x: _safe_float(x.get("scanner_score")), reverse=True)
        return {
            "scanner": "Breakout Scanner",
            "rows": rows,
            "best_breakout_candidate": rows[0] if rows else None,
            "data_health": "ok" if rows else "degraded",
            "last_scan_time": _utc_now_iso(),
        }

    def _extended_hours(self) -> Dict[str, Any]:
        rows = scan_market()
        return {
            "scanner": "Extended Hours",
            "rows": rows[:8],
            "notes": "Pre/post market columns depend on upstream provider availability.",
            "data_health": "ok" if rows else "degraded",
            "last_scan_time": _utc_now_iso(),
        }

    def _futures(self) -> Dict[str, Any]:
        futures_symbols = ["ES=F", "NQ=F", "YM=F", "RTY=F", "CL=F", "GC=F"]
        rows = []
        for symbol in futures_symbols:
            result = analyze_stock(symbol)
            if result:
                rows.append(result)

        rows.sort(key=lambda x: _safe_float(x.get("scanner_score")), reverse=True)
        return {
            "scanner": "Futures",
            "rows": rows,
            "data_health": "ok" if rows else "degraded",
            "last_scan_time": _utc_now_iso(),
        }

    def _watchlist(self) -> Dict[str, Any]:
        symbols = [item.get("ticker", "").upper() for item in get_watchlist() if item.get("ticker")]
        rows = []

        for symbol in symbols:
            result = analyze_stock(symbol)
            if result:
                rows.append(result)

        rows.sort(key=lambda x: _safe_float(x.get("scanner_score")), reverse=True)
        return {
            "scanner": "Watchlist Scanner",
            "rows": rows,
            "data_health": "ok" if rows else "degraded",
            "last_scan_time": _utc_now_iso(),
        }

    def _upcoming(self) -> Dict[str, Any]:
        rows = scan_market()
        rows = [item for item in rows if _safe_float(item.get("scanner_score")) >= 35]
        rows.sort(key=lambda x: _safe_float(x.get("scanner_score")), reverse=True)
        return {
            "scanner": "Upcoming Opportunities",
            "rows": rows,
            "data_health": "ok" if rows else "degraded",
            "last_scan_time": _utc_now_iso(),
        }

    def _overview(self) -> Dict[str, Any]:
        movers = self._movers()
        volume = self._volume()
        reversals = self._reversals()
        breakouts = self._breakouts()

        all_rows = movers.get("rows", [])
        highest_confidence = all_rows[0] if all_rows else None

        risk_candidate = None
        if all_rows:
            risk_candidate = min(all_rows, key=lambda x: _safe_float(x.get("scanner_score")))

        return {
            "scanner": "Overview",
            "highest_confidence_setup": highest_confidence,
            "largest_mover": movers.get("largest_mover"),
            "strongest_relative_volume": volume.get("strongest_relative_volume"),
            "best_reversal_candidate": reversals.get("best_reversal_candidate"),
            "best_breakout_candidate": breakouts.get("best_breakout_candidate"),
            "highest_risk_setup": risk_candidate,
            "data_health": "ok" if all_rows else "degraded",
            "last_scan_time": _utc_now_iso(),
            "rows": all_rows,
            "tracked_symbols": SCAN_LIST,
        }
