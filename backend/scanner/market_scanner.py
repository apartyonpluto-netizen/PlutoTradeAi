import yfinance as yf
from backend.scanner.watchlist import is_on_watchlist, add_to_watchlist


SCAN_LIST = [
    "TSLA", "NVDA", "AAPL", "AMD", "META", "MSFT",
    "PLTR", "COIN", "MARA", "RIVN", "SOFI", "HOOD",
    "SPY", "QQQ"
]


def get_number(value):
    try:
        return float(value.item())
    except AttributeError:
        return float(value)


def analyze_stock(ticker):
    data = yf.download(ticker, period="5d", interval="1d", progress=False, auto_adjust=True)

    if data.empty or len(data) < 2:
        return None

    latest = data.iloc[-1]
    previous = data.iloc[-2]

    latest_close = get_number(latest["Close"])
    previous_close = get_number(previous["Close"])
    latest_volume = get_number(latest["Volume"])
    avg_volume = get_number(data["Volume"].mean())

    percent_change = ((latest_close - previous_close) / previous_close) * 100
    relative_volume = latest_volume / avg_volume if avg_volume > 0 else 0

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
    }


def scan_market():
    results = []

    for ticker in SCAN_LIST:
        result = analyze_stock(ticker)

        if result:
            results.append(result)

    return sorted(results, key=lambda x: x["scanner_score"], reverse=True)


if __name__ == "__main__":
    for stock in scan_market():
        print(stock)

        if not stock["on_watchlist"] and stock["scanner_score"] >= 65:
            print(add_to_watchlist(
                stock["ticker"],
                category="AI Discovery",
                status="Pending",
                ai_score=stock["scanner_score"],
                notes="Market scanner detected strong movement and/or volume"
            ))
