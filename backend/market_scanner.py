from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

import pandas as pd

if __package__:
    from . import nasdaq_universe
    from .integrations import alpaca_data
else:
    import nasdaq_universe
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

# How long a finished Nasdaq screen is reused. The Render cron runs every
# 5 minutes; 4 minutes lets that cron refresh the list while the scanner
# page's 20-second poll, and the other gunicorn workers, reuse one result.
SCAN_CACHE_SECONDS = 4 * 60
SNAPSHOT_BATCH_SIZE = 80
# Free tier is 200 Alpaca calls/minute. ~50 snapshot batches plus the bar
# calls and the 6-ticker brain pass have to share that. A short gap keeps
# the snapshot burst near 2 calls/second (~120/min) instead of bursting
# the whole minute budget up front.
DEFAULT_SNAPSHOT_GAP_SECONDS = 0.35


class _ScanResult(list):
    """A list of scanner rows plus the screen-level counts the page shows.

    Callers keep unpacking (rows, errors, timestamp). The summary is an
    attribute so adding it does not change that tuple.
    """

    def __init__(self, rows=None, summary=None):
        super().__init__(rows or [])
        self.summary = dict(summary or {})


def _data_dir() -> Path:
    return Path(os.environ.get("PLUTO_DATA_DIR", str(Path(__file__).resolve().parents[1] / "data"))).resolve()


def _cache_file() -> Path:
    path = _data_dir() / "scanner"
    path.mkdir(parents=True, exist_ok=True)
    return path / "nasdaq_scan_cache.json"


def _env_int(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(low, min(value, high))


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def scanner_top_n() -> int:
    return _env_int("PLUTO_SCANNER_TOP_N", 48, 1, 200)


def scanner_score_cap() -> int:
    return _env_int("PLUTO_SCANNER_SCORE_CAP", 120, 1, 400)


def _filters() -> Dict[str, float]:
    return {
        "min_price": _env_float("PLUTO_SCANNER_MIN_PRICE", 5.0),
        "max_price": _env_float("PLUTO_SCANNER_MAX_PRICE", 2000.0),
        "min_dollar_volume": _env_float("PLUTO_SCANNER_MIN_DOLLAR_VOLUME", 20_000_000.0),
        "min_share_volume": _env_float("PLUTO_SCANNER_MIN_SHARE_VOLUME", 300_000.0),
    }


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


def _num(value: object) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _money(value: float) -> str:
    if value >= 1_000_000_000:
        return f"${value / 1_000_000_000:.1f}B"
    if value >= 1_000_000:
        return f"${value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"${value / 1_000:.0f}k"
    return f"${value:.0f}"


def _snapshot_metrics(snapshot: Dict[str, object]) -> Optional[Dict[str, float]]:
    trade = snapshot.get("latestTrade") or {}
    daily = snapshot.get("dailyBar") or {}
    prev = snapshot.get("prevDailyBar") or {}
    if not isinstance(trade, dict):
        trade = {}
    if not isinstance(daily, dict):
        daily = {}
    if not isinstance(prev, dict):
        prev = {}
    prev_close = _num(prev.get("c"))
    price = _num(trade.get("p")) or _num(daily.get("c")) or prev_close
    if price is None or price <= 0:
        return None
    base = prev_close or price
    percent_change = ((price - base) / base * 100) if base else 0.0
    prev_shares = _num(prev.get("v")) or 0.0
    today_shares = _num(daily.get("v")) or 0.0
    prev_dollars = (prev_close or 0.0) * prev_shares
    today_dollars = price * today_shares
    return {
        "price": price,
        "percent_change": percent_change,
        "share_volume": max(prev_shares, today_shares),
        "dollar_volume": max(prev_dollars, today_dollars),
    }


def _passes_liquidity(metrics: Dict[str, float], filters: Dict[str, float]) -> Optional[str]:
    price = metrics["price"]
    if price < filters["min_price"] or price > filters["max_price"]:
        return None
    dollar_ok = metrics["dollar_volume"] >= filters["min_dollar_volume"]
    share_ok = metrics["share_volume"] >= filters["min_share_volume"]
    if not dollar_ok and not share_ok:
        return None
    parts = [f"price ${price:.2f} is inside ${filters['min_price']:.0f}–${filters['max_price']:.0f}"]
    if dollar_ok:
        parts.append(f"dollar volume {_money(metrics['dollar_volume'])}")
    if share_ok:
        parts.append(f"share volume {int(metrics['share_volume']):,}")
    return " and ".join(parts)


def _score_from_bars(
    tickers: Sequence[str],
    watchlist_set: set,
    now_stamp: str,
    screen_reasons: Dict[str, str],
) -> Tuple[List[Dict[str, object]], List[str]]:
    errors: List[str] = []
    results: List[Dict[str, object]] = []
    try:
        daily = alpaca_data.get_bars(list(tickers), period="1mo", interval="1d")
        intraday = alpaca_data.get_bars(list(tickers), period="1d", interval="5m")
    except Exception as error:
        return [], [f"Scanner fetch failed: {error}"]

    for ticker in tickers:
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
        row = {
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
        if ticker in screen_reasons:
            row["screen_reason"] = screen_reasons[ticker]
        results.append(row)
    results.sort(key=lambda row: row["scanner_score"], reverse=True)
    return results, errors


def _apply_watchlist(rows: List[Dict[str, object]], watchlist_tickers: Sequence[str] | None) -> None:
    watchlist_set = {ticker.upper().strip() for ticker in (watchlist_tickers or []) if ticker}
    for row in rows:
        row["on_watchlist"] = str(row.get("ticker", "")).upper() in watchlist_set


def _read_disk_cache() -> Optional[Tuple[_ScanResult, List[str], str]]:
    path = _cache_file()
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("rows"), list):
        return None
    rows = _ScanResult(payload["rows"], payload.get("summary") or {})
    return rows, list(payload.get("errors") or []), str(payload.get("last_updated") or "")


def _write_disk_cache(rows: _ScanResult, errors: List[str], last_updated: str) -> None:
    path = _cache_file()
    body = {
        "cached_at": time.time(),
        "top_n": scanner_top_n(),
        "rows": list(rows),
        "errors": errors,
        "last_updated": last_updated,
        "summary": rows.summary,
    }
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(body), encoding="utf-8")
    os.replace(tmp, path)


