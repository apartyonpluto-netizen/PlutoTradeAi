def build_options_plan(stock):
    ticker = stock["ticker"]
    price = stock["latest_close"]
    confidence = stock["confidence"]

    candle_bias = stock.get("candle_bias", "Neutral")

    bullish = (
        candle_bias == "Bullish"
        and stock["distance_to_support_percent"] <= 7
        and stock["percent_change"] >= -5
    )

    bearish = (
        candle_bias == "Bearish"
        and stock["distance_to_resistance_percent"] <= 7
        and stock["percent_change"] <= 5
    )

    if bullish:
        return {
            "ticker": ticker,
            "direction": "Bullish",
            "trade_type": "Options",
            "contract_type": "CALL",
            "suggested_strike": "Near-the-money call",
            "suggested_expiration": "7–21 DTE swing / same-week only for day trade",
            "entry_trigger": round(price * 1.01, 2),
            "stop_loss": round(price * 0.97, 2),
            "target_1": round(price * 1.04, 2),
            "target_2": round(price * 1.08, 2),
            "confidence": confidence,
            "reason": "Bullish candle behavior near support with upside continuation potential",
        }

    if bearish:
        return {
            "ticker": ticker,
            "direction": "Bearish",
            "trade_type": "Options",
            "contract_type": "PUT",
            "suggested_strike": "Near-the-money put",
            "suggested_expiration": "7–21 DTE swing / same-week only for day trade",
            "entry_trigger": round(price * 0.99, 2),
            "stop_loss": round(price * 1.03, 2),
            "target_1": round(price * 0.96, 2),
            "target_2": round(price * 0.92, 2),
            "confidence": confidence,
            "reason": "Bearish candle behavior near resistance with downside continuation potential",
        }

    return {
        "ticker": ticker,
        "direction": "Neutral",
        "trade_type": "Options",
        "contract_type": "NO TRADE",
        "suggested_strike": None,
        "suggested_expiration": None,
        "entry_trigger": None,
        "stop_loss": None,
        "target_1": None,
        "target_2": None,
        "confidence": confidence,
        "reason": "No clean options direction yet",
    }
