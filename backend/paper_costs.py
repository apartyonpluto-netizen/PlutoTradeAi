"""Modeled slippage and regulatory fees for paper results.

Sandbox fills and the in-app paper journal print at the quoted price, and
Webull paper often reports zero commission. A live fill pays the spread and
the same regulatory fees a real sell does. This module prices that gap so
paper P&L is not the flattering number.

Live records are left alone: their fill prices already include slippage, and
broker_fees.py already stored the fees the broker reported.

Defaults (override with env, never printed):
  PLUTO_PAPER_SLIPPAGE_BPS          adverse bps each side (default 5)
  PLUTO_PAPER_SEC_FEE_RATE          fraction of sell notional (default 0.0000278)
  PLUTO_PAPER_FINRA_TAF_PER_SHARE   per share sold (default 0.000166, cap $8.30)
  PLUTO_PAPER_OPTION_FEE_PER_CONTRACT  per option contract sold (default 0.04)
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

DEFAULT_SLIPPAGE_BPS = 5.0
DEFAULT_SEC_FEE_RATE = 0.0000278
DEFAULT_FINRA_TAF_PER_SHARE = 0.000166
FINRA_TAF_CAP = 8.30
DEFAULT_OPTION_FEE_PER_CONTRACT = 0.04
OPTION_MULTIPLIER = 100.0


def _positive_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value >= 0 else default


def slippage_bps() -> float:
    return _positive_float("PLUTO_PAPER_SLIPPAGE_BPS", DEFAULT_SLIPPAGE_BPS)


def slip_price(price: float, side: str) -> float:
    """Adverse fill. Buys pay more, sells receive less. `side` is buy or sell."""
    rate = slippage_bps() / 10_000.0
    if side == "buy":
        return round(price * (1.0 + rate), 4)
    return round(price * (1.0 - rate), 4)


def regulatory_fees(sell_notional: float, sell_shares: float = 0.0, option_contracts: float = 0.0) -> float:
    sec = max(0.0, sell_notional) * _positive_float("PLUTO_PAPER_SEC_FEE_RATE", DEFAULT_SEC_FEE_RATE)
    taf = min(FINRA_TAF_CAP, max(0.0, sell_shares) * _positive_float("PLUTO_PAPER_FINRA_TAF_PER_SHARE", DEFAULT_FINRA_TAF_PER_SHARE))
    occ = max(0.0, option_contracts) * _positive_float("PLUTO_PAPER_OPTION_FEE_PER_CONTRACT", DEFAULT_OPTION_FEE_PER_CONTRACT)
    return round(sec + taf + occ, 4)


def round_trip(
    entry_price: float,
    exit_price: float,
    quantity: float,
    *,
    short: bool = False,
    option: bool = False,
    apply_slippage: bool = True,
) -> Dict[str, float]:
    """Quote P&L versus the same trade after adverse slippage and regulatory fees."""
    multiplier = OPTION_MULTIPLIER if option else 1.0
    sign = -1.0 if short else 1.0
    quote_pnl = (exit_price - entry_price) * quantity * sign * multiplier
    if apply_slippage:
        slipped_entry = slip_price(entry_price, "sell" if short else "buy")
        slipped_exit = slip_price(exit_price, "buy" if short else "sell")
    else:
        slipped_entry, slipped_exit = entry_price, exit_price
    slipped_pnl = (slipped_exit - slipped_entry) * quantity * sign * multiplier
    sell_price = slipped_entry if short else slipped_exit
    sell_notional = abs(sell_price * quantity * multiplier)
    fees = regulatory_fees(
        sell_notional,
        sell_shares=0.0 if option else quantity,
        option_contracts=quantity if option else 0.0,
    )
    return {
        "entry_price": round(slipped_entry, 4),
        "exit_price": round(slipped_exit, 4),
        "quote_pnl": round(quote_pnl, 4),
        "slippage": round(quote_pnl - slipped_pnl, 4),
        "fees": fees,
        "net": round(slipped_pnl - fees, 4),
    }


def _number(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _environment(trade: Dict[str, Any]) -> str:
    value = str(trade.get("environment") or "").lower()
    if value:
        return value
    try:
        import broker_env
        return str(broker_env.current().get("environment") or "sandbox")
    except Exception:
        return "sandbox"


def _is_live(trade: Dict[str, Any]) -> bool:
    return _environment(trade) == "live"


def estimate_for_record(trade: Dict[str, Any]) -> Optional[Dict[str, float]]:
    """None when the record has no prices to slip. Does not touch live records."""
    if _is_live(trade):
        return None
    entry = _number(trade.get("average_entry_price"))
    exit_price = _number(trade.get("average_exit_price"))
    quantity = _number(trade.get("exited_quantity") if trade.get("exited_quantity") not in (None, "") else trade.get("filled_quantity"))
    if entry is None or exit_price is None or quantity is None or entry <= 0 or exit_price <= 0 or quantity <= 0:
        return None
    short = str(trade.get("direction") or "").lower() == "short" or str(trade.get("side") or "").upper() == "SELL"
    option = str(trade.get("instrument_type") or "").upper() == "OPTION"
    estimate = round_trip(entry, exit_price, quantity, short=short, option=option, apply_slippage=True)
    broker_fees = _number(trade.get("fees"))
    if broker_fees and broker_fees > 0:
        estimate["net"] = round(estimate["net"] + estimate["fees"] - broker_fees, 4)
        estimate["fees"] = round(broker_fees, 4)
        estimate["fees_source"] = "broker"
    else:
        estimate["fees_source"] = "modeled"
    return estimate


def performance_net(trade: Dict[str, Any]) -> Optional[float]:
    """The P&L a report should show. Live uses the stored net. Paper uses
    net_after_costs when stamped, otherwise a fresh estimate, otherwise the
    stored net (unknown prices are not invented)."""
    stored = _number(trade.get("net_realized_pnl"))
    if trade.get("pnl_status") not in (None, "", "complete"):
        return stored
    if _is_live(trade):
        return stored
    stamped = _number(trade.get("net_after_costs"))
    if stamped is not None:
        return stamped
    estimate = estimate_for_record(trade)
    if estimate is None:
        return stored
    return estimate["net"]


def stamp_closed_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """Adds modeled cost fields. Does not change net_realized_pnl, which stays
    the broker-derived number existing reconciliation tests assert on."""
    if record.get("pnl_status") not in (None, "", "complete"):
        return record
    if _is_live(record):
        if record.get("net_realized_pnl") is not None:
            record["net_after_costs"] = record.get("net_realized_pnl")
        return record
    estimate = estimate_for_record(record)
    if estimate is None:
        return record
    record["modeled_slippage"] = estimate["slippage"]
    record["modeled_fees"] = estimate["fees"]
    record["modeled_fees_source"] = estimate["fees_source"]
    record["net_after_costs"] = estimate["net"]
    return record
