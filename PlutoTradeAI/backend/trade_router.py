from approval_engine import evaluate_add_candidate, evaluate_trade_candidate
from risk_manager import evaluate_risk


def route_trade_candidate(stock, confidence):
    risk_report = evaluate_risk(stock, confidence)
    add_decision = evaluate_add_candidate(stock, confidence)
    trade_decision = evaluate_trade_candidate(confidence, risk_report)

    if trade_decision["action"] == "NO_LIVE_TRADE":
        route = "WATCH_OR_PAPER"
    elif trade_decision["action"] == "PAPER_TRADE":
        route = "WEBULL_PAPER"
    else:
        route = "ETRADE_MANUAL_APPROVAL"

    return {
        "ticker": stock["ticker"],
        "route": route,
        "add_decision": add_decision,
        "trade_decision": trade_decision,
        "risk_report": risk_report,
    }