from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from integrations import webull as webull_api

"""Contract selection for real options trading (2026-09-03) - picks ONE
specific, live, listed option contract for a directional signal ("CALL"/
"PUT", the same recommendation build_strategy_intelligence already
produces for the equity path), entirely from Webull's own real market data.
Deliberately does not touch options/options_brain.py (Yahoo-sourced,
research-only, explicitly disclaimed "No options execution is enabled.") -
this is a separate data path built specifically for real order placement.

Field names below (strike_price, expiration_date, symbol from
get_option_contracts; bid/ask/price/open_interest from get_option_snapshot)
are the REAL shapes confirmed live against the sandbox on 2026-09-03 via
the /api/admin/diagnostic/option-contracts and /api/admin/diagnostic/
option-snapshot routes, not assumed from documentation."""

# Never select an expiration inside this many days - avoids 0DTE/near-dated
# pin and gamma risk. Never look further out than the upper bound either -
# keeps the contract's timeframe roughly matched to the confidence engine's
# own short-horizon signal, and keeps premium cost from ballooning with
# extra extrinsic value for time this app's signal doesn't actually predict.
DEFAULT_MIN_DAYS_OUT = 7
DEFAULT_MAX_DAYS_OUT = 21

# Strike search band around the current price - contracts outside this are
# not considered "near the money" for this selector's purposes.
DEFAULT_STRIKE_BAND_PCT = 0.05

# A contract whose bid-ask spread (relative to the mid price) is wider than
# this is skipped - not tradeable cleanly regardless of direction
# confidence. Deep-OTM/illiquid contracts (see the $0.04/$0.07 ADBE example
# found live during this feature's own discovery pass) can have enormous
# relative spreads on a tiny premium.
DEFAULT_MAX_SPREAD_PCT = 0.25


def _parse_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        parsed = float(value)
        return parsed
    except (TypeError, ValueError):
        return None


def select_option_contract(
    app_key: str,
    app_secret: str,
    ticker: str,
    direction: str,
    current_price: float,
    min_days_out: int = DEFAULT_MIN_DAYS_OUT,
    max_days_out: int = DEFAULT_MAX_DAYS_OUT,
    strike_band_pct: float = DEFAULT_STRIKE_BAND_PCT,
    max_spread_pct: float = DEFAULT_MAX_SPREAD_PCT,
) -> Optional[Dict[str, Any]]:
    """Selects the nearest-expiration, nearest-to-at-the-money, liquid
    contract matching `direction` ("CALL" or "PUT") for `ticker`. Returns
    None (never raises for an ordinary "nothing suitable" outcome - a
    broker/network failure still propagates, matching every other
    best-effort lookup in this codebase's scanner path) when:
      - the broker has no listed contracts in the expiration/strike window,
      - the closest contract's live snapshot has no usable bid/ask, or
      - the bid-ask spread is wider than max_spread_pct of the mid price.
    A None return means "fall back to the existing equity path for this
    candidate" - see the scanner integration in app.py.

    Returns, on success: {"option_symbol", "strike", "expiration_date",
    "option_type", "bid", "ask", "mid", "delta"} - option_symbol is
    Webull's own resolved contract symbol (e.g. "ADBE260918C00420000"),
    useful for snapshot/monitoring lookups, but NOT what gets sent in an
    order leg - place_option_order/preview_option_order take the
    underlying ticker + strike + expiration_date directly (see
    integrations/webull.py's _build_option_order docstring for why)."""
    option_type = "CALL" if str(direction).upper() == "CALL" else "PUT"
    if current_price is None or current_price <= 0:
        return None

    today = date.today()
    start_date = (today + timedelta(days=min_days_out)).isoformat()
    end_date = (today + timedelta(days=max_days_out)).isoformat()
    strike_lo = round(current_price * (1 - strike_band_pct), 2)
    strike_hi = round(current_price * (1 + strike_band_pct), 2)

    contracts = webull_api.get_option_contracts(
        app_key, app_secret, ticker,
        option_type=option_type, start_date=start_date, end_date=end_date,
        strike_price_gte=strike_lo, strike_price_lte=strike_hi,
    )
    if not contracts:
        return None

    # Nearest expiration first, then nearest-to-the-money strike within it -
    # picking expiration first keeps the timeframe short and consistent
    # even when a farther-out expiration happens to have a closer strike.
    def _expiration_key(contract: Dict[str, Any]) -> str:
        return str(contract.get("expiration_date", "9999-99-99"))

    nearest_expiration = min((_expiration_key(c) for c in contracts), default=None)
    if nearest_expiration is None:
        return None
    same_expiration = [c for c in contracts if _expiration_key(c) == nearest_expiration]

    def _strike_distance(contract: Dict[str, Any]) -> float:
        strike = _parse_float(contract.get("strike_price"))
        if strike is None:
            return float("inf")
        return abs(strike - current_price)

    candidate = min(same_expiration, key=_strike_distance)
    symbol = candidate.get("symbol")
    strike = _parse_float(candidate.get("strike_price"))
    if not symbol or strike is None:
        return None

    liquid = _check_liquidity(app_key, app_secret, symbol, max_spread_pct)
    if liquid is None:
        return None

    return {
        "option_symbol": symbol,
        "strike": strike,
        "expiration_date": nearest_expiration,
        "option_type": option_type,
        "bid": liquid["bid"],
        "ask": liquid["ask"],
        "mid": liquid["mid"],
        "delta": liquid.get("delta"),
    }


def _check_liquidity(app_key: str, app_secret: str, option_symbol: str, max_spread_pct: float) -> Optional[Dict[str, Any]]:
    """Returns {"bid", "ask", "mid", "delta"} if the contract has a usable,
    reasonably tight two-sided market; None otherwise (no snapshot data, a
    zero/missing bid or ask, or a spread wider than max_spread_pct of mid).
    A missing bid in particular means "nobody is currently willing to buy
    this back" - not tradeable regardless of how attractive the ask looks."""
    snapshot_rows = webull_api.get_option_snapshot(app_key, app_secret, [option_symbol])
    row = next((r for r in snapshot_rows if str(r.get("symbol", "")) == option_symbol), None)
    if row is None and snapshot_rows:
        row = snapshot_rows[0]
    if not row:
        return None
    bid = _parse_float(row.get("bid"))
    ask = _parse_float(row.get("ask"))
    if bid is None or ask is None or bid <= 0 or ask <= 0 or ask < bid:
        return None
    mid = (bid + ask) / 2
    if mid <= 0:
        return None
    spread_pct = (ask - bid) / mid
    if spread_pct > max_spread_pct:
        return None
    return {"bid": bid, "ask": ask, "mid": mid, "delta": _parse_float(row.get("delta"))}
