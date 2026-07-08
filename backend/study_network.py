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

        plan = combined["options_plan"]

        if auto_paper and 50 <= combined["confidence"] < 80 and plan["contract_type"] != "NO TRADE":
            combined["paper_trade_log"] = log_paper_options_trade(plan)
        else:
            combined["paper_trade_log"] = None

        study_results.append(combined)

    return sorted(study_results, key=lambda x: x["confidence"], reverse=True)


def print_report(results):
    print("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("PlutoTrade AI Study Network v0.2")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")

    for stock in results:
        plan = stock["options_plan"]

        print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print(stock["ticker"])
        print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print(f"Current Price: ${stock['latest_close']}")
        print(f"Move: {stock['percent_change']}%")
        print(f"Relative Volume: {stock['relative_volume']}x")
        print(f"Support: ${stock['support']}")
        print(f"Resistance: ${stock['resistance']}")
        print(f"Distance to Support: {stock['distance_to_support_percent']}%")
        print(f"Distance to Resistance: {stock['distance_to_resistance_percent']}%")
        print(f"Candle: {stock['candle_type']}")
        print(f"Candle Bias: {stock['candle_bias']}")
        print(f"Confidence: {stock['confidence']}%")
        print(f"Suggested Action: {stock['suggested_action']}")
        print("\nReasons:")
        for reason in stock["reasons"]:
            print(f"- {reason}")

        print("\nOptions Plan:")
        print(f"Direction: {plan['direction']}")
        print(f"Contract Type: {plan['contract_type']}")
        print(f"Suggested Strike: {plan['suggested_strike']}")
        print(f"Suggested Expiration: {plan['suggested_expiration']}")
        print(f"Entry Trigger: {plan['entry_trigger']}")
        print(f"Stop Loss: {plan['stop_loss']}")
        print(f"Target 1: {plan['target_1']}")
        print(f"Target 2: {plan['target_2']}")
        print(f"Reason: {plan['reason']}")

        if stock["paper_trade_log"]:
            print(f"\nPaper Trade: {stock['paper_trade_log']}")

        print()


if __name__ == "__main__":
    results = study_market(auto_paper=True)
    print_report(results)
