from __future__ import annotations

import math
from typing import Dict

# No real-time treasury yield feed wired up - 5% is a reasonable stand-in for
# the short-dated risk-free rate and only mildly affects the Greeks anyway,
# far less than IV does. Revisit if precision here ever matters more.
RISK_FREE_RATE = 0.05


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def black_scholes_greeks(
    spot: float,
    strike: float,
    years_to_expiry: float,
    implied_volatility: float,
    option_type: str,
) -> Dict[str, float]:
    """Standard Black-Scholes Greeks. yfinance's option chain gives real bid/
    ask/volume/open interest but not Greeks - this computes them from the IV
    Yahoo already provides per-contract, the same input a real Greeks feed
    would use."""
    option_type = option_type.strip().lower()
    if spot <= 0 or strike <= 0 or years_to_expiry <= 0 or implied_volatility <= 0:
        return {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}

    sqrt_t = math.sqrt(years_to_expiry)
    d1 = (math.log(spot / strike) + (RISK_FREE_RATE + 0.5 * implied_volatility**2) * years_to_expiry) / (
        implied_volatility * sqrt_t
    )
    d2 = d1 - implied_volatility * sqrt_t

    gamma = _norm_pdf(d1) / (spot * implied_volatility * sqrt_t)
    vega = spot * _norm_pdf(d1) * sqrt_t / 100  # per 1% IV move

    if option_type == "call":
        delta = _norm_cdf(d1)
        theta = (
            -(spot * _norm_pdf(d1) * implied_volatility) / (2 * sqrt_t)
            - RISK_FREE_RATE * strike * math.exp(-RISK_FREE_RATE * years_to_expiry) * _norm_cdf(d2)
        ) / 365
    else:
        delta = _norm_cdf(d1) - 1
        theta = (
            -(spot * _norm_pdf(d1) * implied_volatility) / (2 * sqrt_t)
            + RISK_FREE_RATE * strike * math.exp(-RISK_FREE_RATE * years_to_expiry) * _norm_cdf(-d2)
        ) / 365

    return {"delta": round(delta, 4), "gamma": round(gamma, 4), "theta": round(theta, 4), "vega": round(vega, 4)}
