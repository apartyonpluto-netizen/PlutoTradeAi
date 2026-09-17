"""One-shot live diagnostic for the "MSTR scan EMA stack is wildly stale"
report (2026-09-03).

Run it wherever the REAL Alpaca keys are available (Render shell, or
locally after `export ALPACA_API_KEY_ID=... ALPACA_API_SECRET_KEY=...`):

    python backend/scripts/diag_mstr_ema.py MSTR AAPL

For each ticker it prints, straight off the app's own data path:
  * the daily frame alpaca_data.get_bars_single(t, "9mo", "1d") returns:
    row count, FIRST and LAST bar date, last close
  * EMA9 / EMA20 / EMA50 computed exactly as strategy_brain / charting_brain do
  * strategy_brain.build_strategy_intelligence()'s market_context
    (current_price there == last DAILY close)
  * alpaca_data.get_latest_trade_price(t)  -- genuinely real-time (IEX)
  * the gap between the strategy's current_price and the real-time price

INTERPRETATION
  last bar date is today / yesterday, gap < ~3%  -> data is FRESH.
     EMAs sitting well below price is just lag on a fast move, not a bug.
  last bar date is days/weeks old, gap large     -> STALE daily frame for
     that ticker: Alpaca returned an old-but-long series, it cleared the
     >=80-bar sufficiency gate, and the scan emitted a candidate off dead
     numbers. That is the reported failure.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from integrations import alpaca_data  # noqa: E402
from brains import strategy_brain  # noqa: E402


def _emas(closes: pd.Series) -> dict:
    return {
        span: round(float(closes.ewm(span=span, adjust=False).mean().iloc[-1]), 2)
        for span in (9, 20, 50, 200)
    }


def diagnose(ticker: str) -> None:
    print(f"\n{'=' * 70}\n{ticker}\n{'=' * 70}")
    if not alpaca_data.is_configured():
        print("  ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY not set in this env - aborting.")
        return

    try:
        daily = alpaca_data.get_bars_single(ticker, period="9mo", interval="1d")
    except Exception as exc:  # noqa: BLE001
        print(f"  get_bars_single raised: {exc!r}")
        return

    if daily.empty:
        print("  daily frame is EMPTY (would be insufficient_data -> ticker skipped).")
        return

    closes = daily["Close"].astype(float)
    last_ts = daily.index[-1]
    first_ts = daily.index[0]
    age_days = (datetime.now(timezone.utc) - last_ts.to_pydatetime()).total_seconds() / 86400.0

    print(f"  rows                 : {len(daily)}  (sufficiency gate needs >= 80)")
    print(f"  first bar            : {first_ts.date()}")
    print(f"  last bar             : {last_ts.date()}   <-- age {age_days:.1f} calendar days")
    print(f"  last daily close     : {float(closes.iloc[-1]):.2f}")
    print(f"  EMA 9/20/50/200      : {_emas(closes)}")

    rt = alpaca_data.get_latest_trade_price(ticker)
    print(f"  get_latest_trade_price: {rt}")

    strat = strategy_brain.build_strategy_intelligence(ticker)
    mc = strat.get("market_context", {})
    print(f"  build_strategy_intelligence.insufficient_data : {strat.get('insufficient_data')}")
    print(f"  strategy best/conf/rec: {strat.get('best_strategy')} / {strat.get('strategy_confidence')} / {strat.get('recommendation')}")
    print(f"  market_context.current_price : {mc.get('current_price')}   (== last daily close)")
    print(f"  market_context EMA 9/20/50   : {mc.get('ema_9')} / {mc.get('ema_20')} / {mc.get('ema_50')}")
    print(f"  why_this_strategy_fits       : {strat.get('why_this_strategy_fits')}")

    if rt and mc.get("current_price"):
        gap = abs(rt - float(mc["current_price"])) / rt * 100
        verdict = "STALE DAILY FRAME" if (gap > 4 or age_days > 4) else "fresh"
        print(f"  >>> strategy current_price vs real-time gap: {gap:.1f}%   => {verdict}")


if __name__ == "__main__":
    tickers = [t.upper() for t in sys.argv[1:]] or ["MSTR", "AAPL"]
    for t in tickers:
        diagnose(t)
