def analyze_candle(open_price, high_price, low_price, close_price):
    body = abs(close_price - open_price)
    candle_range = high_price - low_price

    if candle_range == 0:
        return "Invalid candle"

    upper_wick = high_price - max(open_price, close_price)
    lower_wick = min(open_price, close_price) - low_price

    body_percent = body / candle_range
    upper_wick_percent = upper_wick / candle_range
    lower_wick_percent = lower_wick / candle_range

    if lower_wick_percent > 0.5 and body_percent < 0.35:
        return "Hammer / bullish rejection candle"

    if upper_wick_percent > 0.5 and body_percent < 0.35:
        return "Shooting star / bearish rejection candle"

    if body_percent < 0.1:
        return "Doji / indecision candle"

    if close_price > open_price:
        return "Bullish candle"

    if close_price < open_price:
        return "Bearish candle"

    return "Neutral candle"


if __name__ == "__main__":
    test = analyze_candle(100, 105, 95, 104)
    print(test)