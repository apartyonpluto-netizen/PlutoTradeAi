from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Sequence, Tuple

import pandas as pd
import yfinance as yf

DEFAULT_SCAN_UNIVERSE = [
    "AAPL",
    "AMD",
    "META",
    "MSFT",
    "NVDA",
    "PLTR",
    "QQQ",
    "SPY",
    "TSLA",
]


def _extract_ticker_frame(dataframe: pd.DataFrame, ticker: str, field_name: str) -> pd.Series:
    if dataframe.empty:
        return pd.Series(dtype=float)

    if isinstance(dataframe.columns, pd.MultiIndex):
        if (ticker, field_name) in dataframe.columns:
            return dataframe[(ticker, field_name)].dropna()
        if (field_name, ticker) in dataframe.columns:
            return dataframe[(field_name, ticker)].dropna()
        return pd.Series(dtype=float)

    if field_name in dataframe.columns:
        return dataframe[field_name].dropna()
    return pd.Series(dtype=float)


def _compute_scanner_score(percent_change: float, relative_volume: float) -> int:
    pct_component = min(abs(percent_change) * 12, 45)
    volume_component = min(relative_volume * 25, 45)
    directional_bonus = 8 if percent_change > 0 else 0
    score = int(round(min(99, pct_component + volume_component + directional_bonus)))
    return max(1, score)


def scan_market(
    tickers: Sequence[str] | None = None, watchlist_tickers: Sequence[str] | None = None
) -> Tuple[List[Dict[str, str]], List[str], str]:
    scan_tickers = [ticker.upper().strip() for ticker in (tickers or DEFAULT_SCAN_UNIVERSE) if ticker]
    watchlist_set = {ticker.upper().strip() for ticker in (watchlist_tickers or [])}
    now_stamp = datetime.now(timezone.utc).isoformat()
    if not scan_tickers:
        return [], ["No tickers supplied to scanner."], now_stamp

    errors: List[str] = []
    results: List[Dict[str, str]] = []

    try:
        # timeout=8 - tightened from 15s on 2026-08-21: even with an
        # explicit per-call timeout, live production logs (Standard plan,
        # 4 workers) still showed gunicorn's own 60s WORKER TIMEOUT firing
        # and SIGKILLing workers during a sustained YFRateLimitError burst.
        # Root cause: this call is sequential with the per-ticker brain
        # chain below (up to 6 more yf.download() calls per ticker, see
        # strategy_brain.py's _fetch_ohlcv), so worst-case totals stack
        # ADDITIVELY within one request. At 15s+8s×6=63s the old numbers
        # could exceed even a healthy 60s worker timeout on their own,
        # before any actual rate-limiting. Tightened here and in the brains
        # to 8s+5s×6=38s, and gunicorn's --timeout raised to 90s (Procfile,
        # render.yaml) for defense in depth on the new 4-worker/2GB plan.
        daily = yf.download(
            tickers=" ".join(scan_tickers),
            period="1mo",
            interval="1d",
            auto_adjust=False,
            progress=False,
            threads=True,
            group_by="ticker",
            timeout=8,
        )
        intraday = yf.download(
            tickers=" ".join(scan_tickers),
            period="1d",
            interval="5m",
            auto_adjust=False,
            progress=False,
            threads=True,
            group_by="ticker",
            timeout=8,
        )
    except Exception as error:
        return [], [f"Scanner fetch failed: {error}"], now_stamp

    for ticker in scan_tickers:
        close_daily = _extract_ticker_frame(daily, ticker, "Close")
        volume_daily = _extract_ticker_frame(daily, ticker, "Volume")
        close_intraday = _extract_ticker_frame(intraday, ticker, "Close")
        volume_intraday = _extract_ticker_frame(intraday, ticker, "Volume")

        if close_daily.empty and close_intraday.empty:
            errors.append(f"{ticker}: no market data returned.")
            continue

        current_price = float(close_intraday.iloc[-1]) if not close_intraday.empty else float(close_daily.iloc[-1])
        previous_close = float(close_daily.iloc[-2]) if len(close_daily) >= 2 else current_price
        percent_change = ((current_price - previous_close) / previous_close * 100) if previous_close else 0.0

        today_volume = (
            float(volume_intraday.dropna().sum())
            if not volume_intraday.empty
            else float(volume_daily.iloc[-1] if not volume_daily.empty else 0.0)
        )
        avg_volume = float(volume_daily.tail(20).mean()) if not volume_daily.empty else 0.0
        relative_volume = (today_volume / avg_volume) if avg_volume else 0.0

        scanner_score = _compute_scanner_score(percent_change=percent_change, relative_volume=relative_volume)
        status = "Hot" if scanner_score >= 80 else "Watch" if scanner_score >= 60 else "Quiet"

        results.append(
            {
                "ticker": ticker,
                "price": round(current_price, 2),
                "percent_change": round(percent_change, 2),
                "volume": int(today_volume),
                "relative_volume": round(relative_volume, 2),
                "scanner_score": scanner_score,
                "status": status,
                "last_updated": now_stamp,
                "on_watchlist": ticker in watchlist_set,
            }
        )

    results.sort(key=lambda row: row["scanner_score"], reverse=True)
    return results, errors, now_stamp
