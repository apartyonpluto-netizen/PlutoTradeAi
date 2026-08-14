from __future__ import annotations

"""Leading market-regime SHADOW indicator (VIX) for the autonomous scan.

STATUS: unvalidated research hypothesis, SHADOW MODE ONLY. Per explicit
review (this module previously had live veto power over new entries; that
was removed - see REGIME_MAPPING_VERSION and app.py's own shadow-block
comment), NOTHING in this module may influence which trades are placed,
their size, or their protection. It only computes and records what a
VIX-based confidence adjustment WOULD have proposed, so that once real
closed-trade outcomes exist (see autonomy/closed_trades.py) the mapping
below can be checked out-of-sample before any future promotion to a
live, decision-affecting signal is even considered.

Mirrors brains/llm_reasoning.py's {available, adjustment, reasoning} shape
where it overlaps, but adds explicit source/fetch timestamps and 3-way
staleness handling that a same-call LLM verdict doesn't need - a VIX quote
can be legitimately fresh, legitimately stale (served from a fallback
cache after a failed fetch), or genuinely unusable (never fetched, a
corrupt/future timestamp, or simply too old - e.g. the market is closed
and ^VIX's 5-minute bars stopped advancing hours ago). This module refuses
to collapse those into one "available" bit.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Optional

import yfinance as yf

VIX_TICKER = "^VIX"

# Bump whenever the threshold table below changes, so every shadow record
# stays attributable to the exact mapping that produced it even after the
# table is later revised. The "_unvalidated" suffix is deliberate and part
# of the identifier, not just a comment - this mapping has NOT been
# checked against this app's own closed-trade history (unlike, say,
# calibration.py's per-strategy multiplier, which is backtested). It must
# earn activation through out-of-sample backtesting and shadow-trading
# evidence before ever being wired into a real decision.
REGIME_MAPPING_VERSION = "vix_shadow_v1_unvalidated"

# How often this module will hit yfinance for a fresh quote - a
# performance/rate-limit control only, NOT a statement about how old the
# returned quote's own data is (see MAX_USABLE_AGE_SECONDS for that). A
# normal reuse within this window is NOT "stale cache" - see
# get_vix_snapshot's used_stale_cache semantics below.
REFETCH_INTERVAL_SECONDS = 300

# The quote's OWN age (fetch time minus its real source-bar timestamp, not
# minus when this module last talked to yfinance) beyond which it is no
# longer usable at all, regardless of how it was obtained. This is what
# actually protects against a market-closed or broken-feed VIX reading
# being silently treated as current: outside market hours ^VIX's 5-minute
# bars stop advancing, so age_seconds grows past this threshold on its
# own with no separate market-calendar logic required - deliberately, per
# this app's established "don't assume what isn't verified" discipline
# (there is no verified market-calendar component here to lean on).
MAX_USABLE_AGE_SECONDS = 900  # 15 minutes

# VIX level -> PROPOSED confidence adjustment, in points, on the SAME
# 0-99 scale opp["confidence"] uses. Monotonically non-positive (never
# proposes rewarding a calm reading) - this app's autonomous strategies
# are long/CALL-only (see brains/strategy_brain.py), so elevated
# volatility is the regime an unsupervised long-only system is most
# exposed in; a low VIX reading is not verified to predict anything here.
_VIX_ADJUSTMENT_THRESHOLDS = (
    (30.0, -15),  # extreme fear / crisis-level volatility
    (25.0, -10),  # high fear
    (20.0, -5),  # mildly elevated
)

_QUOTE_CACHE: Dict[str, object] = {"quote": None, "cached_at": None}


@dataclass
class _VixQuote:
    level: float
    source_time: datetime  # timestamp of the underlying price bar itself
    fetch_time: datetime  # when this module actually hit yfinance for it


def _fetch_vix_quote() -> Optional[_VixQuote]:
    """Direct yfinance fetch - deliberately bypasses
    market_scanner.scan_market, whose own "last_updated" field is stamped
    at fetch time rather than the underlying bar's real timestamp, which
    would collapse exactly the source-vs-fetch distinction this module
    needs to stay honest about staleness. Returns None on ANY failure -
    never fabricates a quote or a timestamp."""
    try:
        history = yf.Ticker(VIX_TICKER).history(period="1d", interval="5m")
    except Exception:  # noqa: BLE001 - yfinance/network failures are expected occasionally
        return None
    if history is None or history.empty or "Close" not in history.columns:
        return None
    try:
        level = float(history["Close"].iloc[-1])
        source_index = history.index[-1]
        source_time = source_index.to_pydatetime()
    except (IndexError, TypeError, ValueError):
        return None

    if source_time.tzinfo is None:
        source_time = source_time.replace(tzinfo=timezone.utc)
    else:
        source_time = source_time.astimezone(timezone.utc)

    return _VixQuote(level=level, source_time=source_time, fetch_time=datetime.now(timezone.utc))


def get_vix_snapshot(force_refresh: bool = False) -> Dict[str, object]:
    """The single entry point for this module. Returns a fully
    self-describing snapshot rather than a bare float, because a bare
    number can't say when it's from or how it was obtained - both are
    required fields on the shadow record this feeds.

    Returns a dict with:
        vix_level          float or None. None whenever status is
                            "unavailable" for lack of any usable quote or
                            a corrupt/future timestamp - NOT suppressed
                            merely for being past MAX_USABLE_AGE_SECONDS
                            (that case keeps the number for audit, but
                            status="unavailable" still means "do not use
                            this as a signal").
        source_time         datetime (UTC) of the underlying quote/bar, or
                            None if no quote was ever obtained.
        fetch_time          datetime (UTC) this snapshot was produced -
                            always set.
        age_seconds         fetch_time - source_time in seconds, or None
                            if source_time is unknown or would be negative
                            (see the future-timestamp guard below).
        status              "fresh" - a quote exists, was not served from
                                a failed-fetch fallback, and its own
                                source age is within MAX_USABLE_AGE_SECONDS.
                            "stale" - a quote exists (served from a
                                failed-fetch fallback cache) and its
                                source age is still within
                                MAX_USABLE_AGE_SECONDS.
                            "unavailable" - no usable quote at all (never
                                fetched, a future/corrupt source
                                timestamp), OR the quote's source age
                                exceeds MAX_USABLE_AGE_SECONDS.
        used_stale_cache    True only when THIS call's own fetch attempt
                            failed (or was skipped because none has ever
                            succeeded) and a previously fetched quote was
                            served instead - NOT true for the normal fast
                            path of reusing a quote fetched less than
                            REFETCH_INTERVAL_SECONDS ago, which is
                            ordinary caching, not a fallback.
    """
    now = datetime.now(timezone.utc)
    cached_quote: Optional[_VixQuote] = _QUOTE_CACHE.get("quote")  # type: ignore[assignment]
    cached_at = _QUOTE_CACHE.get("cached_at")

    used_stale_cache = False
    should_refetch = force_refresh or not isinstance(cached_at, datetime) or (now - cached_at).total_seconds() >= REFETCH_INTERVAL_SECONDS

    if should_refetch:
        quote = _fetch_vix_quote()
        if quote is not None:
            _QUOTE_CACHE["quote"] = quote
            _QUOTE_CACHE["cached_at"] = now
        elif cached_quote is not None:
            # This round's fetch failed (or force_refresh asked for one
            # and it still failed) - fall back to the last known quote,
            # but this IS a stale-cache use and must be labeled as such,
            # never silently treated as equivalent to a live read.
            quote = cached_quote
            used_stale_cache = True
    else:
        quote = cached_quote  # ordinary reuse within the refetch window - not a fallback

    if quote is None:
        return {
            "vix_level": None,
            "source_time": None,
            "fetch_time": now,
            "age_seconds": None,
            "status": "unavailable",
            "used_stale_cache": used_stale_cache,
        }

    age_seconds = (now - quote.source_time).total_seconds()
    if age_seconds < 0:
        # Source timestamp in the future relative to now - clock skew or a
        # malformed bar index. Never trust it as fresh; treat as though no
        # quote existed rather than guessing at a "corrected" age.
        return {
            "vix_level": None,
            "source_time": quote.source_time,
            "fetch_time": now,
            "age_seconds": None,
            "status": "unavailable",
            "used_stale_cache": used_stale_cache,
        }

    if age_seconds > MAX_USABLE_AGE_SECONDS:
        # Beyond the maximum usable age (e.g. market closed, or a stuck
        # feed) - unavailable regardless of how it was obtained. The level
        # itself is still returned for audit/debugging visibility, but
        # status="unavailable" means callers must not use it as a signal -
        # compute_shadow_adjustment enforces a hard zero here.
        return {
            "vix_level": quote.level,
            "source_time": quote.source_time,
            "fetch_time": now,
            "age_seconds": round(age_seconds, 1),
            "status": "unavailable",
            "used_stale_cache": used_stale_cache,
        }

    status = "stale" if used_stale_cache else "fresh"
    return {
        "vix_level": quote.level,
        "source_time": quote.source_time,
        "fetch_time": now,
        "age_seconds": round(age_seconds, 1),
        "status": status,
        "used_stale_cache": used_stale_cache,
    }


def compute_shadow_adjustment(snapshot: Dict[str, object]) -> Dict[str, object]:
    """Maps a VIX snapshot (see get_vix_snapshot) to a PROPOSED confidence
    adjustment - proposed only. Nothing in this module is read by the live
    order-placement decision; see app.py's shadow-computation call site
    for the structural guarantee.

    status == "unavailable" always forces a hard zero adjustment, per
    explicit requirement: a stale-fallback or missing quote must never be
    treated as a usable signal, even in shadow form. status == "stale"
    (within the usable age window but served from a failed-fetch fallback)
    still gets a genuine proposed adjustment - clearly labeled via the
    snapshot's own status field - so later backtesting can decide whether
    to discount or exclude stale-sourced shadow records rather than that
    judgment call being silently baked in here."""
    status = snapshot.get("status")
    vix_level = snapshot.get("vix_level")

    if status == "unavailable" or vix_level is None:
        return {
            "mapping_version": REGIME_MAPPING_VERSION,
            "proposed_adjustment": 0,
            "reasoning": "VIX unavailable or unusably stale this scan - proposed adjustment forced to 0.",
        }

    adjustment = 0
    for threshold, value in _VIX_ADJUSTMENT_THRESHOLDS:
        if vix_level >= threshold:
            adjustment = value
            break

    staleness_note = " (served from a stale fallback cache)" if status == "stale" else ""
    if adjustment == 0:
        reasoning = f"VIX at {vix_level:.1f}{staleness_note} - within normal range, no adjustment proposed."
    else:
        reasoning = (
            f"VIX at {vix_level:.1f}{staleness_note} - elevated volatility regime, proposed adjustment "
            f"{adjustment:+d} points for this long-only setup (unvalidated hypothesis, shadow mode only)."
        )

    return {"mapping_version": REGIME_MAPPING_VERSION, "proposed_adjustment": adjustment, "reasoning": reasoning}
