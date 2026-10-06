"""Structured strategy form. Rules are a closed catalog so a description,
a CSV, or a model can only fill in fields the backtest knows how to run."""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from trade_planner import MIN_TRADES_FOR_PROBABILITY

STYLES = (
    "breakout",
    "pullback",
    "reversal",
    "mean_reversion",
    "momentum",
    "vwap",
    "gap",
    "trend",
    "support",
    "resistance",
    "custom",
)

TIMEFRAMES = ("daily", "intraday")
DIRECTIONS = ("long", "short", "both")
INSTRUMENTS = ("equity", "options", "both")
TARGET_TYPES = ("percent", "r_multiple", "none")

# Daily-bar rules only. Intraday ideas (VWAP) can be named, and the
# backtest says it did not reconstruct them.
ENTRY_RULES: Dict[str, Dict[str, Any]] = {
    "rsi_below": {"label": "RSI 14 below", "value": True, "min": 1, "max": 99},
    "rsi_above": {"label": "RSI 14 above", "value": True, "min": 1, "max": 99},
    "price_above_ema20": {"label": "Close above EMA 20", "value": False},
    "price_below_ema20": {"label": "Close below EMA 20", "value": False},
    "price_above_ema50": {"label": "Close above EMA 50", "value": False},
    "price_below_ema50": {"label": "Close below EMA 50", "value": False},
    "ema_stack_up": {"label": "EMA 9 above EMA 20 above EMA 50", "value": False},
    "ema_stack_down": {"label": "EMA 9 below EMA 20 below EMA 50", "value": False},
    "near_20d_high": {"label": "Close within 1.5% of the 20-day high", "value": False},
    "near_20d_low": {"label": "Close within 1.5% of the 20-day low", "value": False},
    "breakout_20d": {"label": "Close above the prior 20-day high", "value": False},
    "breakdown_20d": {"label": "Close below the prior 20-day low", "value": False},
    "relative_volume_above": {"label": "Relative volume above", "value": True, "min": 0.1, "max": 20},
    "day_change_above": {"label": "Day change percent above", "value": True, "min": -30, "max": 30},
    "day_change_below": {"label": "Day change percent below", "value": True, "min": -30, "max": 30},
    "gap_above": {"label": "Opening gap percent above", "value": True, "min": -30, "max": 30},
    "gap_below": {"label": "Opening gap percent below", "value": True, "min": -30, "max": 30},
}

MAX_RULES = 8
MAX_TICKERS = 5
MAX_NAME_LENGTH = 80
MAX_DESCRIPTION_LENGTH = 4000
MAX_IMPORTED_TRADES = 500
MIN_HOLD_DAYS = 1
MAX_HOLD_DAYS = 20
MAX_STRATEGIES_PER_USER = 25

_TICKER = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")

# Families the built-in strategy brain actually scores. Kept here so a
# renamed strategy does not silently inherit another strategy's trades
# without the page saying so.
BUILTIN_STRATEGY_NAMES = (
    "Breakout",
    "Pullback",
    "Reversal",
    "VWAP Bounce",
    "Gap and Go",
    "Trend Continuation",
    "Support Bounce",
    "Resistance Rejection",
    "Momentum",
    "Mean Reversion",
    "Earnings Momentum",
    "Options Momentum",
)


def scoring_enabled() -> bool:
    """Paper-scoring opt-in. Unset means off. Never read by the scan."""
    return os.environ.get("PLUTO_USER_STRATEGY_SCORING", "").strip().lower() in {"1", "true", "yes", "on"}


def rule_catalog() -> List[Dict[str, Any]]:
    return [
        {"kind": kind, "label": spec["label"], "needs_value": bool(spec["value"])}
        for kind, spec in ENTRY_RULES.items()
    ]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_text(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]


def _optional_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError("A numeric field was not a number.") from error
    if number != number or number in (float("inf"), float("-inf")):
        raise ValueError("A numeric field was not a finite number.")
    return number


