from __future__ import annotations

import math
import os
import threading
from datetime import datetime, timezone
import time
from typing import Any, Dict, List, Optional, Tuple

import yfinance as yf

from backend.brains.registry import BrainRegistry
from backend.services.market_data_service import (
    build_api_response,
    get_chart_data,
    get_stock_snapshot,
    normalize_ticker,
    validate_ticker,
)
from backend.services.symbol_universe_service import SymbolUniverseService

OVERVIEW_TTL_SECONDS = int(os.getenv("PLUTO_RESEARCH_CACHE_SECONDS", "45"))
STALE_TTL_SECONDS = int(os.getenv("PLUTO_RESEARCH_STALE_SECONDS", "300"))
REQUEST_TIMEOUT_SECONDS = float(os.getenv("PLUTO_RESEARCH_TIMEOUT_SECONDS", "7"))


_SUPPORTED_EXCHANGES = {"NASDAQ", "NYSE", "NYSE ARCA", "NYSE AMERICAN", "IEX", "BATS", "OTHER"}
_SUPPORTED_ASSET_TYPES = {"stock", "etf"}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_epoch() -> float:
    return datetime.now(timezone.utc).timestamp()


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(num):
        return None
    return num


def _safe_round(value: Optional[float], digits: int = 2) -> Optional[float]:
    if value is None:
        return None
    return round(value, digits)


def _safe_int(value: Any) -> Optional[int]:
    num = _to_float(value)
    if num is None:
        return None
    try:
        return int(num)
    except (TypeError, ValueError):
        return None


def _trend_label(close_values: List[float]) -> str:
    if len(close_values) < 2:
        return "UNAVAILABLE"
    if close_values[-1] > close_values[0]:
        return "UPTREND"
    if close_values[-1] < close_values[0]:
        return "DOWNTREND"
    return "SIDEWAYS"


