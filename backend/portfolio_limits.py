"""Portfolio-level limits: how much of the account one position, all
positions together, and one sector may hold, plus a drawdown pause.

Every limit is a percent of the same equity the scan sizes against, and
every limit is OFF (0) until the user sets it - this module never invents
a risk tolerance. Exposure is market value from the broker's own positions
(including holdings the agent did not open, which it never trades but
does count), plus the agent's working entry orders, plus anything placed
or proposed earlier in the same scan.

The total and sector limits are sizing constraints, not just vetoes: the
scan passes the remaining room to _compute_position_quantity /
_compute_option_contract_quantity, which size the trade down to fit and
skip it only when not even one share or contract fits.

Known simplifications, stated on purpose:
  * Options count at premium (market value), not delta-adjusted exposure.
  * Sectors come from SECTOR_BY_TICKER below - this app's own approximate
    classification for its scan universe, not an official one. Broad index
    ETFs are their own bucket even though they overlap every sector.
  * Drawdown is measured from the highest equity this app has seen. A
    withdrawal lowers equity and can trip it (the safe direction); a
    deposit raises the peak.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("PLUTO_DATA_DIR", str(BASE_DIR / "data"))).resolve()
USER_DATA_ROOT = DATA_DIR / "users"

UNKNOWN_SECTOR = "Unknown"
BROAD_MARKET = "Broad market ETF"
OPTION_MULTIPLIER = 100.0

_SECTORS: Dict[str, Iterable[str]] = {
    "Technology": (
        "AAPL", "MSFT", "NVDA", "AVGO", "ADBE", "CSCO", "INTC", "QCOM", "TXN", "AMAT", "INTU", "MU", "LRCX", "KLAC",
        "PANW", "ADI", "SNPS", "CDNS", "CRWD", "MRVL", "WDAY", "ROP", "PLTR", "SMCI", "ARM", "SNOW", "SHOP", "AMD", "MSTR",
    ),
    "Communication Services": ("GOOGL", "GOOG", "META", "NFLX", "TMUS", "CMCSA", "T", "VZ", "DIS", "ROKU"),
    "Consumer Discretionary": ("AMZN", "TSLA", "BKNG", "ABNB", "ORLY", "PDD", "MELI", "HD", "NKE", "SBUX", "MCD", "RIVN", "DKNG"),
    "Consumer Staples": ("COST", "PEP", "MDLZ", "MNST", "WMT", "PG", "KO", "TGT"),
    "Health Care": ("ISRG", "VRTX", "REGN", "GILD", "DXCM", "UNH", "LLY", "JNJ", "PFE", "MRK", "ABBV", "TMO"),
    "Financials": ("JPM", "BAC", "WFC", "GS", "MS", "SCHW", "V", "MA", "AXP", "PYPL", "COIN", "SOFI", "AFRM"),
    "Energy": ("XOM", "CVX", "COP", "SLB"),
    "Industrials": ("BA", "CAT", "GE", "HON", "UPS", "LMT", "DE", "CTAS", "UBER"),
    BROAD_MARKET: ("SPY", "QQQ", "IWM", "DIA"),
}
SECTOR_BY_TICKER: Dict[str, str] = {ticker: sector for sector, tickers in _SECTORS.items() for ticker in tickers}
# Sector ETFs map to the sector they track.
SECTOR_BY_TICKER.update({
    "XLK": "Technology", "XLC": "Communication Services", "XLY": "Consumer Discretionary", "XLP": "Consumer Staples",
    "XLV": "Health Care", "XLF": "Financials", "XLE": "Energy", "XLI": "Industrials", "XLU": "Utilities",
    "XLB": "Materials", "XLRE": "Real Estate", "SMH": "Technology",
})

# An OCC-style option symbol: underlying, YYMMDD, C/P, 8-digit strike.
_OPTION_SYMBOL = re.compile(r"^([A-Z]{1,6})\d{6}[CP]\d{8}$")


def sector_for(ticker: str) -> str:
    return SECTOR_BY_TICKER.get(str(ticker or "").upper().strip(), UNKNOWN_SECTOR)


def underlying_symbol(position: Dict[str, Any]) -> str:
    """A position's underlying ticker - the option symbol's root for option rows."""
    for key in ("underlying_symbol", "underlying"):
        if position.get(key):
            return str(position[key]).upper()
    symbol = str(position.get("symbol", "")).upper().strip()
    match = _OPTION_SYMBOL.match(symbol)
    return match.group(1) if match else symbol


def _float(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if number == number and number not in (float("inf"), float("-inf")) else 0.0


def limits_from_settings(settings: Dict[str, Any]) -> Dict[str, float]:
    """The four limits, as percents; 0 means off."""
    return {
        "max_position_percent": max(0.0, _float(settings.get("max_position_exposure_percent"))),
        "max_total_exposure_percent": max(0.0, _float(settings.get("max_total_exposure_percent"))),
        "max_sector_exposure_percent": max(0.0, _float(settings.get("max_sector_exposure_percent"))),
        "max_drawdown_percent": max(0.0, _float(settings.get("max_drawdown_percent"))),
    }


def exposure_snapshot(positions: Iterable[Dict[str, Any]], working_orders: Iterable[Dict[str, Any]] = ()) -> Dict[str, Any]:
    """Market value by ticker and sector, from broker positions plus the
    notional of working BUY orders (entries not yet filled). Sell orders are
    exits or protection for held positions and add no exposure."""
    by_ticker: Dict[str, float] = {}
    for position in positions or []:
        ticker = underlying_symbol(position)
        value = abs(_float(position.get("market_value")))
        if not value:
            value = abs(_float(position.get("quantity")) * _float(position.get("last_price")))
        if ticker and value:
            by_ticker[ticker] = by_ticker.get(ticker, 0.0) + value
    for order in working_orders or []:
        if str(order.get("side", "")).upper() != "BUY":
            continue
        ticker = underlying_symbol(order)
        remaining = _float(order.get("total_quantity") or order.get("quantity")) - _float(order.get("filled_quantity"))
        price = _float(order.get("limit_price"))
        multiplier = OPTION_MULTIPLIER if str(order.get("instrument_type", "")).upper() == "OPTION" else 1.0
        if ticker and remaining > 0 and price > 0:
            by_ticker[ticker] = by_ticker.get(ticker, 0.0) + remaining * price * multiplier
    by_sector: Dict[str, float] = {}
    for ticker, value in by_ticker.items():
        sector = sector_for(ticker)
        by_sector[sector] = by_sector.get(sector, 0.0) + value
    return {"total": sum(by_ticker.values()), "by_ticker": by_ticker, "by_sector": by_sector}


def add_exposure(snapshot: Dict[str, Any], ticker: str, value: float) -> None:
    """Counts a trade placed or proposed earlier in this same scan."""
    ticker = str(ticker or "").upper()
    if not ticker or value <= 0:
        return
    snapshot["by_ticker"][ticker] = snapshot["by_ticker"].get(ticker, 0.0) + value
    sector = sector_for(ticker)
    snapshot["by_sector"][sector] = snapshot["by_sector"].get(sector, 0.0) + value
    snapshot["total"] = snapshot.get("total", 0.0) + value


def room_for(ticker: str, equity: float, snapshot: Dict[str, Any], limits: Dict[str, float]) -> Dict[str, Any]:
    """Dollar room left under the total and sector limits for a new trade in
    `ticker`, as sizing constraints: {name: (room_dollars, reason)}. Empty
    when both limits are off. A sector limit with an unclassified ticker
    leaves zero room - concentration can't be checked, so it fails closed."""
    caps: Dict[str, Any] = {}
    sector = sector_for(ticker)
    total_limit = limits.get("max_total_exposure_percent", 0.0)
    sector_limit = limits.get("max_sector_exposure_percent", 0.0)
    if equity <= 0 and (total_limit or sector_limit):
        return {"total_exposure": (0.0, "account equity is zero or unknown - exposure limits can't be checked")}
    if total_limit:
        allowed = equity * total_limit / 100
        used = snapshot.get("total", 0.0)
        caps["total_exposure"] = (
            max(0.0, allowed - used),
            f"total exposure limit reached (${used:,.0f} of ${allowed:,.0f} allowed, {total_limit:g}% of account)",
        )
    if sector_limit:
        if sector == UNKNOWN_SECTOR:
            caps["sector_exposure"] = (0.0, f"{ticker} has no sector classification - the {sector_limit:g}% sector limit can't be checked")
        else:
            allowed = equity * sector_limit / 100
            used = snapshot.get("by_sector", {}).get(sector, 0.0)
            caps["sector_exposure"] = (
                max(0.0, allowed - used),
                f"{sector} exposure limit reached (${used:,.0f} of ${allowed:,.0f} allowed, {sector_limit:g}% of account)",
            )
    return caps


