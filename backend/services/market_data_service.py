from __future__ import annotations

import math
import os
import re
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Protocol, Tuple

import pandas as pd
import yfinance as yf

DEFAULT_PROVIDER = "YFinanceProvider"
DEFAULT_DATA_STATUS = "DELAYED"
DEFAULT_MARKET_SESSION = "regular"
TICKER_PATTERN = re.compile(r"^[A-Z0-9.\-\^=]{1,12}$")

_SNAPSHOT_CACHE: Dict[str, Dict[str, Any]] = {}
_CHART_CACHE: Dict[str, Dict[str, Any]] = {}
_CACHE_TTL_SECONDS = int(os.getenv("PLUTO_MARKET_CACHE_SECONDS", "30"))
_STALE_TTL_SECONDS = int(os.getenv("PLUTO_MARKET_STALE_SECONDS", "300"))

_TIMEFRAME_MAP = {
    "1D": ("1d", "5m"),
    "5D": ("5d", "15m"),
    "1M": ("1mo", "1h"),
    "3M": ("3mo", "1d"),
    "6M": ("6mo", "1d"),
    "1Y": ("1y", "1d"),
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_epoch() -> float:
    return datetime.now(timezone.utc).timestamp()


def _iso_age_seconds(timestamp_iso: Optional[str]) -> Optional[int]:
    if not timestamp_iso:
        return None
    try:
        parsed = datetime.fromisoformat(timestamp_iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    age = _now_epoch() - parsed.timestamp()
    return max(int(age), 0)


def _to_number(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(numeric):
        return None
    return numeric


def _safe_round(value: Optional[float], digits: int = 2) -> Optional[float]:
    if value is None:
        return None
    return round(value, digits)


def _series_to_float(series: pd.Series, index: int = -1) -> Optional[float]:
    if series.empty:
        return None
    return _to_number(series.iloc[index])


def normalize_ticker(raw_ticker: str) -> str:
    return (raw_ticker or "").strip().upper()


def validate_ticker(ticker: str) -> Tuple[bool, Optional[str]]:
    if not ticker:
        return False, "Ticker is required"
    if not TICKER_PATTERN.match(ticker):
        return False, "Ticker format is invalid"
    return True, None


def detect_asset_type(ticker: str) -> str:
    upper = normalize_ticker(ticker)
    if upper.endswith("=F") or upper in {"ES", "NQ", "YM", "RTY", "CL", "GC"}:
        return "Futures"
    if upper.startswith("^"):
        return "Index"
    if upper.endswith("-USD"):
        return "Crypto (Future)"
    if upper in {"SPY", "QQQ", "IWM", "DIA", "XLF", "XLE", "XLK", "XLI", "XLV", "XLB", "XLY", "XLP", "XLU", "XLRE"}:
        return "ETF"
    return "Stock"


def _normalize_status(value: str) -> str:
    allowed = {"LIVE", "STREAMING", "DELAYED", "CACHED", "STALE", "DEMO", "UNAVAILABLE"}
    upper = (value or "UNAVAILABLE").upper()
    return upper if upper in allowed else "UNAVAILABLE"


def _provider_name(raw: str) -> str:
    return raw or DEFAULT_PROVIDER


def _build_market_object(
    ticker: str,
    price: Optional[float],
    provider: str,
    timestamp_iso: Optional[str],
    market_session: Optional[str],
    status: str,
) -> Dict[str, Any]:
    normalized_status = _normalize_status(status)
    ts = timestamp_iso or utc_now_iso()
    return {
        "ticker": normalize_ticker(ticker),
        "price": _safe_round(price),
        "provider": _provider_name(provider),
        "timestamp": ts,
        "market_session": market_session or DEFAULT_MARKET_SESSION,
        "age_seconds": _iso_age_seconds(ts),
        "status": normalized_status,
    }


def _update_response_status(payload: Dict[str, Any], status: str, age_seconds: Optional[int] = None) -> Dict[str, Any]:
    normalized_status = _normalize_status(status)
    updated = deepcopy(payload)
    updated["data_status"] = normalized_status.lower()

    data = updated.get("data")
    if isinstance(data, dict):
        data["market_data_status"] = normalized_status
        if age_seconds is not None:
            data["market_data_age_seconds"] = age_seconds
        market_object = data.get("market_object")
        if isinstance(market_object, dict):
            market_object["status"] = normalized_status
            if age_seconds is not None:
                market_object["age_seconds"] = age_seconds
            if status in {"CACHED", "STALE"}:
                market_object["timestamp"] = data.get("last_updated") or market_object.get("timestamp")

    return updated


def _cache_set(cache: Dict[str, Dict[str, Any]], key: str, value: Dict[str, Any]) -> None:
    cache[key] = {
        "created_at": _now_epoch(),
        "cached_at_iso": utc_now_iso(),
        "value": deepcopy(value),
    }


def _cache_get(cache: Dict[str, Dict[str, Any]], key: str) -> Optional[Dict[str, Any]]:
    entry = cache.get(key)
    if not entry:
        return None

    age = _now_epoch() - entry.get("created_at", 0)
    if age > _CACHE_TTL_SECONDS:
        return None

    return _update_response_status(entry.get("value", {}), "CACHED", int(age))


def _cache_get_stale(cache: Dict[str, Dict[str, Any]], key: str) -> Optional[Dict[str, Any]]:
    entry = cache.get(key)
    if not entry:
        return None

    age = _now_epoch() - entry.get("created_at", 0)
    if age > _STALE_TTL_SECONDS:
        return None

    return _update_response_status(entry.get("value", {}), "STALE", int(age))


def build_api_response(
    success: bool,
    data: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
    data_status: str = "UNAVAILABLE",
    provider: str = DEFAULT_PROVIDER,
) -> Dict[str, Any]:
    return {
        "success": success,
        "data": data if data is not None else {},
        "error": error,
        "timestamp": utc_now_iso(),
        "data_status": _normalize_status(data_status).lower(),
        "provider": _provider_name(provider),
    }


class MarketDataProvider(Protocol):
    name: str

    def fetch_history(self, ticker: str, period: str, interval: str) -> pd.DataFrame:
        ...

    def fetch_fast_info(self, ticker: str) -> Dict[str, Any]:
        ...

    def default_status(self) -> str:
        ...


class ResearchMarketDataProvider:
    name = "YFinanceProvider"

    def fetch_history(self, ticker: str, period: str, interval: str) -> pd.DataFrame:
        history = yf.download(
            ticker,
            period=period,
            interval=interval,
            progress=False,
            auto_adjust=True,
            prepost=True,
            threads=False,
        )

        if history is None or history.empty:
            return pd.DataFrame()

        if isinstance(history.columns, pd.MultiIndex):
            if ticker in history.columns.get_level_values(-1):
                history = history.xs(ticker, axis=1, level=-1)
            else:
                history.columns = [c[0] if isinstance(c, tuple) else c for c in history.columns]

        return history.dropna(subset=["Open", "High", "Low", "Close"])

    def fetch_fast_info(self, ticker: str) -> Dict[str, Any]:
        try:
            ticker_obj = yf.Ticker(ticker)
            raw = ticker_obj.fast_info if hasattr(ticker_obj, "fast_info") else {}
            return dict(raw) if raw else {}
        except Exception:
            return {}

    def default_status(self) -> str:
        return "DELAYED"


class StreamingMarketDataProvider:
    name = "StreamingProvider"

    def fetch_history(self, ticker: str, period: str, interval: str) -> pd.DataFrame:
        # Streaming provider integration is intentionally deferred to a future sprint.
        raise NotImplementedError("Streaming provider is not configured yet")

    def fetch_fast_info(self, ticker: str) -> Dict[str, Any]:
        return {}

    def default_status(self) -> str:
        return "STREAMING"


_PROVIDER_REGISTRY: Dict[str, MarketDataProvider] = {
    "yfinance": ResearchMarketDataProvider(),
    "research": ResearchMarketDataProvider(),
    "streaming": StreamingMarketDataProvider(),
}


def _active_provider() -> MarketDataProvider:
    key = os.getenv("PLUTO_MARKET_PROVIDER", "yfinance").strip().lower()
    provider = _PROVIDER_REGISTRY.get(key)
    if provider is None:
        return _PROVIDER_REGISTRY["yfinance"]
    if key == "streaming":
        # Keep runtime stable while WebSocket ingestion is not wired yet.
        return _PROVIDER_REGISTRY["yfinance"]
    return provider


def _calc_rvol(history: pd.DataFrame) -> Optional[float]:
    if history.empty or "Volume" not in history:
        return None

    recent = history["Volume"].tail(30)
    if recent.empty:
        return None

    latest = _to_number(recent.iloc[-1])
    mean_volume = _to_number(recent.mean())
    if latest is None or mean_volume in (None, 0):
        return None

    return latest / mean_volume


def _calc_emas(history: pd.DataFrame) -> Dict[str, Optional[float]]:
    if history.empty:
        return {"ema9": None, "ema20": None, "ema50": None, "ema200": None}

    close = history["Close"]
    return {
        "ema9": _safe_round(_to_number(close.ewm(span=9, adjust=False).mean().iloc[-1])),
        "ema20": _safe_round(_to_number(close.ewm(span=20, adjust=False).mean().iloc[-1])),
        "ema50": _safe_round(_to_number(close.ewm(span=50, adjust=False).mean().iloc[-1])),
        "ema200": _safe_round(_to_number(close.ewm(span=200, adjust=False).mean().iloc[-1])),
    }


def _line_series(values: pd.Series) -> List[Dict[str, Any]]:
    output: List[Dict[str, Any]] = []
    for idx, value in values.items():
        numeric = _to_number(value)
        if numeric is None:
            continue
        output.append({"time": int(idx.timestamp()), "value": _safe_round(numeric, 4)})
    return output


def _calc_ema_series(history: pd.DataFrame) -> Dict[str, List[Dict[str, Any]]]:
    if history.empty:
        return {"ema9_series": [], "ema20_series": [], "ema50_series": [], "ema200_series": []}

    close = history["Close"]
    return {
        "ema9_series": _line_series(close.ewm(span=9, adjust=False).mean()),
        "ema20_series": _line_series(close.ewm(span=20, adjust=False).mean()),
        "ema50_series": _line_series(close.ewm(span=50, adjust=False).mean()),
        "ema200_series": _line_series(close.ewm(span=200, adjust=False).mean()),
    }


def _calc_vwap(history: pd.DataFrame) -> Optional[float]:
    if history.empty or "Volume" not in history:
        return None

    typical_price = (history["High"] + history["Low"] + history["Close"]) / 3.0
    volume = history["Volume"]
    denominator = _to_number(volume.sum())
    if denominator in (None, 0):
        return None

    numerator = _to_number((typical_price * volume).sum())
    if numerator is None:
        return None

    return _safe_round(numerator / denominator)


def _calc_vwap_series(history: pd.DataFrame) -> List[Dict[str, Any]]:
    if history.empty or "Volume" not in history:
        return []

    typical_price = (history["High"] + history["Low"] + history["Close"]) / 3.0
    volume = history["Volume"]

    cumulative_pv = (typical_price * volume).cumsum()
    cumulative_volume = volume.cumsum()
    vwap = cumulative_pv / cumulative_volume

    return _line_series(vwap)


def _calc_key_levels(history: pd.DataFrame) -> Dict[str, Optional[float]]:
    if history.empty:
        return {
            "major_support": None,
            "major_resistance": None,
            "breakout_level": None,
            "breakdown_level": None,
            "reversal_zone_low": None,
            "reversal_zone_high": None,
            "premarket_high": None,
            "premarket_low": None,
            "after_hours_high": None,
            "after_hours_low": None,
        }

    last_40 = history.tail(40)
    last_20 = history.tail(20)
    last_10 = history.tail(10)

    major_support = _to_number(last_40["Low"].min()) if not last_40.empty else None
    major_resistance = _to_number(last_40["High"].max()) if not last_40.empty else None
    breakout_level = _to_number(last_20["High"].max()) if not last_20.empty else None
    breakdown_level = _to_number(last_20["Low"].min()) if not last_20.empty else None
    reversal_zone_low = _to_number(last_10["Low"].quantile(0.2)) if not last_10.empty else None
    reversal_zone_high = _to_number(last_10["High"].quantile(0.8)) if not last_10.empty else None

    intraday = history.copy()
    if hasattr(intraday.index, "tz") and intraday.index.tz is not None:
        intraday = intraday.tz_convert("US/Eastern")

    pre = intraday.between_time("04:00", "09:29") if not intraday.empty else pd.DataFrame()
    post = intraday.between_time("16:01", "20:00") if not intraday.empty else pd.DataFrame()

    premarket_high = _to_number(pre["High"].max()) if not pre.empty else None
    premarket_low = _to_number(pre["Low"].min()) if not pre.empty else None
    after_hours_high = _to_number(post["High"].max()) if not post.empty else None
    after_hours_low = _to_number(post["Low"].min()) if not post.empty else None

    return {
        "major_support": _safe_round(major_support),
        "major_resistance": _safe_round(major_resistance),
        "breakout_level": _safe_round(breakout_level),
        "breakdown_level": _safe_round(breakdown_level),
        "reversal_zone_low": _safe_round(reversal_zone_low),
        "reversal_zone_high": _safe_round(reversal_zone_high),
        "premarket_high": _safe_round(premarket_high),
        "premarket_low": _safe_round(premarket_low),
        "after_hours_high": _safe_round(after_hours_high),
        "after_hours_low": _safe_round(after_hours_low),
    }


def get_stock_snapshot(ticker: str) -> Dict[str, Any]:
    normalized = normalize_ticker(ticker)
    cached = _cache_get(_SNAPSHOT_CACHE, normalized)
    if cached is not None:
        return cached

    is_valid, error = validate_ticker(normalized)
    if not is_valid:
        return build_api_response(False, error=error, data_status="UNAVAILABLE")

    provider = _active_provider()

    try:
        period, interval = _TIMEFRAME_MAP["1M"]
        history = provider.fetch_history(normalized, period, interval)
    except Exception:
        history = pd.DataFrame()

    if history.empty:
        stale = _cache_get_stale(_SNAPSHOT_CACHE, normalized)
        if stale is not None:
            return stale
        return build_api_response(
            False,
            error="Data unavailable",
            data_status="UNAVAILABLE",
            provider=provider.name,
        )

    latest_close = _series_to_float(history["Close"], -1)
    previous_close_bar = _series_to_float(history["Close"], -2)
    day_high = _to_number(history["High"].tail(1).max())
    day_low = _to_number(history["Low"].tail(1).min())
    latest_volume = _series_to_float(history["Volume"], -1) if "Volume" in history else None

    daily_change = None
    daily_change_percent = None
    if latest_close is not None and previous_close_bar not in (None, 0):
        daily_change = latest_close - previous_close_bar
        daily_change_percent = (daily_change / previous_close_bar) * 100

    annual = provider.fetch_history(normalized, "1y", "1d")
    year_high = _to_number(annual["High"].max()) if not annual.empty else None
    year_low = _to_number(annual["Low"].min()) if not annual.empty else None

    fast_info = provider.fetch_fast_info(normalized)
    pre_market = _to_number(fast_info.get("preMarketPrice"))
    post_market = _to_number(fast_info.get("postMarketPrice"))
    regular_market = _to_number(fast_info.get("lastPrice")) or latest_close
    market_cap = _to_number(fast_info.get("marketCap"))

    status = provider.default_status()
    market_object = _build_market_object(
        ticker=normalized,
        price=regular_market,
        provider=provider.name,
        timestamp_iso=utc_now_iso(),
        market_session=DEFAULT_MARKET_SESSION,
        status=status,
    )

    data = {
        "ticker": normalized,
        "company": normalized,
        "asset_type": detect_asset_type(normalized),
        "current_price": _safe_round(regular_market),
        "daily_change": _safe_round(daily_change),
        "daily_change_percent": _safe_round(daily_change_percent),
        "session_status": DEFAULT_MARKET_SESSION,
        "market_session": DEFAULT_MARKET_SESSION,
        "premarket_price": _safe_round(pre_market),
        "after_hours_price": _safe_round(post_market),
        "volume": int(latest_volume) if latest_volume is not None else None,
        "relative_volume": _safe_round(_calc_rvol(history)),
        "market_cap": int(market_cap) if market_cap is not None else None,
        "previous_close": _safe_round(previous_close_bar),
        "day_high": _safe_round(day_high),
        "day_low": _safe_round(day_low),
        "week_52_high": _safe_round(year_high),
        "week_52_low": _safe_round(year_low),
        "data_source": provider.name,
        "last_updated": market_object["timestamp"],
        "market_data_status": market_object["status"],
        "market_data_age_seconds": market_object["age_seconds"],
        "market_object": market_object,
    }

    response = build_api_response(
        True,
        data=data,
        data_status=status,
        provider=provider.name,
    )
    _cache_set(_SNAPSHOT_CACHE, normalized, response)
    return response


def get_chart_data(ticker: str, timeframe: str = "1M") -> Dict[str, Any]:
    normalized = normalize_ticker(ticker)
    frame = (timeframe or "1M").upper()
    cache_key = f"{normalized}:{frame}"

    cached = _cache_get(_CHART_CACHE, cache_key)
    if cached is not None:
        return cached

    is_valid, error = validate_ticker(normalized)
    if not is_valid:
        return build_api_response(False, error=error, data_status="UNAVAILABLE")

    if frame not in _TIMEFRAME_MAP:
        return build_api_response(False, error="Unsupported timeframe", data_status="UNAVAILABLE")

    provider = _active_provider()

    try:
        period, interval = _TIMEFRAME_MAP[frame]
        history = provider.fetch_history(normalized, period, interval)
    except Exception:
        history = pd.DataFrame()

    if history.empty:
        stale = _cache_get_stale(_CHART_CACHE, cache_key)
        if stale is not None:
            return stale
        return build_api_response(
            False,
            error="Data unavailable",
            data_status="UNAVAILABLE",
            provider=provider.name,
        )

    if history.index.tz is None:
        history = history.tz_localize("UTC")

    candles: List[Dict[str, Any]] = []
    volume: List[Dict[str, Any]] = []

    for idx, row in history.iterrows():
        ts = int(idx.timestamp())
        open_value = _to_number(row.get("Open"))
        close_value = _to_number(row.get("Close"))

        candles.append(
            {
                "time": ts,
                "open": open_value,
                "high": _to_number(row.get("High")),
                "low": _to_number(row.get("Low")),
                "close": close_value,
            }
        )

        color = "#23d18b"
        if open_value is not None and close_value is not None and close_value < open_value:
            color = "#e34f4f"

        volume.append({"time": ts, "value": _to_number(row.get("Volume")), "color": color})

    indicators = {
        **_calc_emas(history),
        **_calc_ema_series(history),
        "vwap": _calc_vwap(history),
        "vwap_series": _calc_vwap_series(history),
        **_calc_key_levels(history),
    }

    latest_price = candles[-1]["close"] if candles else None
    market_object = _build_market_object(
        ticker=normalized,
        price=latest_price,
        provider=provider.name,
        timestamp_iso=utc_now_iso(),
        market_session=DEFAULT_MARKET_SESSION,
        status=provider.default_status(),
    )

    data = {
        "ticker": normalized,
        "timeframe": frame,
        "market_session": DEFAULT_MARKET_SESSION,
        "candles": candles,
        "volume": volume,
        "indicators": indicators,
        "last_updated": market_object["timestamp"],
        "market_data_status": market_object["status"],
        "market_data_age_seconds": market_object["age_seconds"],
        "market_object": market_object,
    }

    response = build_api_response(
        True,
        data=data,
        data_status=provider.default_status(),
        provider=provider.name,
    )
    _cache_set(_CHART_CACHE, cache_key, response)
    return response
