def calculate_confidence(stock):
    confidence = 0
    reasons = []

    if stock["scanner_score"] >= 50:
        confidence += 20
        reasons.append("Strong market scanner score")

    if abs(stock["percent_change"]) >= 3:
        confidence += 15
        reasons.append("Large price movement detected")

    if stock["relative_volume"] >= 1.5:
        confidence += 20
        reasons.append("Volume is above normal")

    if stock["distance_to_support_percent"] <= 5:
        confidence += 15
        reasons.append("Price is near support")

    if stock["distance_to_resistance_percent"] <= 5:
        confidence += 10
        reasons.append("Price is near resistance or breakout zone")

    if stock.get("candle_score", 0) >= 10:
        confidence += 15
        reasons.append(f"Candle signal detected: {stock.get('candle_type')}")

    if stock["on_watchlist"]:
        confidence += 10
        reasons.append("Stock is already on your watchlist")

    confidence = min(confidence, 100)

    return {
        "confidence": confidence,
        "reasons": reasons,
        "suggested_action": suggest_action(confidence, stock),
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