def _cache_is_fresh(payload_age_seconds: float) -> bool:
    try:
        ttl = int(os.environ.get("PLUTO_SCANNER_CACHE_SECONDS", str(SCAN_CACHE_SECONDS)))
    except ValueError:
        ttl = SCAN_CACHE_SECONDS
    return payload_age_seconds < max(30, ttl)


def _compute_nasdaq_scan(
    watchlist_tickers: Sequence[str] | None,
    universe_fallback: Sequence[str] | None,
) -> Tuple[_ScanResult, List[str], str]:
    now_stamp = datetime.now(timezone.utc).isoformat()
    symbols, source = nasdaq_universe.load_symbols(universe_fallback)
    summary = {
        "mode": "nasdaq",
        "universe_source": source,
        "universe_count": len(symbols),
        "snapshot_count": 0,
        "liquid_count": 0,
        "scored_count": 0,
        "shown_count": 0,
        "top_n": scanner_top_n(),
    }
    if not symbols:
        return _ScanResult([], summary), ["No Nasdaq symbols available and no fallback list was provided."], now_stamp

    filters = _filters()
    try:
        snapshots = alpaca_data.get_snapshots(symbols)
    except Exception as error:
        raise RuntimeError(f"snapshot screen failed: {error}") from error

    screened: List[Tuple[str, Dict[str, float], str]] = []
    for symbol in symbols:
        snapshot = snapshots.get(symbol)
        if not isinstance(snapshot, dict):
            continue
        metrics = _snapshot_metrics(snapshot)
        if metrics is None:
            continue
        reason = _passes_liquidity(metrics, filters)
        if reason:
            screened.append((symbol, metrics, reason))

    screened.sort(key=lambda item: (abs(item[1]["percent_change"]), item[1]["dollar_volume"]), reverse=True)
    score_cap = scanner_score_cap()
    survivors = screened[:score_cap]
    screen_reasons = {symbol: reason for symbol, _metrics, reason in survivors}
    summary["snapshot_count"] = len(snapshots)
    summary["liquid_count"] = len(screened)

    scored, errors = _score_from_bars([symbol for symbol, _metrics, _reason in survivors], set(), now_stamp, screen_reasons)
    if not scored and errors and str(errors[0]).startswith("Scanner fetch failed"):
        raise RuntimeError(errors[0])

    top_n = scanner_top_n()
    shown = scored[:top_n]
    summary["scored_count"] = len(scored)
    summary["shown_count"] = len(shown)
    for rank, row in enumerate(shown, start=1):
        row["rank"] = rank
        row["universe_count"] = summary["universe_count"]
        row["liquid_count"] = summary["liquid_count"]
        row["scored_count"] = summary["scored_count"]
        row["top_n"] = top_n
        row["universe_source"] = source
        screen = row.get("screen_reason") or "passed the liquidity screen"
        row["qualification_reason"] = (
            f"#{rank} of {len(shown)}. {screen}. "
            f"Scanner score {row['scanner_score']} from {row['percent_change']}% and {row['relative_volume']}x relative volume."
        )
    _apply_watchlist(shown, watchlist_tickers)
    if source == "fallback":
        errors = list(errors) + ["Nasdaq symbol directory was unavailable; scanned the saved fallback list."]
    result = _ScanResult(shown, summary)
    return result, errors, now_stamp


