from __future__ import annotations

"""Cross-checks the real-time price this app is about to trade on against
a SECOND, independent source before submitting a real order - added
2026-09-11 as part of the "memory, real outcomes, multi-source data"
push toward live-trading trust. Modeled on news/news_service.py's
NewsService/ProviderResult combine-and-report shape: query every
provider, report per-provider status, and only change behavior when
providers genuinely disagree.

Why Webull's own quote, not a third-party vendor (Finnhub etc.): the
question that actually matters here isn't "is some external feed
accurate in the abstract," it's "does the broker we are about to submit
this order to agree with the price we computed" - and the Webull Python
SDK already vendored in this project exposes exactly that
(integrations/webull.py's get_equity_snapshot, added alongside this
file, mirrors the options-side get_option_snapshot this app already
calls live). No new account or API key needed, and it directly targets
the concrete failure mode already found this session (MU pricing at
$1012 from a stale/wrong feed).

Scope (deliberately limited): only wired into the pre-submission price-
drift check in app.py's _run_autonomous_trade_scan_locked - the one read
where a wrong price can directly cause a real, immediate order
consequence. NOT wired into brains/strategy_brain.py's real-time read
(the "confidence-engine" price used to score/rank candidates before any
order is even being considered): that call runs for up to 6 tickers on
every 5-minute scan tick regardless of user, doubling Webull sandbox
call volume for a read whose only consequence is which candidates get
looked at, not whether real money moves - a materially different risk/
cost trade-off. Revisit only with real evidence this narrower scope
isn't enough, matching this codebase's established discipline against
wiring in more signal sources on faith alone.

Fail-closed discipline, matching every other market-data read in this
app: Alpaca unavailable -> price is None, exactly today's behavior
(nothing here changes that path). Webull unavailable/errors while
Alpaca succeeds -> NOT treated as disagreement; falls back to Alpaca's
price alone, exactly today's behavior - a missing second opinion is not
evidence the first one is wrong, and this cross-check must only ever
make the system MORE conservative on a genuine conflict, never LESS
available because the extra check itself failed. Both available and
disagree beyond tolerance -> price is None and disagreement=True, so the
caller can attribute a distinct skip_category ("price_sources_disagree")
instead of folding it into the generic "could not confirm a fresh
price" reason."""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from . import alpaca_data
from . import webull as webull_api

# Both readings are taken back-to-back, effectively the same instant, from
# two real-time-ish feeds (Alpaca's IEX feed vs Webull's own) - genuine
# disagreement beyond ordinary bid/ask noise between two live feeds should
# be small. Deliberately tighter than _MAX_ENTRY_PRICE_DRIFT_PERCENT in
# app.py (2.0%), which bounds drift across TIME (scan-time vs
# pre-submission); this bounds disagreement between two sources read at
# the SAME time - if this proves too tight/loose against real Webull
# snapshots once the response shape is confirmed live, tune this constant,
# not the comparison logic.
PRICE_SOURCE_DISAGREEMENT_TOLERANCE_PERCENT = 1.5


@dataclass
class PriceProviderResult:
    provider: str
    price: Optional[float]
    error: Optional[str]


def _alpaca_price(ticker: str) -> PriceProviderResult:
    # get_latest_trade_price already never raises - returns None on any
    # failure (see its own docstring) - no try/except needed here.
    price = alpaca_data.get_latest_trade_price(ticker)
    if price is None:
        return PriceProviderResult(provider="alpaca", price=None, error="no price returned")
    return PriceProviderResult(provider="alpaca", price=float(price), error=None)


def _extract_last_price(snapshot_item: Dict[str, Any]) -> Optional[float]:
    """See get_equity_snapshot's own docstring: "price" is inferred by
    analogy to the confirmed-live option snapshot's field name, not yet
    independently confirmed for equities. Deliberately checks ONLY this
    one key rather than guessing across several candidates - a wrong
    field name here must surface as "Webull has no price" (safe: falls
    back to Alpaca alone), not as a confident read of the wrong field."""
    raw_price = snapshot_item.get("price")
    if raw_price is None:
        return None
    try:
        return float(raw_price)
    except (TypeError, ValueError):
        return None


def _webull_price(creds: Optional[Dict[str, str]], ticker: str) -> PriceProviderResult:
    if not creds or not creds.get("app_key") or not creds.get("app_secret"):
        return PriceProviderResult(provider="webull", price=None, error="Webull credentials not configured")
    try:
        snapshot = webull_api.get_equity_snapshot(creds["app_key"], creds["app_secret"], [ticker])
    except Exception as error:  # noqa: BLE001 - a cross-check read must never raise into the real scan
        return PriceProviderResult(provider="webull", price=None, error=str(error))
    if not snapshot:
        return PriceProviderResult(provider="webull", price=None, error="empty snapshot response")
    price = _extract_last_price(snapshot[0])
    if price is None:
        return PriceProviderResult(provider="webull", price=None, error="no usable price field in snapshot response")
    return PriceProviderResult(provider="webull", price=price, error=None)


def get_cross_checked_price(
    ticker: str, creds: Optional[Dict[str, str]], tolerance_percent: float = PRICE_SOURCE_DISAGREEMENT_TOLERANCE_PERCENT,
) -> Dict[str, Any]:
    """Returns:
        {
            "price": float | None,       # the value the caller should use; None means "could not confirm"
            "disagreement": bool,        # True only when BOTH sources returned a price and they differ beyond tolerance
            "provider_status": [ {"provider": ..., "price": ..., "error": ...}, ... ],
        }

    Alpaca's own price is always what gets returned on success (matching
    every other read in this codebase, which already treats Alpaca as the
    real-time source of record) - Webull's reading is used only to CONFIRM
    or REJECT it, never to replace it."""
    alpaca_result = _alpaca_price(ticker)
    webull_result = _webull_price(creds, ticker)
    provider_status = [
        {"provider": alpaca_result.provider, "price": alpaca_result.price, "error": alpaca_result.error},
        {"provider": webull_result.provider, "price": webull_result.price, "error": webull_result.error},
    ]

    if alpaca_result.price is None:
        return {"price": None, "disagreement": False, "provider_status": provider_status}

    if webull_result.price is None:
        # Second opinion unavailable - fall back to Alpaca alone, exactly
        # today's behavior. Not evidence of a wrong price.
        return {"price": alpaca_result.price, "disagreement": False, "provider_status": provider_status}

    deviation_percent = abs(webull_result.price - alpaca_result.price) / alpaca_result.price * 100
    if deviation_percent > tolerance_percent:
        return {"price": None, "disagreement": True, "provider_status": provider_status}

    return {"price": alpaca_result.price, "disagreement": False, "provider_status": provider_status}
