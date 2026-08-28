from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Sequence, Tuple

import pandas as pd

if __package__:
    from .integrations import alpaca_data
else:
    from integrations import alpaca_data

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


def _extract_ticker_frame(bars_by_ticker: Dict[str, pd.DataFrame], ticker: str, field_name: str) -> pd.Series:
    """ticker-scoped lookup into the {ticker: flat_dataframe} shape
    alpaca_data.get_bars returns (see that module's docstring) - a ticker
    Alpaca had no bars for is simply absent from the dict, same as it
    would be all-NaN/missing from yfinance's old batch frame."""
    frame = bars_by_ticker.get(ticker)
    if frame is None or frame.empty or field_name not in frame.columns:
        return pd.Series(dtype=float)
    return frame[field_name].dropna()


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
        # Alpaca Market Data API, not yfinance - found live 2026-08-28 that
        # Yahoo Finance was actively rate-limiting every single request from
        # this app's Render deployment ("Scanner timed out - Yahoo Finance
        # is rate limiting.", 0 rows, every scan), which is the direct
        # cause of candidates_found being 0 on every autonomous scan: an
        # empty scan here plus an empty watchlist means the per-ticker
        # intelligence loop downstream (_build_page_context) never even
        # runs. This is a real, external vendor block on Yahoo's public
        # (unofficial) endpoint, not something any amount of retry/backoff/
        # thread-bounding on THIS app's side could fix - see this file's
        # git history for the session-long sequence of yfinance rate-limit
        # workarounds that all treated the symptom, not the cause. See
        # integrations/alpaca_data.py's own module docstring for exactly
        # what's covered by this migration and what's deliberately still
        # on yfinance (VIX shadow-mode data, options chains, other
        # lower-traffic dashboard pages).
        daily = alpaca_data.get_bars(scan_tickers, period="1mo", interval="1d")
        intraday = alpaca_data.get_bars(scan_tickers, period="1d", interval="5m")
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
