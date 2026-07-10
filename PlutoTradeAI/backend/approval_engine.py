def evaluate_add_candidate(stock, confidence):
    is_new_stock = not stock["on_watchlist"]
    confidence_score = confidence["confidence"]

    if is_new_stock and confidence_score >= 65:
        return {
            "action": "AUTO_ADD_AI_DISCOVERY",
            "needs_user_approval": False,
            "reason": "New stock meets 65%+ confidence rule for AI Discovery",
        }

    if is_new_stock and confidence_score < 65:
        return {
            "action": "HOLD",
            "needs_user_approval": True,
            "reason": "New stock does not meet 65% confidence requirement",
        }

    return {
        "action": "KEEP_ON_WATCHLIST",
        "needs_user_approval": False,
        "reason": "Stock is already on your watchlist",
    }


def evaluate_trade_candidate(confidence, risk_report):
    confidence_score = confidence["confidence"]
    risk_lane = risk_report["execution_lane"]

    if confidence_score < 65:
        return {
            "action": "NO_LIVE_TRADE",
            "needs_user_approval": True,
            "reason": "Confidence below live-trade threshold",
        }

    if risk_lane != "ETRADE_READY":
        return {
            "action": "PAPER_TRADE",
            "needs_user_approval": True,
            "reason": "Risk rules did not approve live lane",
        }

    return {
        "action": "LIVE_REVIEW",
        "needs_user_approval": True,
        "reason": "Requires manual approval before any live execution",
    }