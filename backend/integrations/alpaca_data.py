from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence

import pandas as pd
import requests

"""Replaces yfinance as this app's market-data source on the candidate-
discovery critical path (market_scanner.py + the three brains/ modules) -
found live 2026-08-28 that Yahoo Finance is actively rate-limiting requests
from Render's IP range ("Scanner timed out - Yahoo Finance is rate
limiting.", 0 rows returned, every single scan), which is the direct cause
of "0 candidates found" on every run: an empty scanner_rows plus an empty
watchlist means intelligence_tickers is empty, so the per-ticker
strategy/chart/extended-hours loop in _build_page_context never runs at
all - not a confidence-threshold miss, a total absence of input data. This
is a real, external vendor block, not an app bug - see market_scanner.py's
own comments for the session-long history of yfinance rate-limit
workarounds that all treated the SYMPTOM (timeouts, thread leaks) without
being able to fix Yahoo actually blocking the requests.

Alpaca Basic (free) plan: US stocks/ETFs, IEX real-time feed (not full-
market SIP - that needs the $99/mo Algo Trader Plus tier), historical data
back to 2016, 200 API calls/min, most-recent-15-minutes embargoed for
non-real-time feeds - all comfortably enough for this app's current scan
cadence (one multi-symbol batch call for the ~48-ticker CORE_SCAN_UNIVERSE,
plus up to 6 intelligence tickers x up to 2 calls each per scan, nowhere
near 200/min). Verified against Alpaca's own published API reference
(docs.alpaca.markets/reference/stockbars) 2026-08-28 - GET
https://data.alpaca.markets/v2/stocks/bars, symbols= (comma-separated),
timeframe= (e.g. "1Day"/"5Min"), start=/end= (RFC-3339 or YYYY-MM-DD),
feed=iex for the free tier (the default, "sip", requires the paid tier and
would reject a Basic-plan key). Response shape:
{"bars": {"AAPL": [{"t": "...Z", "o":.., "h":.., "l":.., "c":.., "v":..}, ...]}}
per symbol, sorted by symbol then timestamp, paginated via
next_page_token when the total point count (across ALL symbols, not
per-symbol) exceeds `limit`.

Deliberately scoped to the candidate-discovery path only - NOT a full
yfinance replacement across this app. Left on yfinance, as a conscious
choice, not an oversight:
  - regime.py's single VIX (^VIX) shadow-mode-only snapshot: Alpaca's
    equities API has no index data, VIX isn't a tradable stock/ETF, and
    this call is low-frequency/non-critical (regime.py's own docstring:
    "SHADOW MODE ONLY... nothing derived from this is read by
    entries_allowed, sizing, the LLM step, or order submission") - not a
    contributor to the rate-limit problem this module fixes.
  - options_brain.py's option-chain fetch: a different Alpaca endpoint and
    response shape (/v1beta1/options/snapshots/{symbol}, per-contract
    snapshots rather than OHLCV bars) - already explicitly excluded from
    the actual autonomous-scan path via include_options=False (see
    app.py's own comment on that call), so it isn't part of why
    candidates_found was 0.
  - app.py's yf.Search() ticker-name-autocomplete helper and
    analytics.py/candle_brain.py/pattern_brain.py/paper_trader.py's own
    yf.Ticker(...).history() calls: separate, lower-traffic dashboard
    features, not part of the scan/candidate-discovery request path."""

_DATA_BASE_URL = "https://data.alpaca.markets"
_STOCK_BARS_PATH = "/v2/stocks/bars"

# Calendar-day lookback for each period string this app actually uses,
# padded well past the underlying trading-day count so weekends/holidays
# never leave a request short of data - Alpaca counts by calendar day, not
# trading day, unlike yfinance's period="1mo"-style shorthand.
_PERIOD_TO_LOOKBACK_DAYS = {
    "1d": 4,
    "2d": 6,
    "5d": 12,
    "1mo": 40,
    "9mo": 285,
}

# yfinance interval string -> Alpaca timeframe string.
_INTERVAL_TO_ALPACA_TIMEFRAME = {
    "1d": "1Day",
    "5m": "5Min",
}

_REQUIRED_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]


def is_configured() -> bool:
    return bool(os.environ.get("ALPACA_API_KEY_ID", "").strip() and os.environ.get("ALPACA_API_SECRET_KEY", "").strip())


