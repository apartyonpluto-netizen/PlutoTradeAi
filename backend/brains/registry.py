from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from backend.brains.candle_brain import analyze_candle
from backend.brains.confidence_engine import calculate_confidence
from backend.brains.options_brain import build_options_plan
from backend.brains.risk_brain import calculate_risk_plan
from backend.brains.support_resistance import find_support_resistance
from backend.services.market_data_service import get_stock_snapshot


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class BrainRegistry:
    def __init__(self) -> None:
        self.registry = {
            "overview": self._brain_overview,
            "candle": self._candle_brain,
            "pattern": self._pattern_brain,
            "trend": self._trend_brain,
            "volume": self._volume_brain,
            "support-resistance": self._support_resistance_brain,
            "strategy": self._strategy_brain,
            "options": self._options_brain,
            "risk": self._risk_brain,
            "market-readiness": self._market_readiness,
            "confidence": self._confidence_engine,
        }

    def run_overview(self, ticker: str) -> Dict[str, Any]:
        return self.run("overview", ticker)

    def run(self, brain_name: str, ticker: str) -> Dict[str, Any]:
        if brain_name not in self.registry:
            return {
                "success": False,
                "error": "Unknown brain",
                "data": {},
                "timestamp": _utc_now_iso(),
            }

        try:
            data = self.registry[brain_name](ticker)
        except Exception as exc:
            return {
                "success": False,
                "error": str(exc),
                "data": {},
                "timestamp": _utc_now_iso(),
            }

        return {
            "success": True,
            "error": None,
            "data": data,
            "timestamp": _utc_now_iso(),
        }

    def _base_payload(self, ticker: str) -> Dict[str, Any]:
        snapshot = get_stock_snapshot(ticker)
        if not snapshot.get("success"):
            raise ValueError(snapshot.get("error") or "Ticker data is unavailable")

        stock = snapshot["data"]
        levels = find_support_resistance(stock["ticker"])
        if not levels:
            levels = {
                "support": None,
                "resistance": None,
                "distance_to_support_percent": None,
                "distance_to_resistance_percent": None,
                "latest_close": stock.get("current_price"),
            }

        open_price = stock.get("previous_close") or stock.get("current_price") or 0
        close_price = stock.get("current_price") or open_price
        high_price = stock.get("day_high") or close_price
        low_price = stock.get("day_low") or close_price

        candle = analyze_candle(open_price, high_price, low_price, close_price)

        scanner_score = 0
        pct_move = _safe_float(stock.get("daily_change_percent"))
        rel_vol = _safe_float(stock.get("relative_volume"))

        if abs(pct_move) >= 3:
            scanner_score += 25
        if rel_vol >= 1.5:
            scanner_score += 25
        if pct_move > 0:
            scanner_score += 10

        confidence_input = {
            "scanner_score": scanner_score,
            "percent_change": pct_move,
            "relative_volume": rel_vol,
            "distance_to_support_percent": _safe_float(levels.get("distance_to_support_percent"), 99),
            "distance_to_resistance_percent": _safe_float(levels.get("distance_to_resistance_percent"), 99),
            "candle_score": candle.get("score", 0),
            "candle_type": candle.get("candle_type", "Unknown"),
            "on_watchlist": False,
        }
        confidence = calculate_confidence(confidence_input)

        options_input = {
            "ticker": stock["ticker"],
            "latest_close": stock.get("current_price") or close_price,
            "confidence": confidence["confidence"],
            "candle_bias": candle["bias"],
            "distance_to_support_percent": _safe_float(levels.get("distance_to_support_percent"), 99),
            "distance_to_resistance_percent": _safe_float(levels.get("distance_to_resistance_percent"), 99),
            "percent_change": pct_move,
        }
        options = build_options_plan(options_input)
        risk = calculate_risk_plan(options)

        return {
            "ticker": stock["ticker"],
            "stock": stock,
            "levels": levels,
            "candle": candle,
            "confidence": confidence,
            "options": options,
            "risk": risk,
            "data_quality": snapshot.get("data_status", "unavailable"),
            "timestamp": _utc_now_iso(),
        }

    def _brain_overview(self, ticker: str) -> Dict[str, Any]:
        base = self._base_payload(ticker)
        stock = base["stock"]
        levels = base["levels"]
        options = base["options"]
        confidence = base["confidence"]
        risk = base["risk"]
        candle = base["candle"]

        if options["contract_type"] == "CALL":
            call_put_wait = "CALL"
            bias = "Bullish"
        elif options["contract_type"] == "PUT":
            call_put_wait = "PUT"
            bias = "Bearish"
        else:
            call_put_wait = "WAIT"
            bias = "Neutral"

        return {
            "ticker": base["ticker"],
            "bias": bias,
            "confidence": confidence["confidence"],
            "recommended_action": confidence["suggested_action"],
            "call_put_wait_bias": call_put_wait,
            "recommended_strategy": options["reason"],
            "support": levels.get("support"),
            "resistance": levels.get("resistance"),
            "entry_zone": options.get("entry_trigger"),
            "targets": [options.get("target_1"), options.get("target_2")],
            "stop_invalidation": options.get("stop_loss"),
            "risk_score": risk.get("risk_reward_ratio"),
            "bull_case": f"Price can reclaim upside if it holds above support near {levels.get('support')}",
            "bear_case": f"Failure near resistance {levels.get('resistance')} can trigger downside continuation",
            "data_quality": base["data_quality"],
            "trade_thesis": (
                f"{stock['ticker']} shows {candle['candle_type']} behavior with {confidence['confidence']}% confidence. "
                "Use research-only workflow and paper trading validation."
            ),
            "components": base,
        }

    def _candle_brain(self, ticker: str) -> Dict[str, Any]:
        base = self._base_payload(ticker)
        return {
            "ticker": base["ticker"],
            "candle_type": base["candle"]["candle_type"],
            "bias": base["candle"]["bias"],
            "score": base["candle"]["score"],
            "data_quality": base["data_quality"],
        }

    def _pattern_brain(self, ticker: str) -> Dict[str, Any]:
        base = self._base_payload(ticker)
        levels = base["levels"]
        stock = base["stock"]
        return {
            "ticker": base["ticker"],
            "pattern_signal": "Range Compression" if stock.get("day_high") and stock.get("day_low") else "Unavailable",
            "support": levels.get("support"),
            "resistance": levels.get("resistance"),
            "data_quality": base["data_quality"],
        }

    def _trend_brain(self, ticker: str) -> Dict[str, Any]:
        base = self._base_payload(ticker)
        pct = _safe_float(base["stock"].get("daily_change_percent"))
        trend = "Uptrend" if pct > 0 else "Downtrend" if pct < 0 else "Sideways"
        return {
            "ticker": base["ticker"],
            "trend": trend,
            "daily_change_percent": base["stock"].get("daily_change_percent"),
            "data_quality": base["data_quality"],
        }

    def _volume_brain(self, ticker: str) -> Dict[str, Any]:
        base = self._base_payload(ticker)
        rel_vol = base["stock"].get("relative_volume")
        return {
            "ticker": base["ticker"],
            "relative_volume": rel_vol,
            "volume_signal": "Elevated" if _safe_float(rel_vol) >= 1.5 else "Normal",
            "data_quality": base["data_quality"],
        }

    def _support_resistance_brain(self, ticker: str) -> Dict[str, Any]:
        base = self._base_payload(ticker)
        return {
            "ticker": base["ticker"],
            **base["levels"],
            "data_quality": base["data_quality"],
        }

    def _strategy_brain(self, ticker: str) -> Dict[str, Any]:
        base = self._base_payload(ticker)
        return {
            "ticker": base["ticker"],
            "suggested_action": base["confidence"]["suggested_action"],
            "reasons": base["confidence"]["reasons"],
            "data_quality": base["data_quality"],
        }

    def _options_brain(self, ticker: str) -> Dict[str, Any]:
        base = self._base_payload(ticker)
        return {
            "ticker": base["ticker"],
            **base["options"],
            "data_quality": base["data_quality"],
        }

    def _risk_brain(self, ticker: str) -> Dict[str, Any]:
        base = self._base_payload(ticker)
        return {
            "ticker": base["ticker"],
            **base["risk"],
            "data_quality": base["data_quality"],
        }

    def _market_readiness(self, ticker: str) -> Dict[str, Any]:
        base = self._base_payload(ticker)
        confidence_score = _safe_float(base["confidence"].get("confidence"))
        readiness = "Ready" if confidence_score >= 65 else "Watch"
        return {
            "ticker": base["ticker"],
            "readiness": readiness,
            "confidence": confidence_score,
            "data_quality": base["data_quality"],
        }

    def _confidence_engine(self, ticker: str) -> Dict[str, Any]:
        base = self._base_payload(ticker)
        return {
            "ticker": base["ticker"],
            **base["confidence"],
            "data_quality": base["data_quality"],
        }