def _tickers(value: Any) -> List[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        parts = re.split(r"[\s,]+", value)
    elif isinstance(value, (list, tuple)):
        parts = [str(item) for item in value]
    else:
        raise ValueError("Tickers must be a list or a comma-separated list.")
    cleaned: List[str] = []
    for part in parts:
        ticker = part.strip().upper()
        if not ticker:
            continue
        if not _TICKER.match(ticker):
            raise ValueError(f"Ticker {ticker} is not a symbol this form accepts.")
        if ticker not in cleaned:
            cleaned.append(ticker)
    if len(cleaned) > MAX_TICKERS:
        raise ValueError(f"Use at most {MAX_TICKERS} tickers. The built-in backtest has the same cap.")
    return cleaned


def _entry_rules(value: Any) -> List[Dict[str, Any]]:
    if not value:
        return []
    if not isinstance(value, list):
        raise ValueError("Entry rules must be a list.")
    rules: List[Dict[str, Any]] = []
    for raw in value:
        if not isinstance(raw, dict):
            raise ValueError("Each entry rule must be an object.")
        kind = str(raw.get("kind") or "").strip()
        if not kind:
            continue
        spec = ENTRY_RULES.get(kind)
        if spec is None:
            raise ValueError(f"Unknown entry rule {kind}.")
        rule: Dict[str, Any] = {"kind": kind, "label": spec["label"]}
        if spec["value"]:
            number = _optional_float(raw.get("value"))
            if number is None:
                raise ValueError(f"{spec['label']} needs a number.")
            if number < spec["min"] or number > spec["max"]:
                raise ValueError(f"{spec['label']} must be between {spec['min']} and {spec['max']}.")
            rule["value"] = number
        rules.append(rule)
    if len(rules) > MAX_RULES:
        raise ValueError(f"Use at most {MAX_RULES} entry rules.")
    return rules


def _target(payload: Dict[str, Any]) -> Dict[str, Any]:
    raw = payload.get("target")
    if isinstance(raw, dict):
        kind = str(raw.get("type") or "none").strip()
        value = raw.get("value")
    else:
        kind = str(payload.get("target_type") or "none").strip()
        value = payload.get("target_value")
    if kind not in TARGET_TYPES:
        raise ValueError("Target type must be percent, r_multiple, or none.")
    if kind == "none":
        return {"type": "none", "value": None}
    number = _optional_float(value)
    if number is None or number <= 0:
        raise ValueError("A target needs a positive number.")
    if kind == "percent" and number > 100:
        raise ValueError("A percent target cannot be above 100.")
    if kind == "r_multiple" and number > 20:
        raise ValueError("A reward multiple cannot be above 20.")
    return {"type": kind, "value": number}


def _stop(payload: Dict[str, Any]) -> Optional[float]:
    number = _optional_float(payload.get("stop_percent"))
    if number is None:
        return None
    if number < 0.1 or number > 25:
        raise ValueError("Stop percent must be between 0.1 and 25.")
    return number


def _hold(payload: Dict[str, Any], existing: Optional[Dict[str, Any]]) -> int:
    raw = payload.get("max_hold_days", None if existing is None else existing.get("max_hold_days", 5))
    try:
        days = int(raw if raw not in (None, "") else 5)
    except (TypeError, ValueError) as error:
        raise ValueError("Hold days must be a whole number.") from error
    if days < MIN_HOLD_DAYS or days > MAX_HOLD_DAYS:
        raise ValueError(f"Hold days must be between {MIN_HOLD_DAYS} and {MAX_HOLD_DAYS}.")
    return days


def _choice(value: Any, allowed: tuple, default: str, label: str) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return default
    if text not in allowed:
        raise ValueError(f"{label} must be one of: {', '.join(allowed)}.")
    return text


def _imported_trades(value: Any) -> List[Dict[str, Any]]:
    if not value:
        return []
    if not isinstance(value, list):
        raise ValueError("Imported trades must be a list.")
    trades: List[Dict[str, Any]] = []
    for raw in value[:MAX_IMPORTED_TRADES]:
        if not isinstance(raw, dict):
            continue
        ticker = str(raw.get("ticker") or "").strip().upper()
        if ticker and not _TICKER.match(ticker):
            continue
        side = str(raw.get("side") or "long").strip().lower()
        if side not in {"long", "short"}:
            side = "long"
        pnl = _optional_float(raw.get("pnl_percent"))
        trades.append(
            {
                "ticker": ticker,
                "side": side,
                "entry_date": str(raw.get("entry_date") or "")[:10],
                "exit_date": str(raw.get("exit_date") or "")[:10],
                "entry_price": _optional_float(raw.get("entry_price")),
                "exit_price": _optional_float(raw.get("exit_price")),
                "pnl_percent": None if pnl is None else round(pnl, 4),
                "closed": bool(raw.get("closed")),
                "source": str(raw.get("source") or "import")[:40],
            }
        )
    return trades


def normalize_strategy(payload: Dict[str, Any], existing: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Validates a form or model payload into the stored shape.

    A strategy may be saved before it has entry rules (a description or an
    import). The backtest refuses to run until a rule exists.
    """
    if not isinstance(payload, dict):
        raise ValueError("Strategy must be an object.")
    existing = existing or {}
    name = _clean_text(payload.get("name", existing.get("name", "")), MAX_NAME_LENGTH)
    if not name:
        raise ValueError("A strategy needs a name.")
    stop = _stop(payload if "stop_percent" in payload else existing)
    target = _target(payload if ("target" in payload or "target_type" in payload) else {"target": existing.get("target")})
    if target["type"] == "r_multiple" and stop is None:
        raise ValueError("A reward-multiple target needs a stop percent, so R is defined.")
    rules = _entry_rules(payload["entry_rules"]) if "entry_rules" in payload else _entry_rules(existing.get("entry_rules"))
    if "paper_opt_in" in payload:
        paper_opt_in = payload.get("paper_opt_in") in (True, "true", "on", "1", 1)
        if paper_opt_in and not scoring_enabled():
            raise ValueError(
                "Paper scoring is off. Set PLUTO_USER_STRATEGY_SCORING to turn the opt-in on. "
                "Leaving it off means this strategy is not read by the engine."
            )
    else:
        paper_opt_in = bool(existing.get("paper_opt_in", False))
    source = str(payload.get("source") or existing.get("source") or "form")
    if source not in {"form", "plain_english", "keyword_draft", "trade_import"}:
        source = "form"
    imported = _imported_trades(payload["imported_trades"]) if "imported_trades" in payload else _imported_trades(existing.get("imported_trades"))
    strategy = {
        "name": name,
        "description": _clean_text(payload.get("description", existing.get("description", "")), MAX_DESCRIPTION_LENGTH),
        "source": source,
        "style": _choice(payload.get("style", existing.get("style")), STYLES, "custom", "Style"),
        "direction": _choice(payload.get("direction", existing.get("direction")), DIRECTIONS, "long", "Direction"),
        "timeframe": _choice(payload.get("timeframe", existing.get("timeframe")), TIMEFRAMES, "daily", "Timeframe"),
        "instruments": _choice(payload.get("instruments", existing.get("instruments")), INSTRUMENTS, "equity", "Instruments"),
        "tickers": _tickers(payload["tickers"]) if "tickers" in payload else _tickers(existing.get("tickers") or []),
        "entry_rules": rules,
        "stop_percent": stop,
        "target": target,
        "max_hold_days": _hold(payload if "max_hold_days" in payload else existing, existing),
        "paper_opt_in": paper_opt_in,
        "imported_trades": imported,
        "import_note": _clean_text(payload.get("import_note", existing.get("import_note", "")), 500),
        "created_at": existing.get("created_at") or _now(),
        "updated_at": _now(),
    }
    return strategy


def infer_style(strategy: Dict[str, Any]) -> str:
    """Style used for similarity. An explicit style wins. Otherwise the
    entry rules are read, and anything ambiguous stays custom."""
    style = str(strategy.get("style") or "custom")
    if style != "custom":
        return style
    kinds = {rule.get("kind") for rule in strategy.get("entry_rules") or []}
    if "breakout_20d" in kinds or "near_20d_high" in kinds:
        return "breakout"
    if "breakdown_20d" in kinds or "near_20d_low" in kinds:
        return "support" if "near_20d_low" in kinds else "resistance"
    if "gap_above" in kinds or "gap_below" in kinds:
        return "gap"
    if "rsi_below" in kinds or "rsi_above" in kinds:
        return "mean_reversion"
    if "ema_stack_up" in kinds or "price_above_ema20" in kinds:
        return "trend"
    if "relative_volume_above" in kinds or "day_change_above" in kinds or "day_change_below" in kinds:
        return "momentum"
    return "custom"


def hold_bucket(days: int) -> str:
    if days <= 1:
        return "day"
    if days <= 5:
        return "swing"
    return "position"


def form_slots(strategy: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Always MAX_RULES rows so a rule can be added without JavaScript."""
    current = list((strategy or {}).get("entry_rules") or [])
    slots = []
    for index in range(MAX_RULES):
        rule = current[index] if index < len(current) else {}
        slots.append({"kind": rule.get("kind", ""), "value": "" if rule.get("value") is None else rule.get("value")})
    return slots
