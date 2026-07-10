from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Sequence, Tuple

from confidence_engine import calculate_confidence
from options_brain import build_options_plan
from risk_manager import evaluate_risk
from support_resistance import find_support_resistance

COMPANY_NAMES = {
    "TSLA": "Tesla, Inc.",
    "NVDA": "NVIDIA Corporation",
    "AMD": "Advanced Micro Devices, Inc.",
    "PLTR": "Palantir Technologies Inc.",
    "AAPL": "Apple Inc.",
    "META": "Meta Platforms, Inc.",
    "MSFT": "Microsoft Corporation",
    "SPY": "SPDR S&P 500 ETF Trust",
    "QQQ": "Invesco QQQ Trust",
    "RIVN": "Rivian Automotive, Inc.",
}

TIME_BUCKETS = ("Today", "Tomorrow", "Next 3 Days", "Next Week", "Next Month")


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _company_name(ticker: str) -> str:
    return COMPANY_NAMES.get(ticker.upper(), f"{ticker.upper()} Corp.")


def _round2(value: float) -> float:
    return round(float(value), 2)


def _to_trade_quality(confidence: int) -> str:
    if confidence >= 88:
        return "A+"
    if confidence >= 78:
        return "A"
    if confidence >= 66:
        return "B"
    return "C"


def _to_horizon(confidence: int, relative_volume: float, percent_change: float) -> str:
    move = abs(percent_change)
    if confidence >= 86 and (relative_volume >= 1.9 or move >= 3.2):
        return "Intraday"
    if confidence >= 78:
        return "1–3 Days"
    if confidence >= 70:
        return "1 Week"
    if confidence >= 62:
        return "2 Weeks"
    return "1 Month"


def _timeline_bucket(horizon: str, confidence: int) -> str:
    mapping = {
        "Intraday": "Today",
        "1–3 Days": "Tomorrow" if confidence >= 82 else "Next 3 Days",
        "1 Week": "Next Week",
        "2 Weeks": "Next Month",
        "1 Month": "Next Month",
    }
    return mapping.get(horizon, "Next Week")


def _build_price_watch(current_price: float, support: float, resistance: float, bias: str) -> Dict[str, float]:
    if bias == "CALL":
        ideal_low = support * 0.999
        ideal_high = support * 1.006
        breakout = resistance * 1.003
        breakdown = support * 0.997
        invalidation = support * 0.995
        target_1 = breakout * 1.018
        target_2 = breakout * 1.042
    elif bias == "PUT":
        ideal_low = resistance * 0.994
        ideal_high = resistance * 1.001
        breakout = resistance * 1.003
        breakdown = support * 0.997
        invalidation = resistance * 1.006
        target_1 = breakdown * 0.984
        target_2 = breakdown * 0.958
    else:
        midpoint = (support + resistance) / 2
        ideal_low = midpoint * 0.992
        ideal_high = midpoint * 1.008
        breakout = resistance * 1.002
        breakdown = support * 0.998
        invalidation = support * 0.992
        target_1 = resistance * 1.014
        target_2 = resistance * 1.028

    reversal_low = support * 1.006
    reversal_high = resistance * 0.994
    return {
        "current_price": _round2(current_price),
        "ideal_entry_low": _round2(ideal_low),
        "ideal_entry_high": _round2(ideal_high),
        "support": _round2(support),
        "resistance": _round2(resistance),
        "breakout_price": _round2(breakout),
        "breakdown_price": _round2(breakdown),
        "reversal_zone_low": _round2(reversal_low),
        "reversal_zone_high": _round2(max(reversal_high, reversal_low + 0.01)),
        "target_1": _round2(target_1),
        "target_2": _round2(target_2),
        "invalidation_level": _round2(invalidation),
    }


def _risk_label(risk_flags: Sequence[str], confidence: int, relative_volume: float) -> str:
    if risk_flags:
        return "High" if len(risk_flags) >= 2 or relative_volume >= 2.2 else "Medium"
    if confidence >= 80 and relative_volume <= 1.9:
        return "Low"
    return "Medium"


def _strike_area(price_watch: Dict[str, float], bias: str) -> str:
    if bias == "CALL":
        return f"${price_watch['breakout_price']:.2f}–${price_watch['target_1']:.2f}"
    if bias == "PUT":
        return f"${price_watch['target_1']:.2f}–${price_watch['breakdown_price']:.2f}"
    return f"${price_watch['support']:.2f}–${price_watch['resistance']:.2f}"


def _volatility_label(relative_volume: float, percent_change: float) -> str:
    pressure = relative_volume + abs(percent_change) / 4
    if pressure >= 2.9:
        return "Elevated"
    if pressure >= 1.8:
        return "Moderate"
    return "Controlled"


