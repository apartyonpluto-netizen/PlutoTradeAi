from backend.services.market_data_service import get_stock_snapshot
from backend.scanner.watchlist import is_on_watchlist


SCAN_LIST = [
    "TSLA", "NVDA", "AAPL", "AMD", "META", "MSFT",
    "PLTR", "COIN", "MARA", "RIVN", "SOFI", "HOOD",
    "SPY", "QQQ"
]


def analyze_stock(ticker):
    snapshot = get_stock_snapshot(ticker)
    if not snapshot.get("success"):
        return None

    data = snapshot.get("data", {})
    latest_close = data.get("current_price")
    percent_change = data.get("daily_change_percent") or 0
    relative_volume = data.get("relative_volume") or 0
    if latest_close is None:
        return None

    score = 0

    if abs(percent_change) >= 3:
        score += 25

    if relative_volume >= 1.5:
        score += 25

    if latest_close > previous_close:
        score += 10

    if is_on_watchlist(ticker):
        score += 15

    return {
        "ticker": ticker,
        "price": round(latest_close, 2),
        "percent_change": round(percent_change, 2),
        "relative_volume": round(relative_volume, 2),
        "on_watchlist": is_on_watchlist(ticker),
        "scanner_score": score,
        "provider": snapshot.get("provider"),
        "timestamp": snapshot.get("timestamp"),
        "market_session": data.get("market_session"),
        "status": data.get("market_data_status") or snapshot.get("data_status"),
        "age": data.get("market_data_age_seconds"),
    }


def scan_market():
    results = []

    for ticker in SCAN_LIST:
        result = analyze_stock(ticker)

        if result:
            results.append(result)

    return sorted(results, key=lambda x: x["scanner_score"], reverse=True)


if __name__ == "__main__":
    scan_market()
