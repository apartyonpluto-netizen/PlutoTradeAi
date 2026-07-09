from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Sequence, Tuple

import pandas as pd
import yfinance as yf

SCAN_LIST = ["TSLA", "NVDA", "AMD", "PLTR", "AAPL", "META", "MSFT", "SPY", "QQQ"]


def _extract_field(frame: pd.DataFrame, ticker: str, field_name: str) -> pd.Series:
    if frame.empty:
        return pd.Series(dtype=float)
    if isinstance(frame.columns, pd.MultiIndex):
        if (ticker, field_name) in frame.columns:
            return frame[(ticker, field_name)].dropna()
        if (field_name, ticker) in frame.columns:
            return frame[(field_name, ticker)].dropna()
        return pd.Series(dtype=float)
    if field_name in frame.columns:
        return frame[field_name].dropna()
    return pd.Series(dtype=float)


def _score(percent_change: float, relative_volume: float, on_watchlist: bool) -> int:
    pct_component = min(abs(percent_change) * 12, 45)
    vol_component = min(relative_volume * 24, 40)
    directional_bonus = 8 if percent_change > 0 else 0
    watchlist_bonus = 6 if on_watchlist else 0
    return int(max(1, min(99, round(pct_component + vol_component + directional_bonus + watchlist_bonus))))


def scan_market(tickers: Sequence[str], watchlist_tickers: Sequence[str]) -> Tuple[List[Dict[str, object]], List[str], str]:
    scan_universe = sorted({ticker.strip().upper() for ticker in tickers if ticker})
    watchlist_set = {ticker.strip().upper() for ticker in watchlist_tickers if ticker}
    now_stamp = datetime.now(timezone.utc).isoformat()
    if not scan_universe:
        return [], ["No tickers supplied to scanner."], now_stamp

    try:
        daily_frame = yf.download(
            tickers=" ".join(scan_universe),
            period="1mo",
            interval="1d",
            auto_adjust=False,
            progress=False,
            threads=True,
            group_by="ticker",
        )
        intraday_frame = yf.download(
            tickers=" ".join(scan_universe),
            period="1d",
            interval="5m",
            auto_adjust=False,
            progress=False,
            threads=True,
            group_by="ticker",
        )
    except Exception as error:
        return [], [f"Scanner fetch failed: {error}"], now_stamp

    rows: List[Dict[str, object]] = []
    errors: List[str] = []
    for ticker in scan_universe:
        daily_close = _extract_field(daily_frame, ticker, "Close")
        daily_volume = _extract_field(daily_frame, ticker, "Volume")
        intraday_close = _extract_field(intraday_frame, ticker, "Close")
        intraday_volume = _extract_field(intraday_frame, ticker, "Volume")
        if daily_close.empty and intraday_close.empty:
            errors.append(f"{ticker}: no market data returned.")
            continue

        current_price = float(intraday_close.iloc[-1]) if not intraday_close.empty else float(daily_close.iloc[-1])
        previous_close = float(daily_close.iloc[-2]) if len(daily_close) > 1 else current_price
        percent_change = ((current_price - previous_close) / previous_close * 100) if previous_close else 0.0
        current_volume = (
            float(intraday_volume.sum())
            if not intraday_volume.empty
            else float(daily_volume.iloc[-1] if not daily_volume.empty else 0.0)
        )
        avg_volume = float(daily_volume.tail(20).mean()) if not daily_volume.empty else 0.0
        relative_volume = (current_volume / avg_volume) if avg_volume else 0.0
        on_watchlist = ticker in watchlist_set
        scanner_score = _score(percent_change=percent_change, relative_volume=relative_volume, on_watchlist=on_watchlist)
        rows.append(
            {
                "ticker": ticker,
                "price": round(current_price, 2),
                "percent_change": round(percent_change, 2),
                "relative_volume": round(relative_volume, 2),
                "volume": int(current_volume),
                "scanner_score": scanner_score,
                "last_updated": now_stamp,
                "on_watchlist": on_watchlist,
            }
        )

    rows.sort(key=lambda row: row["scanner_score"], reverse=True)
    return rows, errors, now_stamp