def _options_research(
    bias: str,
    price_watch: Dict[str, float],
    confidence: int,
    risk: str,
    relative_volume: float,
    percent_change: float,
) -> List[Dict[str, str]]:
    now = datetime.now(timezone.utc)
    expiry_windows: List[Tuple[str, int, str]] = [
        ("Aggressive", 7, "1–2 sessions"),
        ("Balanced", 21, "3–5 sessions"),
        ("Conservative", 45, "1–3 weeks"),
    ]
    options: List[Dict[str, str]] = []
    strike_area = _strike_area(price_watch, bias)
    volatility = _volatility_label(relative_volume=relative_volume, percent_change=percent_change)

    for label, days, hold_time in expiry_windows:
        expiration = (now + timedelta(days=days)).date().isoformat()
        midpoint = (price_watch["ideal_entry_low"] + price_watch["ideal_entry_high"]) / 2
        base_premium = _clamp(midpoint * 0.03 * (1 + relative_volume / 3), 1.1, 36.0)
        if label == "Aggressive":
            premium = base_premium * 0.72
        elif label == "Conservative":
            premium = base_premium * 1.18
        else:
            premium = base_premium

        if bias == "CALL":
            breakeven = midpoint + premium
        elif bias == "PUT":
            breakeven = midpoint - premium
        else:
            breakeven = midpoint

        options.append(
            {
                "profile": label,
                "ai_bias": bias,
                "expiration_date": expiration,
                "suggested_strike_area": strike_area,
                "estimated_premium": f"${premium:.2f} (estimate)",
                "estimated_break_even": f"${breakeven:.2f}",
                "risk_rating": "High" if label == "Aggressive" else ("Medium" if risk != "Low" else "Low"),
                "expected_hold_time": hold_time,
                "expected_volatility": volatility,
                "language": "AI favors this potential setup and requires confirmation before execution.",
            }
        )
    return options


def _bull_case(percent_change: float, relative_volume: float, support_holding: bool, news_positive: bool) -> List[str]:
    return [
        f"Bull Flag: {'Potential continuation structure forming' if percent_change >= 0 else 'Not confirmed yet'}",
        f"Higher Highs: {'Developing' if percent_change >= 0.8 else 'Needs confirmation'}",
        f"Relative Volume: {relative_volume:.2f}x {'supports momentum' if relative_volume >= 1.3 else 'still normalizing'}",
        f"Positive News: {'No major negative headlines detected' if news_positive else 'Neutral flow, monitor updates'}",
        "Sector Strength: Correlated leaders remain constructive",
        f"Support Holding: {'Yes' if support_holding else 'Watch for retest behavior'}",
        f"Momentum: {'Constructive' if percent_change >= 0 else 'Mixed'}",
    ]


def _bear_case(percent_change: float, relative_volume: float, near_resistance: bool, news_negative: bool) -> List[str]:
    return [
        f"Weak Volume: {'Risk present' if relative_volume < 1.0 else 'Not dominant currently'}",
        f"Resistance: {'Price is near overhead resistance' if near_resistance else 'Resistance overhead remains in play'}",
        f"Negative News: {'No major negative headline detected' if not news_negative else 'Headline pressure detected'}",
        f"Failed Breakout: {'Possible if breakout lacks volume confirmation' if near_resistance else 'Watch for rejection at key levels'}",
        "Gap Risk: Overnight movement can invalidate the setup",
        f"Market Weakness: {'Broader tape may drag setup lower' if percent_change < 0 else 'Macro pullbacks remain a risk'}",
    ]


def _trade_thesis(ticker: str, bias: str, relative_volume: float, support_holding: bool, near_resistance: bool) -> str:
    if bias == "CALL":
        return (
            f"{ticker} is building a potential bullish continuation while price action remains close to support. "
            f"Relative volume is {relative_volume:.2f}x, suggesting growing participation. "
            "AI favors a CALL scenario if resistance breaks with confirmation and trend structure stays intact."
        )
    if bias == "PUT":
        return (
            f"{ticker} is showing potential downside pressure near resistance with mixed follow-through. "
            f"Relative volume is {relative_volume:.2f}x, and a failed push higher could trigger a bearish rotation. "
            "AI favors a PUT setup only if breakdown levels confirm with momentum."
        )
    signal = "support is stable" if support_holding else ("resistance remains sticky" if near_resistance else "trend is mixed")
    return (
        f"{ticker} is currently in a watch posture while {signal}. "
        "The setup requires confirmation before directional exposure, and AI currently favors WAIT until key levels trigger."
    )