def _calc_rsi(close_values: List[float], period: int = 14) -> Optional[float]:
    if len(close_values) <= period:
        return None

    gains = []
    losses = []
    for idx in range(1, len(close_values)):
        diff = close_values[idx] - close_values[idx - 1]
        if diff >= 0:
            gains.append(diff)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(abs(diff))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    if avg_loss == 0:
        return 100.0

    for i in range(period, len(gains)):
        avg_gain = ((avg_gain * (period - 1)) + gains[i]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return _safe_round(100 - (100 / (1 + rs)), 2)


def _normalize_status(value: Any) -> str:
    allowed = {"LIVE", "DELAYED", "CACHED", "STALE", "PARTIAL", "UNAVAILABLE"}
    status = str(value or "UNAVAILABLE").upper()
    return status if status in allowed else "UNAVAILABLE"


def _component_meta(provider: str, status: Any, last_updated: Any, cache_age: Any, latency_ms: float) -> Dict[str, Any]:
    norm_status = _normalize_status(status)
    return {
        "provider": provider,
        "status": norm_status,
        "last_updated": last_updated,
        "cache_age": cache_age,
        "latency": round(latency_ms, 2),
        "freshness": norm_status,
    }


def _infer_error_kind(message: str) -> str:
    text = str(message or "").lower()
    if "required" in text or "format" in text or "invalid" in text:
        return "INVALID_TICKER"
    if "not found" in text or "unsupported" in text or "delisted" in text:
        return "UNSUPPORTED_SECURITY"
    if "timeout" in text or "timed out" in text:
        return "TEMPORARY_TIMEOUT"
    if "unavailable" in text or "service" in text or "provider" in text:
        return "PROVIDER_OUTAGE"
    return "UNAVAILABLE"


class ResearchService:
    def __init__(self, universe: SymbolUniverseService, brain_registry: BrainRegistry) -> None:
        self._universe = universe
        self._brains = brain_registry
        self._lock = threading.RLock()
        self._cache: Dict[str, Dict[str, Any]] = {}

    def resolve_symbol(self, raw_symbol: str) -> Dict[str, Any]:
        started = time.perf_counter()
        symbol = normalize_ticker(raw_symbol)
        is_valid, error = validate_ticker(symbol)
        if not is_valid:
            return build_api_response(
                False,
                error=error,
                data={
                    "symbol": symbol,
                    "validation": "invalid_syntax",
                    "error_kind": "INVALID_TICKER",
                    "reliability": _component_meta("PlutoTradeAI", "UNAVAILABLE", utc_now_iso(), None, (time.perf_counter() - started) * 1000),
                },
                data_status="unavailable",
                provider="PlutoTradeAI",
            )

        self._universe.ensure_fresh()
        directory_row = self._universe.resolve(symbol)

        snapshot = get_stock_snapshot(symbol)
        if not snapshot.get("success") and not directory_row:
            reason = snapshot.get("error") or "No provider data"
            return build_api_response(
                False,
                error="Symbol not found or data unavailable",
                data={
                    "symbol": symbol,
                    "validation": "invalid_or_unsupported",
                    "reason": reason,
                    "error_kind": _infer_error_kind(reason),
                    "reliability": _component_meta(
                        snapshot.get("provider", "YFinanceProvider"),
                        snapshot.get("data_status", "UNAVAILABLE"),
                        snapshot.get("timestamp"),
                        None,
                        (time.perf_counter() - started) * 1000,
                    ),
                },
                data_status="unavailable",
                provider="PlutoTradeAI",
            )

        identity = self._build_identity(symbol, directory_row, snapshot)
        availability = {
            "quote": bool(snapshot.get("success")),
            "history": bool(get_chart_data(symbol, timeframe="3M").get("success")),
            "fundamentals": identity.get("market_cap") is not None or identity.get("sector") is not None,
            "news": True,
        }

        status = snapshot.get("data_status", "cached")
        response = build_api_response(
            True,
            data={
                "symbol": symbol,
                "normalized_symbol": symbol,
                "is_supported": identity.get("exchange") in _SUPPORTED_EXCHANGES and identity.get("asset_type") in _SUPPORTED_ASSET_TYPES,
                "identity": identity,
                "availability": availability,
                "provider_map": self.provider_map(),
                "resolved_at": utc_now_iso(),
                "error_kind": None,
                "reliability": _component_meta(
                    snapshot.get("provider", "YFinanceProvider"),
                    status,
                    snapshot.get("timestamp"),
                    snapshot.get("data", {}).get("market_data_age_seconds") if isinstance(snapshot.get("data"), dict) else None,
                    (time.perf_counter() - started) * 1000,
                ),
            },
            data_status=status,
            provider=snapshot.get("provider", "YFinanceProvider"),
        )
        return response

    def quote(self, raw_symbol: str) -> Dict[str, Any]:
        started = time.perf_counter()
        symbol = normalize_ticker(raw_symbol)
        snapshot = get_stock_snapshot(symbol)
        if not snapshot.get("success"):
            message = snapshot.get("error", "Quote unavailable")
            return build_api_response(
                False,
                error=message,
                data={
                    "symbol": symbol,
                    "error_kind": _infer_error_kind(message),
                    "reliability": _component_meta(
                        snapshot.get("provider", "YFinanceProvider"),
                        snapshot.get("data_status", "UNAVAILABLE"),
                        snapshot.get("timestamp"),
                        None,
                        (time.perf_counter() - started) * 1000,
                    ),
                },
                data_status="unavailable",
                provider="YFinanceProvider",
            )

        data = snapshot.get("data", {})
        quote = {
            "symbol": symbol,
            "price": data.get("current_price"),
            "change": data.get("daily_change"),
            "change_percent": data.get("daily_change_percent"),
            "open": data.get("previous_close"),
            "high": data.get("day_high"),
            "low": data.get("day_low"),
            "previous_close": data.get("previous_close"),
            "bid": None,
            "ask": None,
            "volume": data.get("volume"),
            "average_volume": None,
            "relative_volume": data.get("relative_volume"),
            "market_cap": data.get("market_cap"),
            "week_52_high": data.get("week_52_high"),
            "week_52_low": data.get("week_52_low"),
            "market_session": data.get("market_session"),
            "freshness": {
                "status": data.get("market_data_status") or snapshot.get("data_status"),
                "age_seconds": data.get("market_data_age_seconds"),
                "last_updated": data.get("last_updated"),
            },
            "provider": snapshot.get("provider"),
            "reliability": _component_meta(
                snapshot.get("provider", "YFinanceProvider"),
                data.get("market_data_status") or snapshot.get("data_status"),
                data.get("last_updated") or snapshot.get("timestamp"),
                data.get("market_data_age_seconds"),
                (time.perf_counter() - started) * 1000,
            ),
        }

        return build_api_response(True, data=quote, data_status=snapshot.get("data_status", "cached"), provider=snapshot.get("provider", "YFinanceProvider"))

    def history(self, raw_symbol: str, timeframe: str = "3M") -> Dict[str, Any]:
        started = time.perf_counter()
        symbol = normalize_ticker(raw_symbol)
        chart = get_chart_data(symbol, timeframe=timeframe)
        if not chart.get("success"):
            message = chart.get("error", "History unavailable")
            return build_api_response(
                False,
                error=message,
                data={
                    "symbol": symbol,
                    "error_kind": _infer_error_kind(message),
                    "reliability": _component_meta(
                        chart.get("provider", "YFinanceProvider"),
                        chart.get("data_status", "UNAVAILABLE"),
                        chart.get("timestamp"),
                        None,
                        (time.perf_counter() - started) * 1000,
                    ),
                },
                data_status="unavailable",
                provider=chart.get("provider", "YFinanceProvider"),
            )

        data = chart.get("data", {})
        history_payload = {
            "symbol": symbol,
            "timeframe": data.get("timeframe"),
            "candles": data.get("candles", []),
            "volume": data.get("volume", []),
            "freshness": {
                "status": data.get("market_data_status") or chart.get("data_status"),
                "age_seconds": data.get("market_data_age_seconds"),
                "last_updated": data.get("last_updated"),
            },
            "provider": chart.get("provider"),
            "reliability": _component_meta(
                chart.get("provider", "YFinanceProvider"),
                data.get("market_data_status") or chart.get("data_status"),
                data.get("last_updated") or chart.get("timestamp"),
                data.get("market_data_age_seconds"),
                (time.perf_counter() - started) * 1000,
            ),
        }
        return build_api_response(True, data=history_payload, data_status=chart.get("data_status", "cached"), provider=chart.get("provider", "YFinanceProvider"))

    def technicals(self, raw_symbol: str) -> Dict[str, Any]:
        started = time.perf_counter()
        symbol = normalize_ticker(raw_symbol)
        chart = get_chart_data(symbol, timeframe="3M")
        snapshot = get_stock_snapshot(symbol)

        if not chart.get("success") and not snapshot.get("success"):
            message = "Technicals unavailable"
            return build_api_response(
                False,
                error=message,
                data={
                    "symbol": symbol,
                    "error_kind": _infer_error_kind(message),
                    "reliability": _component_meta(
                        "PlutoTradeAI",
                        "UNAVAILABLE",
                        utc_now_iso(),
                        None,
                        (time.perf_counter() - started) * 1000,
                    ),
                },
                data_status="unavailable",
                provider="PlutoTradeAI",
            )

        candles = chart.get("data", {}).get("candles", [])
        closes = [float(item["close"]) for item in candles if item.get("close") is not None]

        indicators = chart.get("data", {}).get("indicators", {})
        latest = closes[-1] if closes else None
        ma20 = indicators.get("ema20")
        trend = _trend_label(closes[-20:] if len(closes) >= 20 else closes)

        momentum = None
        if len(closes) >= 6:
            prior = closes[-6]
            if prior not in (None, 0):
                momentum = _safe_round(((closes[-1] - prior) / prior) * 100, 2)

        volatility = None
        if len(closes) >= 10:
            returns = []
            for idx in range(1, len(closes)):
                prev = closes[idx - 1]
                curr = closes[idx]
                if prev in (None, 0):
                    continue
                returns.append((curr - prev) / prev)
            if returns:
                mean = sum(returns) / len(returns)
                variance = sum((item - mean) ** 2 for item in returns) / len(returns)
                volatility = _safe_round((variance ** 0.5) * 100, 2)

        breakout_status = "UNAVAILABLE"
        if latest is not None and indicators.get("breakout_level") is not None:
            breakout_status = "BREAKOUT" if latest >= indicators["breakout_level"] else "BELOW_BREAKOUT"

        gap_status = "UNAVAILABLE"
        snapshot_data = snapshot.get("data", {}) if snapshot.get("success") else {}
        previous_close = snapshot_data.get("previous_close")
        open_price = snapshot_data.get("previous_close")
        if previous_close not in (None, 0) and open_price is not None:
            gap_pct = ((open_price - previous_close) / previous_close) * 100
            if gap_pct >= 1:
                gap_status = "GAP_UP"
            elif gap_pct <= -1:
                gap_status = "GAP_DOWN"
            else:
                gap_status = "NO_GAP"

        payload = {
            "symbol": symbol,
            "trend": trend,
            "momentum": momentum,
            "volatility": volatility,
            "moving_averages": {
                "ema9": indicators.get("ema9"),
                "ema20": ma20,
                "ema50": indicators.get("ema50"),
                "ema200": indicators.get("ema200"),
                "vwap": indicators.get("vwap"),
            },
            "rsi": _calc_rsi(closes[-60:]) if closes else None,
            "support_levels": {
                "major_support": indicators.get("major_support"),
                "reversal_zone_low": indicators.get("reversal_zone_low"),
            },
            "resistance_levels": {
                "major_resistance": indicators.get("major_resistance"),
                "reversal_zone_high": indicators.get("reversal_zone_high"),
            },
            "breakout_status": breakout_status,
            "gap_status": gap_status,
            "volume_confirmation": snapshot_data.get("relative_volume"),
            "candle_pattern_observations": [
                "Elevated volume" if _to_float(snapshot_data.get("relative_volume")) and _to_float(snapshot_data.get("relative_volume")) >= 1.5 else "Volume normal",
                f"Trend: {trend}",
            ],
            "freshness": {
                "status": chart.get("data", {}).get("market_data_status") or chart.get("data_status"),
                "age_seconds": chart.get("data", {}).get("market_data_age_seconds"),
                "last_updated": chart.get("data", {}).get("last_updated"),
            },
            "provider": chart.get("provider", "YFinanceProvider"),
            "reliability": _component_meta(
                chart.get("provider", "YFinanceProvider"),
                chart.get("data", {}).get("market_data_status") or chart.get("data_status"),
                chart.get("data", {}).get("last_updated") or chart.get("timestamp"),
                chart.get("data", {}).get("market_data_age_seconds"),
                (time.perf_counter() - started) * 1000,
            ),
        }

        return build_api_response(True, data=payload, data_status=chart.get("data_status", "cached"), provider=chart.get("provider", "YFinanceProvider"))

    def analysis(self, raw_symbol: str) -> Dict[str, Any]:
        started = time.perf_counter()
        symbol = normalize_ticker(raw_symbol)
        overview = self._brains.run_overview(symbol)
        trend = self._brains.run("trend", symbol)
        risk = self._brains.run("risk", symbol)

        if not overview.get("success"):
            message = overview.get("error", "Analysis unavailable")
            return build_api_response(
                False,
                error=message,
                data={
                    "symbol": symbol,
                    "error_kind": _infer_error_kind(message),
                    "reliability": _component_meta("PlutoTradeAI", "UNAVAILABLE", utc_now_iso(), None, (time.perf_counter() - started) * 1000),
                },
                data_status="unavailable",
                provider="PlutoTradeAI",
            )

        d = overview.get("data", {})
        technical_trend = trend.get("data", {}).get("trend") if trend.get("success") else None
        risk_data = risk.get("data", {}) if risk.get("success") else {}

        target_1, target_2 = None, None
        targets = d.get("targets")
        if isinstance(targets, list) and targets:
            target_1 = targets[0]
            target_2 = targets[1] if len(targets) > 1 else None

        payload = {
            "symbol": symbol,
            "confidence_score": d.get("confidence"),
            "bullish_thesis": d.get("bull_case"),
            "bearish_thesis": d.get("bear_case"),
            "entry_zone": d.get("entry_zone"),
            "invalidation": d.get("stop_invalidation"),
            "stop_loss": d.get("stop_invalidation"),
            "target_one": target_1,
            "target_two": target_2,
            "risk_reward_ratio": d.get("risk_score"),
            "warning_flags": d.get("components", {}).get("confidence", {}).get("reasons", []),
            "data_freshness": d.get("data_quality"),
            "analysis_timestamp": overview.get("timestamp"),
            "trend": technical_trend,
            "risk_status": risk_data.get("risk_status"),
            "provider": "PlutoTradeAI",
            "reliability": _component_meta(
                "PlutoTradeAI",
                d.get("data_quality") or "CACHED",
                overview.get("timestamp") or utc_now_iso(),
                None,
                (time.perf_counter() - started) * 1000,
            ),
        }

        return build_api_response(True, data=payload, data_status="cached", provider="PlutoTradeAI")

    def news(self, raw_symbol: str) -> Dict[str, Any]:
        started = time.perf_counter()
        symbol = normalize_ticker(raw_symbol)

        try:
            ticker = yf.Ticker(symbol)
            raw_news = ticker.news or []
        except Exception:
            raw_news = []

        rows = []
        for item in raw_news[:10]:
            if not isinstance(item, dict):
                continue
            publish_time = item.get("providerPublishTime")
            published_iso = None
            if publish_time:
                try:
                    published_iso = datetime.fromtimestamp(int(publish_time), tz=timezone.utc).isoformat()
                except Exception:
                    published_iso = None

            rows.append(
                {
                    "title": item.get("title"),
                    "publisher": item.get("publisher"),
                    "link": item.get("link"),
                    "published_at": published_iso,
                    "type": item.get("type"),
                }
            )

        earnings_date = None
        try:
            cal = yf.Ticker(symbol).calendar
            if hasattr(cal, "empty") and not cal.empty:
                if "Earnings Date" in cal.index:
                    earnings_date = str(cal.loc["Earnings Date"].iloc[0])
        except Exception:
            earnings_date = None

        payload = {
            "symbol": symbol,
            "news": rows,
            "earnings_date": earnings_date,
            "unusual_movement_explanation": None,
            "sector_comparison": None,
            "provider": "YFinanceNews",
            "freshness": {
                "status": "CACHED" if rows else "PARTIAL",
                "last_updated": utc_now_iso(),
            },
            "status_note": "Recent news loaded successfully." if rows else "News unavailable. Quote provider healthy. Technical analysis completed.",
            "reliability": _component_meta("YFinanceNews", "CACHED" if rows else "PARTIAL", utc_now_iso(), None, (time.perf_counter() - started) * 1000),
        }

        return build_api_response(True, data=payload, data_status="cached" if rows else "partial", provider="YFinanceNews")

    def overview(self, raw_symbol: str) -> Dict[str, Any]:
        symbol = normalize_ticker(raw_symbol)

        cache_key = f"overview:{symbol}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        resolve = self.resolve_symbol(symbol)
        if not resolve.get("success"):
            return resolve

        quote = self.quote(symbol)
        technicals = self.technicals(symbol)
        analysis = self.analysis(symbol)
        news = self.news(symbol)

        identity = resolve.get("data", {}).get("identity", {})
        payload = {
            "symbol": symbol,
            "identity": identity,
            "market_data": quote.get("data", {}) if quote.get("success") else {},
            "technicals": technicals.get("data", {}) if technicals.get("success") else {},
            "plutotrade_analysis": analysis.get("data", {}) if analysis.get("success") else {},
            "context": news.get("data", {}) if news.get("success") else {},
            "providers": self.provider_map(),
            "component_reliability": {
                "resolve": resolve.get("data", {}).get("reliability"),
                "quote": quote.get("data", {}).get("reliability"),
                "technicals": technicals.get("data", {}).get("reliability"),
                "analysis": analysis.get("data", {}).get("reliability"),
                "news": news.get("data", {}).get("reliability"),
            },
            "availability": {
                "identity": bool(identity),
                "quote": quote.get("success", False),
                "technicals": technicals.get("success", False),
                "analysis": analysis.get("success", False),
                "news": news.get("success", False),
            },
            "last_updated": utc_now_iso(),
        }

        data_status = "cached"
        if not quote.get("success") and technicals.get("success"):
            data_status = "partial"
        elif not quote.get("success") and not technicals.get("success"):
            data_status = "unavailable"

        response = build_api_response(True, data=payload, data_status=data_status, provider="PlutoTradeAI")
        self._cache_set(cache_key, response)
        return response

    def provider_map(self) -> Dict[str, str]:
        return {
            "symbol_directory_provider": "NasdaqTraderDirectory",
            "quote_provider": "YFinanceProvider",
            "history_provider": "YFinanceProvider",
            "fundamentals_provider": "YFinanceProvider",
            "news_provider": "YFinanceNews",
            "scanner_engine": "PlutoScannerEngine",
            "chart_provider": os.getenv("PLUTO_CHART_PROVIDER", "tradingview").upper(),
            "signal_provider": os.getenv("PLUTO_SIGNAL_PROVIDER", "tradingview").upper(),
        }

    def _build_identity(self, symbol: str, directory_row: Optional[Dict[str, Any]], snapshot: Dict[str, Any]) -> Dict[str, Any]:
        info = self._fetch_fundamentals(symbol)

        exchange = None
        name = None
        asset_type = None
        if directory_row:
            exchange = directory_row.get("exchange")
            name = directory_row.get("name")
            asset_type = directory_row.get("asset_type")

        exchange = exchange or info.get("exchange")
        name = name or info.get("longName") or info.get("shortName") or symbol
        asset_type = asset_type or ("etf" if str(info.get("quoteType", "")).upper() == "ETF" else "stock")

        identity = {
            "symbol": symbol,
            "name": name,
            "exchange": exchange,
            "asset_type": str(asset_type).lower(),
            "sector": info.get("sector") or None,
            "industry": info.get("industry") or None,
            "market_cap": _safe_int(info.get("marketCap")),
        }

        if snapshot.get("success"):
            data = snapshot.get("data", {})
            if identity["market_cap"] is None:
                identity["market_cap"] = _safe_int(data.get("market_cap"))

        return identity

    def _fetch_fundamentals(self, symbol: str) -> Dict[str, Any]:
        try:
            ticker = yf.Ticker(symbol)
            info = ticker.info or {}
            return info if isinstance(info, dict) else {}
        except Exception:
            return {}

    def _cache_set(self, key: str, response: Dict[str, Any]) -> None:
        with self._lock:
            self._cache[key] = {
                "created_at": _now_epoch(),
                "response": response,
            }

    def _cache_get(self, key: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            entry = self._cache.get(key)
            if not entry:
                return None

            age = _now_epoch() - entry.get("created_at", 0)
            if age <= OVERVIEW_TTL_SECONDS:
                return entry.get("response")
            if age <= STALE_TTL_SECONDS:
                stale = dict(entry.get("response") or {})
                stale["data_status"] = "stale"
                stale.setdefault("data", {})
                if isinstance(stale["data"], dict):
                    stale["data"]["stale_age_seconds"] = int(age)
                return stale

            return None