def _scan_nasdaq(
    watchlist_tickers: Sequence[str] | None,
    universe_fallback: Sequence[str] | None,
    force_refresh: bool,
) -> Tuple[_ScanResult, List[str], str]:
    """One shared Nasdaq screen for every gunicorn worker.

    The lock covers the Alpaca calls. A second worker waits and then
    reads the cache the first worker wrote, instead of each worker
    spending its own copy of the 200-calls/minute budget.
    """
    import fcntl

    path = _cache_file()
    lock_path = path.with_name(path.name + ".lock")
    with open(lock_path, "a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            if not force_refresh and path.exists():
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    age = time.time() - float(payload.get("cached_at") or 0)
                    same_limit = int(payload.get("top_n") or 0) == scanner_top_n()
                except (OSError, ValueError, TypeError):
                    payload, age, same_limit = None, 10**9, False
                if payload and same_limit and _cache_is_fresh(age):
                    cached = _read_disk_cache()
                    if cached:
                        rows, errors, stamp = cached
                        _apply_watchlist(rows, watchlist_tickers)
                        return rows, errors, stamp
            try:
                rows, errors, stamp = _compute_nasdaq_scan(watchlist_tickers, universe_fallback)
            except Exception as error:
                cached = _read_disk_cache()
                if cached:
                    rows, errors, stamp = cached
                    _apply_watchlist(rows, watchlist_tickers)
                    note = f"Scanner refresh failed ({error}); showing the last saved list."
                    if note not in errors:
                        errors = list(errors) + [note]
                    return rows, errors, stamp
                now_stamp = datetime.now(timezone.utc).isoformat()
                return _ScanResult([], {"mode": "nasdaq", "top_n": scanner_top_n()}), [f"Scanner fetch failed: {error}"], now_stamp
            _write_disk_cache(rows, errors, stamp)
            return rows, errors, stamp
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def scan_market(
    tickers: Sequence[str] | None = None,
    watchlist_tickers: Sequence[str] | None = None,
    *,
    universe_fallback: Sequence[str] | None = None,
    force_refresh: bool = False,
) -> Tuple[List[Dict[str, object]], List[str], str]:
    """Score an explicit ticker list, or screen the whole Nasdaq.

    An explicit list (the macro tape, and tests) keeps the original
    two-call bar fetch. tickers=None is the autonomous/scanner path:
    cheap snapshot filter across the Nasdaq directory, then the same
    bar score on the liquid survivors, returning the top
    PLUTO_SCANNER_TOP_N names (default 48).
    """
    if tickers is None:
        return _scan_nasdaq(watchlist_tickers, universe_fallback, force_refresh)

    scan_tickers = [ticker.upper().strip() for ticker in tickers if ticker]
    now_stamp = datetime.now(timezone.utc).isoformat()
    if not scan_tickers:
        return _ScanResult([], {"mode": "explicit", "universe_count": 0}), ["No tickers supplied to scanner."], now_stamp

    results, errors = _score_from_bars(scan_tickers, set(), now_stamp, {})
    _apply_watchlist(results, watchlist_tickers)
    summary = {"mode": "explicit", "universe_count": len(scan_tickers), "shown_count": len(results)}
    return _ScanResult(results, summary), errors, now_stamp