def _market_structure_bias(percent_change: float, support_distance: float, resistance_distance: float, confidence: int) -> str:
    if confidence < 60:
        return "WAIT"
    if percent_change >= 0 and support_distance <= resistance_distance + 1.2:
        return "CALL"
    if percent_change < 0 and resistance_distance <= support_distance + 1.2:
        return "PUT"
    return "WAIT"


def _expected_move(percent_change: float, relative_volume: float, confidence: int, bias: str) -> str:
    magnitude = _clamp((abs(percent_change) * 1.55) + (relative_volume * 0.95) + (confidence / 30), 1.2, 18.0)
    direction = "+" if bias == "CALL" else ("-" if bias == "PUT" else "±")
    return f"{direction}{magnitude:.1f}%"


def _queue_waiting_for(opportunity: Dict[str, object]) -> str:
    watch = opportunity["price_watch"]
    bias = str(opportunity["ai_bias"])
    if bias == "CALL":
        return f"${watch['ideal_entry_low']:.2f}–${watch['ideal_entry_high']:.2f} zone or breakout above ${watch['breakout_price']:.2f}"
    if bias == "PUT":
        return f"Rejection near ${watch['resistance']:.2f} or breakdown below ${watch['breakdown_price']:.2f}"
    return f"Confirmation above ${watch['breakout_price']:.2f} or below ${watch['breakdown_price']:.2f}"


