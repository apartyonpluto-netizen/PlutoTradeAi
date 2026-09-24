def calculate_confidence(stock):
    weights = {
        "trend": 12,
        "momentum": 12,
        "volume": 12,
        "pattern": 10,
        "options": 8,
        "news": 8,
        "support": 10,
        "resistance": 8,
        "risk": 10,
        "market_readiness": 10,
    }

    confidence = 0
    reasons = []
    factors = {}

    trend_score = 0
    if stock["percent_change"] > 0:
        trend_score = weights["trend"]
    elif stock["percent_change"] < 0:
        trend_score = 4
    factors["trend"] = trend_score
    confidence += trend_score

    momentum_score = 0
    if stock["scanner_score"] >= 50:
        momentum_score = weights["momentum"]
        reasons.append("Strong market scanner score")
    elif stock["scanner_score"] >= 30:
        momentum_score = 6
    factors["momentum"] = momentum_score
    confidence += momentum_score

    move_score = 0
    if abs(stock["percent_change"]) >= 3:
        move_score = 10
        reasons.append("Large price movement detected")
    factors["pattern"] = move_score
    confidence += move_score

    volume_score = 0
    if stock["relative_volume"] >= 1.5:
        volume_score = weights["volume"]
        reasons.append("Volume is above normal")
    elif stock["relative_volume"] >= 1.2:
        volume_score = 6
    factors["volume"] = volume_score
    confidence += volume_score

    support_score = 0
    if stock["distance_to_support_percent"] <= 5:
        support_score = weights["support"]
        reasons.append("Price is near support")
    factors["support"] = support_score
    confidence += support_score

    resistance_score = 0
    if stock["distance_to_resistance_percent"] <= 5:
        resistance_score = weights["resistance"]
        reasons.append("Price is near resistance or breakout zone")
    factors["resistance"] = resistance_score
    confidence += resistance_score

    options_score = 0
    if stock.get("candle_score", 0) >= 10:
        options_score = weights["options"]
        reasons.append(f"Candle signal detected: {stock.get('candle_type')}")
    factors["options"] = options_score
    confidence += options_score

    risk_score = weights["risk"] if stock["scanner_score"] >= 40 else 4
    factors["risk"] = risk_score
    confidence += risk_score

    news_score = 4
    factors["news"] = news_score
    confidence += news_score

    readiness_score = weights["market_readiness"] if stock["scanner_score"] >= 45 else 5
    factors["market_readiness"] = readiness_score
    confidence += readiness_score

    if stock["on_watchlist"]:
        confidence += 6
        reasons.append("Stock is already on your watchlist")

    confidence = min(confidence, 100)

    return {
        "confidence": confidence,
        "reasons": reasons,
        "suggested_action": suggest_action(confidence, stock),
        "weights": weights,
        "factors": factors,
    }


def suggest_action(confidence, stock):
    if confidence >= 80:
        return "High-confidence options setup — review for execution later"

    if confidence >= 65:
        if stock["on_watchlist"]:
            return "Strong setup on watchlist — review options trade"
        return "AI Discovery candidate — add to watchlist"

    if confidence >= 50:
        return "Paper trade candidate"

    return "Watch only"
