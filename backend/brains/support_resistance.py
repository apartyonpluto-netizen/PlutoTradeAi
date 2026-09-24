from backend.services.market_data_service import get_chart_data, get_stock_snapshot


def find_support_resistance(ticker, period="3mo", interval="1d"):
    chart = get_chart_data(ticker, timeframe="3M")
    snapshot = get_stock_snapshot(ticker)
    if not chart.get("success") or not snapshot.get("success"):
        return None

    levels = chart.get("data", {}).get("indicators", {})
    latest_close = snapshot.get("data", {}).get("current_price")
    support = levels.get("major_support")
    resistance = levels.get("major_resistance")

    if latest_close in (None, 0) or support is None or resistance is None:
        return None

    distance_to_support = ((latest_close - support) / latest_close) * 100
    distance_to_resistance = ((resistance - latest_close) / latest_close) * 100

    return {
        "latest_close": round(latest_close, 2),
        "support": round(support, 2),
        "resistance": round(resistance, 2),
        "distance_to_support_percent": round(distance_to_support, 2),
        "distance_to_resistance_percent": round(distance_to_resistance, 2),
    }


if __name__ == "__main__":
    find_support_resistance("TSLA")
