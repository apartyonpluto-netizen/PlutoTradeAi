"""Offline confidence-model training.

Replays daily Alpaca bars (IEX, free tier) for up to PLUTO_ML_MAX_SYMBOLS
names, one request at a time with a short pause. The model is admitted only
when the newer period still looks profitable after costs. It never places
an order and it is not called from the monitor loop.

Example:
  cd backend && python scripts/train_confidence_model.py --symbols AAPL,MSFT --months 18
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ml_confidence
from backtest_engine import _fetch_daily_history, _normalize_ohlcv


def main() -> int:
    parser = argparse.ArgumentParser(description="Train the paper-only confidence model from daily bars.")
    parser.add_argument("--symbols", required=True, help="Comma-separated tickers")
    parser.add_argument("--months", type=int, default=ml_confidence.DEFAULT_LOOKBACK_MONTHS)
    args = parser.parse_args()
    symbols = [part.strip().upper() for part in args.symbols.split(",") if part.strip()]
    symbols = symbols[: ml_confidence.DEFAULT_MAX_SYMBOLS]
    months = max(1, min(int(args.months), 24))
    rows = []
    for symbol in symbols:
        print(f"loading {symbol}", flush=True)
        frame = _normalize_ohlcv(_fetch_daily_history(symbol, months), symbol)
        rows.extend(ml_confidence.replay_frame(frame, symbol))
        time.sleep(ml_confidence.ALPACA_PAUSE_SECONDS)
    meta = ml_confidence.train_from_rows(rows)
    print(meta.get("reason") or ("admitted" if meta.get("admitted") else "not admitted"))
    return 0 if meta.get("admitted") else 2


if __name__ == "__main__":
    raise SystemExit(main())
