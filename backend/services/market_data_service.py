from __future__ import annotations

import math
import re
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import yfinance as yf

DEFAULT_PROVIDER = "Yahoo Finance"
DEFAULT_DATA_STATUS = "delayed"
TICKER_PATTERN = re.compile(r"^[A-Z0-9.\-\^=]{1,12}$")
_SNAPSHOT_CACHE: Dict[str, Dict[str, Any]] = {}
_CHART_CACHE: Dict[str, Dict[str, Any]] = {}
_CACHE_TTL_SECONDS = 25

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


def _cache_get(cache: Dict[str, Dict[str, Any]], key: str) -> Optional[Dict[str, Any]]:
    entry = cache.get(key)
    if not entry:
        return None

    created_at = entry.get("created_at", 0)
    if (_now_epoch() - created_at) > _CACHE_TTL_SECONDS:
        cache.pop(key, None)
        return None

    cached_value = deepcopy(entry.get("value"))
    if not cached_value:
        return None

    cached_at = entry.get("cached_at_iso")
    if cached_at:
        cached_value["data_status"] = "cached"
        if isinstance(cached_value.get("data"), dict):
            cached_value["data"]["cache_timestamp"] = cached_at
            cached_value["data"]["market_data_status"] = "cached"
            cached_value["data"]["last_updated"] = cached_at

    return cached_value


def _cache_set(cache: Dict[str, Dict[str, Any]], key: str, value: Dict[str, Any]) -> None:
    cache[key] = {"created_at": _now_epoch(), "cached_at_iso": utc_now_iso(), "value": value}


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
    if upper in {"SPY", "QQQ", "IWM", "DIA", "XLF", "XLE"}:
        return "ETF"
    return "Stock"


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


def _safe_round(value: Optional[float], digits: int = 2) -> Optional[float]:
    if value is None:
        return None
    return round(value, digits)


def _series_to_float(series: pd.Series, index: int = -1) -> Optional[float]:
    if series.empty:
        return None

    value = series.iloc[index]
    return _to_number(value)


def build_api_response(
    success: bool,
    data: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
    data_status: str = "unavailable",
    provider: str = DEFAULT_PROVIDER,
) -> Dict[str, Any]:
    return {
        "success": success,
        "data": data if data is not None else {},
        "error": error,
        "timestamp": utc_now_iso(),
        "data_status": data_status,
        "provider": provider,
    }


def _fetch_history(ticker: str, period: str, interval: str) -> pd.DataFrame:
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
            flattened = []
            for column in history.columns:
                if isinstance(column, tuple):
                    flattened.append(column[0])
                else:
                    flattened.append(column)
            history.columns = flattened

    history = history.dropna(subset=["Open", "High", "Low", "Close"])
    return history


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


def _line_series(history: pd.DataFrame, values: pd.Series) -> List[Dict[str, Any]]:
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
        "ema9_series": _line_series(history, close.ewm(span=9, adjust=False).mean()),
        "ema20_series": _line_series(history, close.ewm(span=20, adjust=False).mean()),
        "ema50_series": _line_series(history, close.ewm(span=50, adjust=False).mean()),
        "ema200_series": _line_series(history, close.ewm(span=200, adjust=False).mean()),
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

    with pd.option_context("mode.use_inf_as_na", True):
        vwap = cumulative_pv / cumulative_volume

    return _line_series(history, vwap)


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
    premarket_high = _to_number(pre["High"].max()) if not pre.empty else None
    premarket_low = _to_number(pre["Low"].min()) if not pre.empty else None

    return {
        "major_support": _safe_round(major_support),
        "major_resistance": _safe_round(major_resistance),
        "breakout_level": _safe_round(breakout_level),
        "breakdown_level": _safe_round(breakdown_level),
        "reversal_zone_low": _safe_round(reversal_zone_low),
        "reversal_zone_high": _safe_round(reversal_zone_high),
        "premarket_high": _safe_round(premarket_high),
        "premarket_low": _safe_round(premarket_low),
    }


