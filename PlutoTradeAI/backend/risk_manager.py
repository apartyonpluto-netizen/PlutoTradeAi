def evaluate_risk(stock, confidence):
    confidence_score = confidence["confidence"]
    percent_change = abs(stock["percent_change"])
    relative_volume = stock["relative_volume"]

    flags = []

    if confidence_score < 65:
        flags.append("Confidence below 65 for live trading")

    if percent_change >= 8:
        flags.append("High intraday movement")

    if relative_volume >= 3:
        flags.append("Unusually high volume spike")

    if flags:
        execution_lane = "PAPER_ONLY"
    else:
        execution_lane = "ETRADE_READY"

    return {
        "ticker": stock["ticker"],
        "risk_flags": flags,
        "risk_ok": execution_lane == "ETRADE_READY",
        "execution_lane": execution_lane,
    }