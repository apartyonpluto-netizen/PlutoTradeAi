from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Sequence


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_float(value: Any, fallback: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def build_neural_status(
    *,
    scanner_rows: Sequence[Dict[str, Any]],
    watchlist_rows: Sequence[Dict[str, Any]],
    news_items: Sequence[Dict[str, Any]],
    options_payloads: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    top_scores = [_safe_float(row.get("scanner_score", 0.0)) for row in scanner_rows[:5]]
    scanner_score = sum(top_scores) / len(top_scores) if top_scores else 0.0
    watchlist_size = len(watchlist_rows)

    bullish_news = sum(1 for item in news_items if str(item.get("sentiment", "")).lower() == "positive")
    bearish_news = sum(1 for item in news_items if str(item.get("sentiment", "")).lower() == "negative")
    news_sentiment_score = bullish_news - bearish_news

    call_votes = sum(1 for item in options_payloads if str(item.get("direction", "")).upper() == "CALL")
    put_votes = sum(1 for item in options_payloads if str(item.get("direction", "")).upper() == "PUT")
    options_bias = call_votes - put_votes

    trend_signal = 1 if scanner_score >= 65 else -1 if scanner_score <= 40 else 0
    volume_signal = 1 if any(_safe_float(row.get("relative_volume", 0)) >= 1.5 for row in scanner_rows[:6]) else 0
    combined_signal = trend_signal + volume_signal + (1 if news_sentiment_score > 0 else -1 if news_sentiment_score < 0 else 0)
    combined_signal += 1 if options_bias > 0 else -1 if options_bias < 0 else 0

    if combined_signal >= 2:
        bias = "BULLISH"
    elif combined_signal <= -2:
        bias = "BEARISH"
    else:
        bias = "NEUTRAL"

    confidence_score = int(max(10, min(95, 40 + scanner_score * 0.35 + abs(combined_signal) * 8)))

    bullish_factors: List[str] = []
    bearish_factors: List[str] = []
    risk_flags: List[str] = []

    if scanner_score >= 70:
        bullish_factors.append(f"Scanner strength elevated ({scanner_score:.0f}).")
    if trend_signal > 0:
        bullish_factors.append("Trend placeholder indicates upward bias.")
    if volume_signal > 0:
        bullish_factors.append("Volume placeholder indicates participation.")
    if news_sentiment_score > 0:
        bullish_factors.append("News sentiment placeholder leans positive.")
    if options_bias > 0:
        bullish_factors.append("Options bias placeholder leans CALL.")

    if scanner_score <= 50:
        bearish_factors.append(f"Scanner strength muted ({scanner_score:.0f}).")
    if trend_signal < 0:
        bearish_factors.append("Trend placeholder indicates downside pressure.")
    if news_sentiment_score < 0:
        bearish_factors.append("News sentiment placeholder leans negative.")
    if options_bias < 0:
        bearish_factors.append("Options bias placeholder leans PUT.")

    if watchlist_size == 0:
        risk_flags.append("Watchlist empty; confidence reduced.")
    if not options_payloads:
        risk_flags.append("Options data unavailable for neural fusion.")
    if not news_items:
        risk_flags.append("News feed unavailable; sentiment placeholder only.")
    risk_flags.append("Live trading disabled by safety policy.")

    if confidence_score >= 75 and bias == "BULLISH":
        final_decision = "PAPER_TRADE"
    elif confidence_score >= 70 and bias == "BEARISH":
        final_decision = "REVIEW"
    elif confidence_score < 45:
        final_decision = "WAIT"
    else:
        final_decision = "WATCH"

    return {
        "confidence_score": confidence_score,
        "bias": bias,
        "bullish_factors": bullish_factors or ["No confirmed bullish drivers yet."],
        "bearish_factors": bearish_factors or ["No confirmed bearish drivers yet."],
        "risk_flags": risk_flags,
        "final_decision": final_decision,
        "inputs": {
            "scanner_score": round(scanner_score, 2),
            "watchlist_size": watchlist_size,
            "trend_signal_placeholder": trend_signal,
            "volume_signal_placeholder": volume_signal,
            "news_sentiment_placeholder": news_sentiment_score,
            "options_bias_placeholder": options_bias,
        },
        "execution": {
            "live_trading_enabled": False,
            "options_execution_enabled": False,
            "approval_required": True,
        },
        "generated_at": _now_iso(),
    }

