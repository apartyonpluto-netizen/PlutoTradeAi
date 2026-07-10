from backend.brains.risk_brain import calculate_risk_plan
import yfinance as yf

from backend.scanner.market_scanner import scan_market, get_number
from backend.brains.support_resistance import find_support_resistance
from backend.brains.candle_brain import analyze_candle
from backend.brains.confidence_engine import calculate_confidence
from backend.brains.options_brain import build_options_plan
from backend.execution.paper_trader import log_paper_options_trade


def get_latest_candle(ticker):
    data = yf.download(ticker, period="5d", interval="1d", progress=False, auto_adjust=True)

    if data.empty:
        return None

    latest = data.iloc[-1]

    return {
        "open": get_number(latest["Open"]),
        "high": get_number(latest["High"]),
        "low": get_number(latest["Low"]),
        "close": get_number(latest["Close"]),
    }


def study_market(auto_paper=True):
    movers = scan_market()
    study_results = []

    for stock in movers:
        ticker = stock["ticker"]
        levels = find_support_resistance(ticker)
        candle = get_latest_candle(ticker)

        if not levels or not candle:
            continue

        candle_analysis = analyze_candle(
            candle["open"],
            candle["high"],
            candle["low"],
            candle["close"],
        )

        combined = {
            **stock,
            **levels,
            "candle_type": candle_analysis["candle_type"],
            "candle_bias": candle_analysis["bias"],
            "candle_score": candle_analysis["score"],
        }

        confidence = calculate_confidence(combined)

        combined["confidence"] = confidence["confidence"]
        combined["reasons"] = confidence["reasons"]
        combined["suggested_action"] = confidence["suggested_action"]
        combined["options_plan"] = build_options_plan(combined)
        combined["risk_plan"] = calculate_risk_plan(combined["options_plan"])

        plan = combined["options_plan"]
        if auto_paper and 50 <= combined["confidence"] < 80 and plan["contract_type"] != "NO TRADE":
            combined["paper_trade_log"] = log_paper_options_trade(plan)
        else:
            combined["paper_trade_log"] = None

        study_results.append(combined)

    return sorted(study_results, key=lambda x: x["confidence"], reverse=True)


def print_report(results):
    lines = []
    lines.append("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("PlutoTrade AI Study Network v0.2")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")

    for stock in results:
        plan = stock["options_plan"]

        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append(stock["ticker"])
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"Current Price: ${stock['latest_close']}")
        lines.append(f"Move: {stock['percent_change']}%")
        lines.append(f"Relative Volume: {stock['relative_volume']}x")
        lines.append(f"Support: ${stock['support']}")
        lines.append(f"Resistance: ${stock['resistance']}")
        lines.append(f"Distance to Support: {stock['distance_to_support_percent']}%")
        lines.append(f"Distance to Resistance: {stock['distance_to_resistance_percent']}%")
        lines.append(f"Candle: {stock['candle_type']}")
        lines.append(f"Candle Bias: {stock['candle_bias']}")
        lines.append(f"Confidence: {stock['confidence']}%")
        lines.append(f"Suggested Action: {stock['suggested_action']}")
        lines.append("\nReasons:")
        for reason in stock["reasons"]:
            lines.append(f"- {reason}")

        lines.append("\nOptions Plan:")
        risk = stock["risk_plan"]
        lines.append("\nRisk Plan:")
        lines.append(f"Risk/Reward: {risk['risk_reward_ratio']}")
        lines.append(f"Risk Status: {risk['risk_status']}")
        lines.append(f"Max Loss Rule: {risk['max_loss_rule']}")
        for note in risk["risk_notes"]:
            lines.append(f"- {note}")
        lines.append(f"Direction: {plan['direction']}")
        lines.append(f"Contract Type: {plan['contract_type']}")
        lines.append(f"Suggested Strike: {plan['suggested_strike']}")
        lines.append(f"Suggested Expiration: {plan['suggested_expiration']}")
        lines.append(f"Entry Trigger: {plan['entry_trigger']}")
        lines.append(f"Stop Loss: {plan['stop_loss']}")
        lines.append(f"Target 1: {plan['target_1']}")
        lines.append(f"Target 2: {plan['target_2']}")
        lines.append(f"Reason: {plan['reason']}")

        if stock["paper_trade_log"]:
            lines.append(f"\nPaper Trade: {stock['paper_trade_log']}")

        lines.append("")

    return "\n".join(lines)


if __name__ == "__main__":
    study_market(auto_paper=True)