def _credential_headers() -> Dict[str, str]:
    key_id = os.environ.get("ALPACA_API_KEY_ID", "").strip()
    secret_key = os.environ.get("ALPACA_API_SECRET_KEY", "").strip()
    if not key_id or not secret_key:
        raise ValueError("ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY are not configured for this deployment.")
    return {"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret_key}


def _feed() -> str:
    # Overridable via env var so upgrading to Algo Trader Plus ($99/mo,
    # full-market SIP data) is a one-line config change, not a code change -
    # see this module's own docstring for the free-tier default reasoning.
    return os.environ.get("ALPACA_DATA_FEED", "iex").strip() or "iex"


def _resolve_start(period: str) -> datetime:
    lookback_days = _PERIOD_TO_LOOKBACK_DAYS.get(period)
    if lookback_days is None:
        raise ValueError(f"Unsupported period {period!r} - add it to _PERIOD_TO_LOOKBACK_DAYS.")
    return datetime.now(timezone.utc) - timedelta(days=lookback_days)


def _resolve_timeframe(interval: str) -> str:
    timeframe = _INTERVAL_TO_ALPACA_TIMEFRAME.get(interval)
    if timeframe is None:
        raise ValueError(f"Unsupported interval {interval!r} - add it to _INTERVAL_TO_ALPACA_TIMEFRAME.")
    return timeframe


def _call_with_429_retry(action_label: str, call):
    """Same retry-on-429 shape as integrations/webull.py's own
    _call_with_429_retry (see that module's docstring for why this pattern
    exists) - a transient rate-limit response from Alpaca itself (distinct
    from Yahoo's blocking, which this whole module exists to route around)
    should be retried with backoff, not surfaced as a hard failure on the
    first hit."""
    last_error: Optional[BaseException] = None
    for attempt in range(3):
        response = call()
        if response.status_code == 429 and attempt < 2:
            last_error = ValueError(f"Alpaca API rate limited ({action_label})")
            time.sleep(1.5 * (attempt + 1))
            continue
        return response
    raise last_error or ValueError(f"Alpaca API error ({action_label}): exhausted retries with no response received")


def _bars_to_frame(bars: List[Dict[str, object]]) -> pd.DataFrame:
    """Builds a FLAT DataFrame (never MultiIndex) with exactly the column
    names/shape callers already expect from yfinance - Open/High/Low/Close/
    Volume, ascending DatetimeIndex. Every existing caller (market_scanner.
    py's _extract_ticker_frame, the three brains/ modules' _extract_field/
    _normalize_ohlcv/MultiIndex-unwrap blocks) already checks
    `isinstance(dataframe.columns, pd.MultiIndex)` and falls through to a
    plain flat-column read when it's False - so returning flat here needs
    ZERO changes to any of that existing downstream logic, only to the
    fetch call itself."""
    if not bars:
        return pd.DataFrame(columns=_REQUIRED_COLUMNS)
    frame = pd.DataFrame(
        {
            "Open": [float(bar["o"]) for bar in bars],
            "High": [float(bar["h"]) for bar in bars],
            "Low": [float(bar["l"]) for bar in bars],
            "Close": [float(bar["c"]) for bar in bars],
            "Volume": [float(bar.get("v", 0) or 0) for bar in bars],
        },
        index=pd.to_datetime([bar["t"] for bar in bars], utc=True),
    )
    frame.index.name = "Date"
    return frame.sort_index()


def get_bars(symbols: Sequence[str], period: str, interval: str) -> Dict[str, pd.DataFrame]:
    """Multi-symbol historical bars - replaces market_scanner.py's batched
    yf.download() call. Returns {symbol: flat_dataframe}, one entry per
    symbol that had any data in the response (a symbol Alpaca has no bars
    for - e.g. a bad/delisted ticker - is simply absent from the dict,
    same as it would be missing/all-NaN from yfinance's own batch frame).
    Follows next_page_token until Alpaca reports no more pages - the
    default limit=10000 already comfortably covers this app's entire
    CORE_SCAN_UNIVERSE (see market_scanner.py) for a 1-month daily window
    in one page, but paginating defensively costs nothing and protects
    against a future larger universe silently losing data instead of
    erroring."""
    normalized_symbols = [symbol.strip().upper() for symbol in symbols if symbol and symbol.strip()]
    if not normalized_symbols:
        return {}

    headers = _credential_headers()
    params = {
        "symbols": ",".join(normalized_symbols),
        "timeframe": _resolve_timeframe(interval),
        "start": _resolve_start(period).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "limit": 10000,
        "adjustment": "raw",
        "feed": _feed(),
    }

    bars_by_symbol: Dict[str, List[Dict[str, object]]] = {}
    page_token: Optional[str] = None
    while True:
        request_params = dict(params)
        if page_token:
            request_params["page_token"] = page_token
        response = _call_with_429_retry(
            "bars",
            lambda p=request_params: requests.get(_DATA_BASE_URL + _STOCK_BARS_PATH, headers=headers, params=p, timeout=10),
        )
        if response.status_code != 200:
            raise ValueError(f"Alpaca API error (bars): HTTP {response.status_code} - {response.text[:300]}")
        payload = response.json()
        for symbol, symbol_bars in (payload.get("bars") or {}).items():
            bars_by_symbol.setdefault(symbol, []).extend(symbol_bars or [])
        page_token = payload.get("next_page_token")
        if not page_token:
            break

    return {symbol: _bars_to_frame(bars) for symbol, bars in bars_by_symbol.items()}


def get_bars_single(symbol: str, period: str, interval: str) -> pd.DataFrame:
    """Single-symbol convenience wrapper over get_bars - replaces the
    yf.download(tickers=<one ticker>, ...) pattern used by
    brains/strategy_brain.py, brains/charting_brain.py, and
    brains/extended_hours_brain.py, each of whose downstream normalization
    already handles a flat single-ticker frame correctly (see get_bars'
    docstring)."""
    normalized = (symbol or "").strip().upper()
    if not normalized:
        return pd.DataFrame(columns=_REQUIRED_COLUMNS)
    return get_bars([normalized], period=period, interval=interval).get(normalized, pd.DataFrame(columns=_REQUIRED_COLUMNS))