def get_stock_snapshot(ticker: str) -> Dict[str, Any]:
    normalized = normalize_ticker(ticker)
    cache_key = normalized
    cached_snapshot = _cache_get(_SNAPSHOT_CACHE, cache_key)
    if cached_snapshot is not None:
        return cached_snapshot

    is_valid, error = validate_ticker(normalized)
    if not is_valid:
        return build_api_response(False, error=error, data_status="unavailable")

    period, interval = _TIMEFRAME_MAP["1M"]
    history = _fetch_history(normalized, period, interval)

    if history.empty:
        return build_api_response(
            False,
            error="Market data unavailable for this ticker",
            data_status="unavailable",
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

    annual = _fetch_history(normalized, "1y", "1d")
    year_high = _to_number(annual["High"].max()) if not annual.empty else None
    year_low = _to_number(annual["Low"].min()) if not annual.empty else None

    ticker_obj = yf.Ticker(normalized)
    fast_info = ticker_obj.fast_info if hasattr(ticker_obj, "fast_info") else {}
    info = dict(fast_info) if fast_info else {}

    pre_market = _to_number(info.get("preMarketPrice"))
    post_market = _to_number(info.get("postMarketPrice"))
    regular_market = _to_number(info.get("lastPrice")) or latest_close
    market_cap = _to_number(info.get("marketCap"))

    data = {
        "ticker": normalized,
        "company": normalized,
        "asset_type": detect_asset_type(normalized),
        "current_price": _safe_round(regular_market),
        "daily_change": _safe_round(daily_change),
        "daily_change_percent": _safe_round(daily_change_percent),
        "session_status": "regular",
        "market_session": "regular",
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
        "data_source": DEFAULT_PROVIDER,
        "last_updated": utc_now_iso(),
        "market_data_status": DEFAULT_DATA_STATUS,
    }

    response = build_api_response(True, data=data, data_status=DEFAULT_DATA_STATUS)
    _cache_set(_SNAPSHOT_CACHE, cache_key, response)
    return response


def get_chart_data(ticker: str, timeframe: str = "1M") -> Dict[str, Any]:
    normalized = normalize_ticker(ticker)
    timeframe = timeframe.upper()
    cache_key = f"{normalized}:{timeframe}"
    cached_chart = _cache_get(_CHART_CACHE, cache_key)
    if cached_chart is not None:
        return cached_chart

    is_valid, error = validate_ticker(normalized)
    if not is_valid:
        return build_api_response(False, error=error, data_status="unavailable")

    if timeframe not in _TIMEFRAME_MAP:
        return build_api_response(False, error="Unsupported timeframe", data_status="unavailable")

    period, interval = _TIMEFRAME_MAP[timeframe]
    history = _fetch_history(normalized, period, interval)

    if history.empty:
        return build_api_response(
            False,
            error="No chart data is available for this symbol",
            data_status="unavailable",
        )

    if history.index.tz is None:
        history = history.tz_localize("UTC")

    candles: List[Dict[str, Any]] = []
    volume: List[Dict[str, Any]] = []

    for idx, row in history.iterrows():
        ts = int(idx.timestamp())
        close_value = _to_number(row.get("Close"))
        open_value = _to_number(row.get("Open"))
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

        volume.append(
            {
                "time": ts,
                "value": _to_number(row.get("Volume")),
                "color": color,
            }
        )

    indicators = {
        **_calc_emas(history),
        **_calc_ema_series(history),
        "vwap": _calc_vwap(history),
        "vwap_series": _calc_vwap_series(history),
        **_calc_key_levels(history),
    }

    data = {
        "ticker": normalized,
        "timeframe": timeframe,
        "market_session": "regular",
        "candles": candles,
        "volume": volume,
        "indicators": indicators,
        "last_updated": utc_now_iso(),
    }

    response = build_api_response(True, data=data, data_status=DEFAULT_DATA_STATUS)
    _cache_set(_CHART_CACHE, cache_key, response)
    return response
