def build_options_plan(stock):
    ticker = stock["ticker"]
    price = stock["latest_close"]
    confidence = stock["confidence"]

    bullish = stock["percent_change"] > 0 and stock["distance_to_support_percent"] <= 6
    bearish = stock["percent_change"] < 0 and stock["distance_to_resistance_percent"] <= 6

    if bullish:
        direction = "Bullish"
        contract_type = "CALL"
        entry_trigger = round(price * 1.01, 2)
        stop_loss = round(price * 0.97, 2)
        target_1 = round(price * 1.04, 2)
        target_2 = round(price * 1.08, 2)
        reason = "Bullish setup near support with upside continuation potential"

    elif bearish:
        direction = "Bearish"
        contract_type = "PUT"
        entry_trigger = round(price * 0.99, 2)
        stop_loss = round(price * 1.03, 2)
        target_1 = round(price * 0.96, 2)
        target_2 = round(price * 0.92, 2)
        reason = "Bearish setup near resistance with downside continuation potential"

    else:
        direction = "Neutral"
        contract_type = "NO TRADE"
        entry_trigger = None
        stop_loss = None
        target_1 = None
        target_2 = None
        reason = "No clean directional setup yet"

    return {
        "ticker": ticker,
        "direction": direction,
        "contract_type": contract_type,
        "suggested_strike": "Near-the-money",
        "suggested_expiration": "7–21 days for swing / same-week only for day trade",
        "entry_trigger": entry_trigger,
        "stop_loss": stop_loss,
        "target_1": target_1,
        "target_2": target_2,
        "confidence": confidence,
        "reason": reason,
    }