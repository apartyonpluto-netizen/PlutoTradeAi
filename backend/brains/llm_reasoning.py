from __future__ import annotations

import threading
from typing import Any, Dict, List

from news.news_service import fetch_news_bundle

try:
    import anthropic
except ImportError:  # pragma: no cover - exercised only when the optional dependency isn't installed
    anthropic = None  # type: ignore[assignment]

MODEL = "claude-sonnet-5"
MAX_NEWS_HEADLINES = 3
MAX_CONFIDENCE_ADJUSTMENT = 20

_VERDICT_TOOL = {
    "name": "submit_trade_verdict",
    "description": "Submit your judgment on whether this already-algorithmically-selected trade candidate should proceed.",
    "input_schema": {
        "type": "object",
        "properties": {
            "verdict": {
                "type": "string",
                "enum": ["approve", "veto"],
                "description": (
                    "veto only if you see a real, specific reason this setup shouldn't be traded despite "
                    "passing the technical score - not because you're generally cautious."
                ),
            },
            "confidence_adjustment": {
                "type": "integer",
                "description": (
                    "An integer from -20 to +20 to add to the technical confidence score, based on "
                    "qualitative factors the technical score can't see (news context, how convincing the "
                    "setup actually looks, anything that looks anomalous)."
                ),
            },
            "reasoning": {
                "type": "string",
                "description": "2-4 sentences explaining your verdict in plain language, referencing the specific evidence you were given.",
            },
        },
        "required": ["verdict", "confidence_adjustment", "reasoning"],
    },
}

SYSTEM_PROMPT = """You are a skeptical second-opinion trading analyst reviewing a setup that has already \
passed an algorithmic technical-analysis filter. Your job is NOT to re-approve what the algorithm already \
decided - it's to catch what pure indicator math can't see: stale or thin news being mistaken for a catalyst, \
a move that looks technically clean but is actually an obvious anomaly (single-headline pump, thin volume \
dressed up as conviction, a setup that contradicts the broader trend context you're given), or a thesis that \
doesn't actually hold together when you read it critically.

Most of the time the technical setup is genuinely fine and you should approve it - your job is not to find a \
reason to veto every trade, it's to catch the real problems when they exist. Vetoing a normal, well-supported \
setup just to seem cautious defeats the purpose of asking you to look. Reserve confidence_adjustment swings \
of more than +/-10 for cases where you have a specific, articulable reason, not a vague feeling. Never treat \
news headlines alone as sufficient grounds to approve or veto - they're context, not a signal by themselves."""


def _build_user_message(candidate: Dict[str, Any], news_headlines: List[str]) -> str:
    news_block = "\n".join(f"- {headline}" for headline in news_headlines) if news_headlines else "No recent headlines found."
    return f"""Ticker: {candidate.get('ticker')}
Strategy: {candidate.get('strategy')} (technical confidence: {candidate.get('confidence')}/99)
Recommendation: {candidate.get('recommendation')}
Entry: ${candidate.get('ideal_entry')}  Target: ${candidate.get('target')}  Stop: ${candidate.get('stop')}

Technical thesis: {candidate.get('why_ai_likes_it')}
Invalidation: {candidate.get('invalidation_rule')}
Risk note: {candidate.get('risk_warning')}

Recent headlines for {candidate.get('ticker')}:
{news_block}

Review this candidate and submit your verdict."""

# get_llm_verdict runs once per qualifying candidate per scan - a handful of
# times every 5-minute scan, all day, every trading day - and was building a
# brand-new anthropic.Anthropic(api_key=...) client on EVERY call, never
# closed or reused. That client wraps a fresh httpx.Client (connection pool,
# TLS context) and triggers the SDK's own pydantic-based request/response
# schema resolution on every instantiation. A live tracemalloc snapshot on
# the deployed service, taken in the minutes before a real OOM crash on
# 2026-08-28, showed exactly this signature - typing_extensions.py,
# annotationlib.py, pydantic/fields.py, pydantic/_internal/_mock_val_ser.py -
# as the single largest still-growing allocation site, recurring and
# enlarging on every scan. Same root shape as the Webull trade-client leak
# already fixed in integrations/webull.py's _get_trade_client (see its own
# extensive comment, and _trade_client_cache) - an API key is static per
# account, so it's safe to build the client once per key and reuse it. This
# does not change auth/network behavior, only avoids rebuilding the same
# object graph on every call.
_client_cache: Dict[str, Any] = {}
_client_cache_lock = threading.Lock()


def _get_client(api_key: str) -> Any:
    cached_client = _client_cache.get(api_key)
    if cached_client is not None:
        return cached_client

    with _client_cache_lock:
        # Re-check inside the lock - another thread may have built and
        # cached this exact key's client while we were waiting.
        cached_client = _client_cache.get(api_key)
        if cached_client is not None:
            return cached_client
        client = anthropic.Anthropic(api_key=api_key)
        _client_cache[api_key] = client
        return client


def get_llm_verdict(candidate: Dict[str, Any], api_key: str) -> Dict[str, Any]:
    """Calls Claude to review an already quant-selected trade candidate,
    returning a genuine judgment rather than templated text. Never raises -
    any failure (missing key, API error, malformed response) degrades to
    "unavailable" so the caller can fall back to the technical score alone
    rather than blocking a scan over a flaky LLM call."""
    if not api_key:
        return {"available": False, "reason": "No Anthropic API key configured."}
    if anthropic is None:
        return {"available": False, "reason": "anthropic package not installed."}

    ticker = str(candidate.get("ticker", "")).upper()
    try:
        news_bundle = fetch_news_bundle([ticker], limit=MAX_NEWS_HEADLINES)
        headlines = [str(item.get("headline", "")) for item in news_bundle.get("items", []) if item.get("headline")][
            :MAX_NEWS_HEADLINES
        ]
    except Exception:  # noqa: BLE001 - news is nice-to-have context, not required for a verdict
        headlines = []

    try:
        client = _get_client(api_key)
        response = client.messages.create(
            model=MODEL,
            max_tokens=500,
            system=SYSTEM_PROMPT,
            tools=[_VERDICT_TOOL],
            tool_choice={"type": "tool", "name": "submit_trade_verdict"},
            messages=[{"role": "user", "content": _build_user_message(candidate, headlines)}],
        )
    except Exception as error:  # noqa: BLE001 - any API failure degrades gracefully rather than blocking the scan
        return {"available": False, "reason": f"Anthropic API call failed: {error}"}

    for block in response.content:
        if getattr(block, "type", "") == "tool_use" and getattr(block, "name", "") == "submit_trade_verdict":
            verdict_input = block.input
            try:
                adjustment = int(verdict_input.get("confidence_adjustment", 0))
            except (TypeError, ValueError):
                adjustment = 0
            adjustment = max(-MAX_CONFIDENCE_ADJUSTMENT, min(MAX_CONFIDENCE_ADJUSTMENT, adjustment))
            verdict = verdict_input.get("verdict", "approve")
            return {
                "available": True,
                "verdict": verdict if verdict in ("approve", "veto") else "approve",
                "confidence_adjustment": adjustment,
                "reasoning": str(verdict_input.get("reasoning", "")),
            }

    return {"available": False, "reason": "Model did not return a structured verdict."}