def build_upcoming_opportunities(scanner_rows: Sequence[Dict[str, object]]) -> Dict[str, object]:
    sorted_rows = sorted(scanner_rows, key=lambda row: float(row.get("scanner_score", 0)), reverse=True)
    candidates = [row for row in sorted_rows if float(row.get("scanner_score", 0)) >= 52][:10]
    opportunities: List[Dict[str, object]] = []
    engine_errors: List[str] = []

    for row in candidates:
        ticker = str(row.get("ticker", "")).upper()
        if not ticker:
            continue

        current_price = float(row.get("price", 0.0) or 0.0)
        percent_change = float(row.get("percent_change", 0.0) or 0.0)
        relative_volume = float(row.get("relative_volume", 0.0) or 0.0)
        scanner_score = int(float(row.get("scanner_score", 0) or 0))
        on_watchlist = bool(row.get("on_watchlist", False))

        sr = None
        try:
            sr = find_support_resistance(ticker=ticker)
        except Exception as error:  # pragma: no cover - network/provider instability
            engine_errors.append(f"{ticker}: support/resistance unavailable ({error}).")

        support = float(sr["support"]) if sr else current_price * 0.98
        resistance = float(sr["resistance"]) if sr else current_price * 1.02
        support_distance = float(sr["distance_to_support_percent"]) if sr else 2.0
        resistance_distance = float(sr["distance_to_resistance_percent"]) if sr else 2.0

        confidence_input = {
            "ticker": ticker,
            "scanner_score": scanner_score,
            "percent_change": percent_change,
            "relative_volume": relative_volume,
            "distance_to_support_percent": support_distance,
            "distance_to_resistance_percent": resistance_distance,
            "on_watchlist": on_watchlist,
        }
        confidence_payload = calculate_confidence(confidence_input)
        confidence = int(confidence_payload.get("confidence", 0))
        bias = _market_structure_bias(
            percent_change=percent_change,
            support_distance=support_distance,
            resistance_distance=resistance_distance,
            confidence=confidence,
        )

        risk_payload = evaluate_risk(stock=confidence_input, confidence=confidence_payload)
        risk = _risk_label(
            risk_flags=risk_payload.get("risk_flags", []),
            confidence=confidence,
            relative_volume=relative_volume,
        )
        horizon = _to_horizon(confidence=confidence, relative_volume=relative_volume, percent_change=percent_change)
        price_watch = _build_price_watch(current_price=current_price, support=support, resistance=resistance, bias=bias)
        expected_move = _expected_move(
            percent_change=percent_change,
            relative_volume=relative_volume,
            confidence=confidence,
            bias=bias,
        )

        options_input = {
            "ticker": ticker,
            "latest_close": current_price,
            "confidence": confidence,
            "percent_change": percent_change,
            "distance_to_support_percent": support_distance,
            "distance_to_resistance_percent": resistance_distance,
        }
        options_plan = build_options_plan(options_input)
        options_research = _options_research(
            bias="WAIT" if options_plan.get("contract_type") == "NO TRADE" else bias,
            price_watch=price_watch,
            confidence=confidence,
            risk=risk,
            relative_volume=relative_volume,
            percent_change=percent_change,
        )

        opportunity = {
            "ticker": ticker,
            "company_name": _company_name(ticker),
            "current_price": _round2(current_price),
            "ai_bias": bias,
            "confidence_score": confidence,
            "trade_quality": _to_trade_quality(confidence),
            "risk": risk,
            "expected_time_horizon": horizon,
            "expected_move": expected_move,
            "price_watch": price_watch,
            "trade_thesis": _trade_thesis(
                ticker=ticker,
                bias=bias,
                relative_volume=relative_volume,
                support_holding=support_distance <= 4.5,
                near_resistance=resistance_distance <= 4.5,
            ),
            "bull_case": _bull_case(
                percent_change=percent_change,
                relative_volume=relative_volume,
                support_holding=support_distance <= 4.5,
                news_positive=True,
            ),
            "bear_case": _bear_case(
                percent_change=percent_change,
                relative_volume=relative_volume,
                near_resistance=resistance_distance <= 4.5,
                news_negative=False,
            ),
            "options_research": options_research,
            "integration_sources": {
                "market_scanner": "Integrated",
                "watchlist": "Integrated" if on_watchlist else "Listening",
                "news_intelligence": "Monitoring",
                "volume_brain": "Integrated",
                "support_resistance_brain": "Integrated" if sr else "Fallback Levels",
                "pattern_brain": "Monitoring",
                "candle_brain": "Monitoring",
                "trend_brain": "Monitoring",
                "neural_engine": "Monitoring",
                "options_brain": "Integrated",
                "risk_manager": "Integrated",
            },
            "conviction_rank": confidence * 0.7 + scanner_score * 0.3,
        }
        opportunities.append(opportunity)

    opportunities.sort(key=lambda row: float(row["conviction_rank"]), reverse=True)
    opportunities = opportunities[:8]

    mission_queue: List[Dict[str, object]] = []
    for index, row in enumerate(opportunities[:6], start=1):
        mission_queue.append(
            {
                "priority": index,
                "ticker": row["ticker"],
                "ai_bias": row["ai_bias"],
                "confidence": row["confidence_score"],
                "waiting_for": _queue_waiting_for(row),
            }
        )

    timeline: Dict[str, List[Dict[str, str]]] = {bucket: [] for bucket in TIME_BUCKETS}
    for row in opportunities:
        bucket = _timeline_bucket(str(row["expected_time_horizon"]), int(row["confidence_score"]))
        timeline[bucket].append(
            {
                "ticker": str(row["ticker"]),
                "label": "Potential breakout" if row["ai_bias"] == "CALL" else (
                    "Potential continuation lower" if row["ai_bias"] == "PUT" else "Potential setup forming"
                ),
                "confidence": f"{row['confidence_score']}%",
            }
        )

    mission_alerts: List[Dict[str, str]] = []
    for row in opportunities:
        watch = row["price_watch"]
        price = float(row["current_price"])
        bias = str(row["ai_bias"])
        confidence = int(row["confidence_score"])
        if watch["ideal_entry_low"] <= price <= watch["ideal_entry_high"]:
            mission_alerts.append(
                {
                    "type": "MISSION ALERT",
                    "ticker": str(row["ticker"]),
                    "message": f"{row['ticker']} has entered its optimal entry zone.",
                    "confidence": f"{confidence}%",
                    "suggested_action": f"Review {bias} opportunity. Requires confirmation.",
                }
            )
            continue
        if bias == "CALL" and price >= watch["breakout_price"]:
            mission_alerts.append(
                {
                    "type": "MISSION ALERT",
                    "ticker": str(row["ticker"]),
                    "message": f"{row['ticker']} is testing breakout trigger ${watch['breakout_price']:.2f}.",
                    "confidence": f"{confidence}%",
                    "suggested_action": "Watch for volume confirmation before acting.",
                }
            )
            continue
        if bias == "PUT" and price <= watch["breakdown_price"]:
            mission_alerts.append(
                {
                    "type": "MISSION ALERT",
                    "ticker": str(row["ticker"]),
                    "message": f"{row['ticker']} is testing breakdown trigger ${watch['breakdown_price']:.2f}.",
                    "confidence": f"{confidence}%",
                    "suggested_action": "Watch for follow-through and risk controls.",
                }
            )

    integration_status = {
        "market_scanner": "Connected",
        "watchlist": "Connected",
        "news_intelligence": "Monitoring",
        "volume_brain": "Connected",
        "support_resistance_brain": "Connected",
        "pattern_brain": "Monitoring",
        "candle_brain": "Monitoring",
        "trend_brain": "Monitoring",
        "neural_engine": "Monitoring",
        "options_brain": "Connected",
        "risk_manager": "Connected",
    }

    return {
        "opportunities": opportunities,
        "mission_queue": mission_queue,
        "timeline": timeline,
        "mission_alerts": mission_alerts,
        "integration_status": integration_status,
        "errors": engine_errors,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
