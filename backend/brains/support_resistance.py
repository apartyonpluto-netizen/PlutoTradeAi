import yfinance as yf


def get_number(value):
    try:
        return float(value.item())
    except AttributeError:
        return float(value)


def find_support_resistance(ticker, period="3mo", interval="1d"):
    data = yf.download(ticker, period=period, interval=interval, progress=False, auto_adjust=True)

    if data.empty or len(data) < 20:
        return None

    recent_lows = data["Low"].tail(20)
    recent_highs = data["High"].tail(20)
    latest_close = get_number(data["Close"].iloc[-1])

    support = get_number(recent_lows.min())
    resistance = get_number(recent_highs.max())

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
