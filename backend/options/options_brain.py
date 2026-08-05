from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Dict, List

import yfinance as yf

if __package__:
    from options_brain import build_options_outlook as legacy_build_options_outlook
    from options_brain import _fetch_option_chain_with_retry, _fetch_option_expirations_with_retry, _fetch_history_with_retry
    from .greeks import black_scholes_greeks
else:
    from options_brain import build_options_outlook as legacy_build_options_outlook
    from options_brain import _fetch_option_chain_with_retry, _fetch_option_expirations_with_retry, _fetch_history_with_retry
    from greeks import black_scholes_greeks

DISCLAIMER = "For research only. No options execution is enabled."


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_numeric_text(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace("$", "").replace(",", "")
    if not text or "unavailable" in text.lower():
        return None
    if "%" in text:
        text = text.split("%", 1)[0]
    try:
        return float(text)
    except ValueError:
        return None


def build_options_research(ticker: str) -> Dict[str, Any]:
    legacy = legacy_build_options_outlook(ticker)
    expirations = legacy.get("expirations", []) if isinstance(legacy.get("expirations"), list) else []
    expiration_suggestions: List[str] = [
        str(item.get("expiration_date", "data unavailable")) for item in expirations[:3] if isinstance(item, dict)
    ]
    while len(expiration_suggestions) < 3:
        expiration_suggestions.append("data unavailable")

    first_contract = expirations[0] if expirations and isinstance(expirations[0], dict) else {}
    direction = str(legacy.get("suggested_direction", "WAIT")).upper()
    if direction not in {"CALL", "PUT", "WAIT"}:
        direction = "WAIT"

    stock_price = legacy.get("current_stock_price", "data unavailable")
    support = legacy.get("key_support", "data unavailable")
    resistance = legacy.get("key_resistance", "data unavailable")
    options_chain_available = any(
        isinstance(item, str) and item and "unavailable" not in item.lower() for item in expiration_suggestions
    )

    if not options_chain_available:
        return {
            "ticker": str(legacy.get("ticker", ticker)).upper(),
            "stock_price": stock_price if isinstance(stock_price, str) else "data unavailable",
            "direction": "WAIT",
            "confidence": 0,
            "reason": "data unavailable: options chain unavailable for this ticker right now.",
            "support": "data unavailable",
            "resistance": "data unavailable",
            "expected_move": "data unavailable",
            "expiration_suggestions": ["data unavailable", "data unavailable", "data unavailable"],
            "strike_area": "data unavailable",
            "estimated_premium": "data unavailable",
            "breakeven": "data unavailable",
            "risk_level": "data unavailable",
            "expirations": expirations,
            "disclaimer": DISCLAIMER,
            "generated_at": legacy.get("generated_at", _now_iso()),
        }

    return {
        "ticker": str(legacy.get("ticker", ticker)).upper(),
        "stock_price": stock_price,
        "direction": direction,
        "confidence": int(legacy.get("confidence_score", 0) or 0),
        "reason": legacy.get("reason_for_direction", "data unavailable"),
        "support": support,
        "resistance": resistance,
        "expected_move": legacy.get("expected_move", "data unavailable"),
        "expiration_suggestions": expiration_suggestions,
        "strike_area": first_contract.get("suggested_strike_area", "data unavailable"),
        "estimated_premium": first_contract.get("estimated_option_premium", "data unavailable"),
        "breakeven": first_contract.get("break_even_price", "data unavailable"),
        "risk_level": legacy.get("risk_level", "data unavailable"),
        "expirations": expirations,
        "disclaimer": DISCLAIMER,
        "generated_at": legacy.get("generated_at", _now_iso()),
    }


def to_legacy_options_payload(research_payload: Dict[str, Any]) -> Dict[str, Any]:
    stock_price = research_payload.get("stock_price", "Data unavailable")
    support = research_payload.get("support", "Data unavailable")
    resistance = research_payload.get("resistance", "Data unavailable")
    direction = str(research_payload.get("direction", "WAIT")).upper()
    if direction not in {"CALL", "PUT", "WAIT"}:
        direction = "WAIT"

    # Prefer the real per-expiration data computed by legacy_build_options_outlook
    # (distinct strike/premium/breakeven/risk copy per timeframe) over fabricating
    # three identical entries from the condensed research_payload fields.
    expirations = research_payload.get("expirations")
    if not isinstance(expirations, list) or not expirations:
        expirations = [
            {
                "timeframe": "Short-term expiration",
                "expiration_date": research_payload.get("expiration_suggestions", ["Data unavailable"])[0],
                "suggested_contract_type": "Call" if direction == "CALL" else "Put" if direction == "PUT" else "Data unavailable",
                "suggested_strike_area": research_payload.get("strike_area", "Data unavailable"),
                "estimated_option_premium": research_payload.get("estimated_premium", "Data unavailable"),
                "break_even_price": research_payload.get("breakeven", "Data unavailable"),
                "risk_warning": "Data unavailable",
                "selection_reason": "Data unavailable",
            }
        ]

    confidence = int(research_payload.get("confidence", 0) or 0)
    return {
        "ticker": research_payload.get("ticker", ""),
        "suggested_direction": direction,
        "confidence_score": confidence,
        "reason_for_direction": research_payload.get("reason", "Data unavailable"),
        "current_stock_price": stock_price,
        "expected_move": research_payload.get("expected_move", "Data unavailable"),
        "key_support": support,
        "key_resistance": resistance,
        "breakout_price": resistance,
        "breakdown_price": support,
        "risk_level": research_payload.get("risk_level", "Data unavailable"),
        "expirations": expirations,
        "disclaimer": research_payload.get("disclaimer", DISCLAIMER),
        "generated_at": research_payload.get("generated_at", _now_iso()),
    }


def _years_to_expiry(expiration_date: str) -> float:
    try:
        expiry = datetime.strptime(expiration_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return 0.0
    days = (expiry - datetime.now(timezone.utc)).total_seconds() / 86400
    return max(days, 0.5) / 365  # floor at half a day so 0DTE contracts don't divide by ~0 in Black-Scholes


def _safe_num(value: Any, default: float = 0.0) -> float:
    """Illiquid strikes with no trades today come back from Yahoo with NaN
    volume/open interest/bid rather than 0 - `value or default` doesn't catch
    that since NaN is truthy in Python."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return default if math.isnan(number) else number


def _contract_row(row: Any, spot: float, years_to_expiry: float, option_type: str) -> Dict[str, Any]:
    iv = _safe_num(row.get("impliedVolatility"))
    greeks = black_scholes_greeks(spot, _safe_num(row.get("strike")), years_to_expiry, iv, option_type)
    return {
        "contract_symbol": row.get("contractSymbol", ""),
        "strike": round(_safe_num(row.get("strike")), 2),
        "bid": round(_safe_num(row.get("bid")), 2),
        "ask": round(_safe_num(row.get("ask")), 2),
        "last_price": round(_safe_num(row.get("lastPrice")), 2),
        "volume": int(_safe_num(row.get("volume"))),
        "open_interest": int(_safe_num(row.get("openInterest"))),
        "implied_volatility": round(iv * 100, 2),
        "in_the_money": bool(row.get("inTheMoney", False)),
        **greeks,
    }


def get_full_option_chain(ticker: str, expiration: str = "") -> Dict[str, Any]:
    """Real bid/ask/volume/open-interest/IV per contract from Yahoo, paired
    calls-and-puts-by-strike like a real broker's chain view, plus Greeks
    computed via Black-Scholes from Yahoo's own IV (Yahoo doesn't provide
    Greeks directly - CBOE-style feeds that do are a paid data product)."""
    normalized = ticker.strip().upper()
    if not normalized:
        raise ValueError("Ticker is required.")

    ticker_client = yf.Ticker(normalized)
    expirations = _fetch_option_expirations_with_retry(ticker_client)
    if not expirations:
        return {
            "ticker": normalized,
            "spot_price": None,
            "expirations": [],
            "selected_expiration": "",
            "calls": [],
            "puts": [],
            "error": "No options chain available for this ticker right now.",
            "generated_at": _now_iso(),
        }

    selected_expiration = expiration if expiration in expirations else expirations[0]

    history = _fetch_history_with_retry(ticker_client, period="1d")
    spot = float(history["Close"].dropna().iloc[-1]) if not history.empty and "Close" in history.columns else None

    chain = _fetch_option_chain_with_retry(ticker_client, selected_expiration)
    if chain is None or spot is None:
        return {
            "ticker": normalized,
            "spot_price": spot,
            "expirations": expirations,
            "selected_expiration": selected_expiration,
            "calls": [],
            "puts": [],
            "error": "Options chain data unavailable for this expiration right now.",
            "generated_at": _now_iso(),
        }

    years = _years_to_expiry(selected_expiration)
    calls = [_contract_row(row, spot, years, "call") for _, row in chain.calls.iterrows()]
    puts = [_contract_row(row, spot, years, "put") for _, row in chain.puts.iterrows()]

    return {
        "ticker": normalized,
        "spot_price": round(spot, 2),
        "expirations": expirations,
        "selected_expiration": selected_expiration,
        "calls": calls,
        "puts": puts,
        "error": "",
        "generated_at": _now_iso(),
    }
