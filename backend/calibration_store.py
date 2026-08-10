from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("PLUTO_DATA_DIR", str(BASE_DIR / "data"))).resolve()
CALIBRATION_FILE = DATA_DIR / "strategy_calibration.json"

# Below this many recorded trades, a strategy's measured win rate is treated
# as noise, not evidence - the raw hand-tuned score is left unadjusted rather
# than swung around by a handful of lucky/unlucky trades.
MIN_TRADES_TO_TRUST = 15
MAX_SCORE_ADJUSTMENT = 0.25


def write_calibration(payload: Dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CALIBRATION_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def get_calibration() -> Dict[str, Any]:
    if not CALIBRATION_FILE.exists():
        return {"status": "never_run", "generated_at": "", "strategy_stats": {}}
    try:
        data = json.loads(CALIBRATION_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"status": "never_run", "generated_at": "", "strategy_stats": {}}
    return data if isinstance(data, dict) else {"status": "never_run", "generated_at": "", "strategy_stats": {}}


def score_multiplier(strategy_name: str) -> float:
    """Multiplier applied to a strategy's raw hand-tuned score, derived from
    its measured backtested AVERAGE RETURN per trade - not win rate. A
    strategy that wins less than half the time but lets winners run bigger
    than its losses (e.g. a 49% win rate with a strong average return) is
    genuinely profitable and should be rewarded, not penalized for a
    coin-flip-adjacent win rate; win rate alone can't tell that difference,
    only expectancy can. 1.0 (no change) if calibration has never run, is
    still running, or this strategy doesn't have enough trade samples yet.
    0% average return leaves the score unchanged; further from 0% nudges it
    proportionally, capped so a small sample or a hot/cold streak can't swing
    the ranking wildly."""
    calibration = get_calibration()
    if calibration.get("status") != "done":
        return 1.0
    stats = calibration.get("strategy_stats", {}).get(strategy_name)
    if not stats or not stats.get("trusted"):
        return 1.0
    avg_return = float(stats.get("avg_return_percent", 0.0))
    adjustment = max(-MAX_SCORE_ADJUSTMENT, min(MAX_SCORE_ADJUSTMENT, avg_return / 10.0))
    return 1.0 + adjustment
