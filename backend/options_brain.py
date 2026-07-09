from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence, Tuple

import pandas as pd
import yfinance as yf

DISCLAIMER_TEXT = "For research only. Not financial advice."


def _safe_float(value: object) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _format_price(value: Optional[float]) -> str:
    if value is None:
        return "Data unavailable"
    return f"${value:,.2f}"


def _format_percent(value: Optional[float]) -> str:
    if value is None:
        return "Data unavailable"
    return f"{value:.2f}%"


def _volatility_risk_label(daily_volatility: Optional[float]) -> str:
    if daily_volatility is None:
        return "Data unavailable"
    if daily_volatility < 0.02:
        return "Low"
    if daily_volatility < 0.035:
        return "Moderate"
    return "Elevated"


def _build_unavailable_expiration(label: str, selected_date: str = "Data unavailable", reason: str = "") -> Dict[str, str]:
    return {
        "timeframe": label,
        "expiration_date": selected_date,
        "suggested_contract_type": "Data unavailable",
        "suggested_strike_area": "Data unavailable",
        "estimated_option_premium": "Data unavailable",
        "break_even_price": "Data unavailable",
        "risk_warning": reason or "Data unavailable. Watch only and wait for live chain confirmation.",
        "selection_reason": "Data unavailable",
    }


