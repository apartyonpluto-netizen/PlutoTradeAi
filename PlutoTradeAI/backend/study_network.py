from options_brain import build_options_plan
from market_scanner import scan_market
from support_resistance import find_support_resistance
from confidence_engine import calculate_confidence


def study_market():
    movers = scan_market()
    study_results = []

    for stock in movers:
        ticker = stock["ticker"]
        levels = find_support_resistance(ticker)

        if levels:
            combined = {
                **stock,
                **levels,
            }

            confidence = calculate_confidence(combined)
            combined["confidence"] = confidence["confidence"]
            combined["reasons"] = confidence["reasons"]
            combined["suggested_action"] = confidence["suggested_action"]
            combined["options_plan"] = build_options_plan(combined)
            study_results.append(combined)

    return sorted(study_results, key=lambda x: x["confidence"], reverse=True)


if __name__ == "__main__":
    results = study_market()

    print("\nPlutoTrade Study Network Results:\n")

    for stock in results:
        print(stock)