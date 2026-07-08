def analyze_candle(open_price, high_price, low_price, close_price):
    body = abs(close_price - open_price)
    candle_range = high_price - low_price

    if candle_range == 0:
        return {
            "candle_type": "Invalid candle",
            "bias": "Neutral",
            "score": 0,
        }

    upper_wick = high_price - max(open_price, close_price)
    lower_wick = min(open_price, close_price) - low_price

    body_percent = body / candle_range
    upper_wick_percent = upper_wick / candle_range
    lower_wick_percent = lower_wick / candle_range

    if lower_wick_percent > 0.5 and body_percent < 0.35:
        return {
            "candle_type": "Hammer / bullish rejection candle",
            "bias": "Bullish",
            "score": 15,
        }

    if upper_wick_percent > 0.5 and body_percent < 0.35:
        return {
            "candle_type": "Shooting star / bearish rejection candle",
            "bias": "Bearish",
            "score": 15,
        }

    if body_percent < 0.1:
        return {
            "candle_type": "Doji / indecision candle",
            "bias": "Neutral",
            "score": 5,
        }

    if close_price > open_price:
        return {
            "candle_type": "Bullish candle",
            "bias": "Bullish",
            "score": 8,
        }

    if close_price < open_price:
        return {
            "candle_type": "Bearish candle",
            "bias": "Bearish",
            "score": 8,
        }

    return {
        "candle_type": "Neutral candle",
        "bias": "Neutral",
        "score": 0,
    }


if __name__ == "__main__":
    print(analyze_candle(100, 106, 99, 101))