def portfolio_context(ticker: str, trade_value: float, equity: float, snapshot: Dict[str, Any], limits: Dict[str, float]) -> Dict[str, Any]:
    """What the portfolio looks like with this trade added - for the trade plan."""
    sector = sector_for(ticker)
    total_after = snapshot.get("total", 0.0) + max(0.0, trade_value)
    sector_after = snapshot.get("by_sector", {}).get(sector, 0.0) + max(0.0, trade_value)
    pct = (lambda value: round(value / equity * 100, 2)) if equity > 0 else (lambda value: None)
    return {
        "sector": sector,
        "total_exposure_after": round(total_after, 2),
        "total_exposure_after_percent": pct(total_after),
        "sector_exposure_after": round(sector_after, 2),
        "sector_exposure_after_percent": pct(sector_after),
        "limits": {key: value for key, value in limits.items() if value},
    }


# --- drawdown -------------------------------------------------------------------


def _peak_file(user_id: str) -> Path:
    if not user_id:
        raise ValueError("user_id is required.")
    path = USER_DATA_ROOT / user_id
    path.mkdir(parents=True, exist_ok=True)
    return path / "equity_peak.json"


@contextlib.contextmanager
def _locked(path: Path) -> Iterator[None]:
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.touch(exist_ok=True)
    with open(lock_path, "r+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def record_equity_and_get_peak(user_id: str, equity: float, equity_source: str, persist: bool = True) -> Dict[str, Any]:
    """Raises the stored high-water mark when equity is higher, and returns
    it. A change of equity source (configured paper balance vs broker value)
    restarts the peak - the two numbers are not comparable. persist=False
    (previews) computes the same result without writing."""
    path = _peak_file(user_id)
    now = datetime.now(timezone.utc).isoformat()
    with _locked(path):
        try:
            data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except json.JSONDecodeError:
            data = {}
        if not isinstance(data, dict) or data.get("equity_source") != equity_source or _float(data.get("peak")) <= 0:
            data = {"peak": equity, "peak_at": now, "equity_source": equity_source, "since": now}
        elif equity > _float(data.get("peak")):
            data.update({"peak": equity, "peak_at": now})
        data["last_equity"] = equity
        data["last_seen_at"] = now
        if persist:
            tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            os.replace(tmp, path)
        return dict(data)


def drawdown_block_reason(equity: float, peak: float, max_drawdown_percent: float) -> Optional[str]:
    """None unless the drawdown limit is on and equity is at least that far below its peak."""
    if not max_drawdown_percent or peak <= 0:
        return None
    drawdown = (peak - equity) / peak * 100
    if drawdown >= max_drawdown_percent:
        return (
            f"account is {drawdown:.1f}% below its peak (${peak:,.2f}), past your {max_drawdown_percent:g}% drawdown limit - "
            "new entries are paused; existing positions are still monitored and protected. Raise the limit or wait for recovery."
        )
    return None


def summarize(snapshot: Dict[str, Any], equity: float) -> List[Dict[str, Any]]:
    """Sector breakdown for display, largest first."""
    rows = []
    for sector, value in sorted(snapshot.get("by_sector", {}).items(), key=lambda item: -item[1]):
        rows.append({"sector": sector, "value": round(value, 2), "percent": round(value / equity * 100, 2) if equity > 0 else None})
    return rows