def _build_data_unavailable_response(ticker: str, reason: str) -> Dict[str, object]:
    return {
        "ticker": ticker,
        "suggested_direction": "WAIT",
        "confidence_score": 0,
        "reason_for_direction": f"AI favors WAIT. {reason}",
        "current_stock_price": "Data unavailable",
        "expected_move": "Data unavailable",
        "key_support": "Data unavailable",
        "key_resistance": "Data unavailable",
        "breakout_price": "Data unavailable",
        "breakdown_price": "Data unavailable",
        "risk_level": "Data unavailable",
        "expirations": [
            _build_unavailable_expiration("Short-term expiration"),
            _build_unavailable_expiration("Medium-term expiration"),
            _build_unavailable_expiration("Safer swing expiration"),
        ],
        "disclaimer": DISCLAIMER_TEXT,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _pick_expiration(option_dates: Sequence[str], min_days: int, max_days: int) -> Optional[str]:
    today = datetime.now(timezone.utc).date()
    parsed_dates: List[Tuple[str, int]] = []
    for date_value in option_dates:
        try:
            expiration_date = datetime.strptime(date_value, "%Y-%m-%d").date()
        except ValueError:
            continue
        days_out = (expiration_date - today).days
        if days_out < 0:
            continue
        parsed_dates.append((date_value, days_out))
    if not parsed_dates:
        return None

    in_window = [item for item in parsed_dates if min_days <= item[1] <= max_days]
    if in_window:
        return min(in_window, key=lambda item: item[1])[0]

    after_window = [item for item in parsed_dates if item[1] >= min_days]
    if after_window:
        return min(after_window, key=lambda item: item[1])[0]
    return max(parsed_dates, key=lambda item: item[1])[0]


def _build_direction_model(close_prices: pd.Series, volume_series: pd.Series) -> Tuple[str, int, str]:
    current_price = float(close_prices.iloc[-1])
    sma20 = float(close_prices.tail(20).mean())
    sma50 = float(close_prices.tail(50).mean()) if len(close_prices) >= 50 else sma20
    five_day_return = ((current_price / float(close_prices.iloc[-6])) - 1) * 100 if len(close_prices) >= 6 else 0.0

    avg_volume = float(volume_series.tail(20).mean()) if not volume_series.empty else 0.0
    last_volume = float(volume_series.iloc[-1]) if not volume_series.empty else 0.0
    relative_volume = (last_volume / avg_volume) if avg_volume else 1.0

    score = 0
    score += 30 if current_price > sma20 else -30
    score += 22 if sma20 >= sma50 else -22
    if five_day_return > 1:
        score += 15
    elif five_day_return < -1:
        score -= 15
    if relative_volume >= 1.2 and five_day_return > 0:
        score += 10
    elif relative_volume >= 1.2 and five_day_return < 0:
        score -= 10

    confidence = min(92, max(30, int(35 + abs(score) * 0.75)))
    if abs(score) < 22:
        direction = "WAIT"
        confidence = min(confidence, 56)
        reason = (
            "AI favors WAIT because trend signals are mixed. Watch breakout and breakdown levels for a possible setup; "
            "trade direction requires confirmation."
        )
        return direction, confidence, reason

    if score > 0:
        direction = "CALL"
        reason = (
            "AI favors a CALL bias while price holds above support with constructive trend behavior. "
            "Watch for confirmation above breakout before considering a setup."
        )
    else:
        direction = "PUT"
        reason = (
            "AI favors a PUT bias while price pressure remains below resistance and downside momentum is active. "
            "Watch for confirmation below breakdown before considering a setup."
        )
    return direction, confidence, reason


def _select_contract_row(contracts: pd.DataFrame, target_strike: float) -> Optional[pd.Series]:
    if contracts.empty or "strike" not in contracts.columns:
        return None
    strikes = contracts["strike"].apply(_safe_float).dropna()
    if strikes.empty:
        return None
    closest_idx = (strikes - target_strike).abs().idxmin()
    return contracts.loc[closest_idx]


def _strike_area(contracts: pd.DataFrame, selected_row: pd.Series) -> str:
    selected_strike = _safe_float(selected_row.get("strike"))
    if selected_strike is None:
        return "Data unavailable"

    strike_series = contracts["strike"].apply(_safe_float).dropna().sort_values()
    strike_values = strike_series.tolist()
    if not strike_values:
        return _format_price(selected_strike)
    center_index = min(range(len(strike_values)), key=lambda idx: abs(strike_values[idx] - selected_strike))
    low_strike = strike_values[max(0, center_index - 1)]
    high_strike = strike_values[min(len(strike_values) - 1, center_index + 1)]
    if low_strike == high_strike:
        return _format_price(selected_strike)
    return f"{_format_price(low_strike)} - {_format_price(high_strike)}"


def _estimated_premium(selected_row: pd.Series) -> Optional[float]:
    bid = _safe_float(selected_row.get("bid"))
    ask = _safe_float(selected_row.get("ask"))
    if bid is not None and ask is not None and bid > 0 and ask > 0:
        return (bid + ask) / 2
    last_price = _safe_float(selected_row.get("lastPrice"))
    if last_price is not None and last_price > 0:
        return last_price
    return None


def build_options_outlook(ticker: str) -> Dict[str, object]:
    symbol = ticker.strip().upper()
    if not symbol:
        raise ValueError("Ticker is required.")

    ticker_client = yf.Ticker(symbol)
    try:
        history = ticker_client.history(period="6mo", interval="1d", auto_adjust=False)
    except Exception as error:
        return _build_data_unavailable_response(symbol, f"Data unavailable ({error}).")

    if history.empty or "Close" not in history.columns:
        return _build_data_unavailable_response(symbol, "Data unavailable from market history.")

    close_prices = history["Close"].dropna()
    if close_prices.empty:
        return _build_data_unavailable_response(symbol, "Data unavailable from closing prices.")
    volume_series = history["Volume"].dropna() if "Volume" in history.columns else pd.Series(dtype=float)

    current_price = float(close_prices.iloc[-1])
    daily_returns = close_prices.pct_change().dropna().tail(20)
    daily_vol = _safe_float(daily_returns.std())
    expected_move_value = current_price * daily_vol * math.sqrt(5) if daily_vol is not None else None
    expected_move_pct = (expected_move_value / current_price * 100) if expected_move_value is not None else None

    support = _safe_float(close_prices.tail(20).min())
    resistance = _safe_float(close_prices.tail(20).max())
    breakout = resistance * 1.002 if resistance is not None else None
    breakdown = support * 0.998 if support is not None else None

    direction, confidence, reason = _build_direction_model(close_prices=close_prices, volume_series=volume_series)
    bias_contract_type = "Call" if direction == "CALL" else "Put" if direction == "PUT" else "Call"

    try:
        option_dates = list(ticker_client.options)
    except Exception:
        option_dates = []

    expiration_specs = [
        (
            "Short-term expiration",
            7,
            14,
            "Short-term contract can capture momentum quickly but decays fast. No profit is guaranteed.",
            "Selected for a 7-14 day aggressive momentum window; requires confirmation.",
        ),
        (
            "Medium-term expiration",
            30,
            45,
            "Medium-term contract balances theta risk and directional follow-through. No profit is guaranteed.",
            "Selected for a 30-45 day balanced swing setup with extra reaction time.",
        ),
        (
            "Safer swing expiration",
            60,
            90,
            "Longer-dated contract reduces near-term decay but still carries downside risk. No profit is guaranteed.",
            "Selected for a 60-90 day time buffer while the setup develops.",
        ),
    ]

    expirations: List[Dict[str, str]] = []
    for label, min_days, max_days, risk_warning, selection_reason in expiration_specs:
        selected_date = _pick_expiration(option_dates, min_days=min_days, max_days=max_days)
        if not selected_date:
            expirations.append(
                _build_unavailable_expiration(
                    label,
                    reason=f"Data unavailable for this window. Watch only; trade setup requires confirmation.",
                )
            )
            continue

        try:
            chain = ticker_client.option_chain(selected_date)
        except Exception:
            expirations.append(
                _build_unavailable_expiration(
                    label,
                    selected_date=selected_date,
                    reason="Data unavailable for this expiration chain. Watch only and wait for live updates.",
                )
            )
            continue

        contracts = chain.calls if bias_contract_type == "Call" else chain.puts
        if contracts.empty:
            expirations.append(
                _build_unavailable_expiration(
                    label,
                    selected_date=selected_date,
                    reason="Data unavailable: contract list is empty for this expiration.",
                )
            )
            continue

        move = expected_move_value or (current_price * 0.02)
        if bias_contract_type == "Call":
            target_strike = current_price + (move * 0.6)
        else:
            target_strike = max(0.01, current_price - (move * 0.6))
        selected_row = _select_contract_row(contracts, target_strike=target_strike)
        if selected_row is None:
            expirations.append(
                _build_unavailable_expiration(
                    label,
                    selected_date=selected_date,
                    reason="Data unavailable: strike selection failed for this expiration.",
                )
            )
            continue

        selected_strike = _safe_float(selected_row.get("strike"))
        premium = _estimated_premium(selected_row)
        if selected_strike is None:
            break_even = "Data unavailable"
        elif premium is None:
            break_even = "Data unavailable"
        elif bias_contract_type == "Call":
            break_even = _format_price(selected_strike + premium)
        else:
            break_even = _format_price(max(0.01, selected_strike - premium))

        expirations.append(
            {
                "timeframe": label,
                "expiration_date": selected_date,
                "suggested_contract_type": bias_contract_type,
                "suggested_strike_area": _strike_area(contracts, selected_row),
                "estimated_option_premium": _format_price(premium) if premium is not None else "Data unavailable",
                "break_even_price": break_even,
                "risk_warning": risk_warning,
                "selection_reason": selection_reason,
            }
        )

    if expected_move_value is None or expected_move_pct is None:
        expected_move_label = "Data unavailable"
    else:
        expected_move_label = f"{_format_price(expected_move_value)} ({_format_percent(expected_move_pct)} projected 1-week range)"

    return {
        "ticker": symbol,
        "suggested_direction": direction,
        "confidence_score": confidence,
        "reason_for_direction": reason,
        "current_stock_price": _format_price(current_price),
        "expected_move": expected_move_label,
        "key_support": _format_price(support),
        "key_resistance": _format_price(resistance),
        "breakout_price": _format_price(breakout),
        "breakdown_price": _format_price(breakdown),
        "risk_level": _volatility_risk_label(daily_vol),
        "expirations": expirations,
        "disclaimer": DISCLAIMER_TEXT,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
