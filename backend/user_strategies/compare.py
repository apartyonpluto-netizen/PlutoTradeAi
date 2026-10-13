"""How a user's strategy sits next to built-in strategies, the setup library,
and other users' strategies in anonymous aggregate.

Numbers keep their source. A win probability is copied from
trade_planner.evidence_from_returns, which stays silent below 30 reconciled
closed trades. Backtests are labeled backtests. A small sample is described
as not reliably estimated, the same bar the trade planner uses.
"""

from __future__ import annotations

from statistics import median
from typing import Any, Dict, List, Optional

from calibration import closed_trade_returns_by_strategy, get_calibration
from setups import registered
from setups.evidence import lookup as evidence_lookup
from trade_planner import MIN_TRADES_FOR_PROBABILITY, evidence_from_returns

from .model import BUILTIN_STRATEGY_NAMES, hold_bucket, infer_style
from .store import aggregate_rows

# Hand description of the twelve built-in strategies, taken from the
# timeframe and direction each one uses in strategy_brain. This is a
# similarity key, not a performance claim.
BUILTIN_PROFILES: List[Dict[str, Any]] = [
    {"name": "Breakout", "style": "breakout", "direction": "long", "timeframe": "intraday", "hold_bucket": "day", "instrument": "equity"},
    {"name": "Pullback", "style": "pullback", "direction": "long", "timeframe": "daily", "hold_bucket": "swing", "instrument": "equity"},
    {"name": "Reversal", "style": "reversal", "direction": "long", "timeframe": "intraday", "hold_bucket": "day", "instrument": "equity"},
    {"name": "VWAP Bounce", "style": "vwap", "direction": "long", "timeframe": "intraday", "hold_bucket": "day", "instrument": "equity"},
    {"name": "Gap and Go", "style": "gap", "direction": "long", "timeframe": "intraday", "hold_bucket": "day", "instrument": "equity"},
    {"name": "Trend Continuation", "style": "trend", "direction": "long", "timeframe": "daily", "hold_bucket": "swing", "instrument": "equity"},
    {"name": "Support Bounce", "style": "support", "direction": "long", "timeframe": "intraday", "hold_bucket": "swing", "instrument": "equity"},
    {"name": "Resistance Rejection", "style": "resistance", "direction": "short", "timeframe": "intraday", "hold_bucket": "day", "instrument": "equity"},
    {"name": "Momentum", "style": "momentum", "direction": "both", "timeframe": "intraday", "hold_bucket": "day", "instrument": "equity"},
    {"name": "Mean Reversion", "style": "mean_reversion", "direction": "both", "timeframe": "intraday", "hold_bucket": "swing", "instrument": "equity"},
    {"name": "Earnings Momentum", "style": "momentum", "direction": "both", "timeframe": "intraday", "hold_bucket": "day", "instrument": "equity"},
    {"name": "Options Momentum", "style": "momentum", "direction": "both", "timeframe": "intraday", "hold_bucket": "day", "instrument": "options"},
]

RELATED_STYLES = {
    "pullback": {"trend", "support"},
    "trend": {"pullback"},
    "support": {"reversal", "pullback"},
    "reversal": {"support", "mean_reversion"},
    "mean_reversion": {"reversal", "vwap"},
    "gap": {"momentum"},
    "momentum": {"gap", "breakout"},
    "breakout": {"momentum"},
    "resistance": {"reversal"},
    "vwap": {"mean_reversion"},
}

MIN_COHORT = 3
MIN_SETUP_SAMPLE = 30


def _direction_points(left: str, right: str) -> int:
    if left == right or left == "both" or right == "both":
        return 20
    return 0


def _style_points(left: str, right: str) -> int:
    if left == "custom" or right == "custom":
        return 0
    if left == right:
        return 50
    if right in RELATED_STYLES.get(left, set()):
        return 25
    return 0


def similarity(user: Dict[str, Any], other: Dict[str, Any]) -> int:
    score = _style_points(user["style"], other["style"])
    score += _direction_points(user["direction"], other["direction"])
    score += 15 if user["timeframe"] == other["timeframe"] else 0
    score += 15 if user["hold_bucket"] == other["hold_bucket"] else 0
    if user.get("instrument") == "options" and other.get("instrument") == "options":
        score += 10
    return min(100, score)


def _user_profile(strategy: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "style": infer_style(strategy),
        "direction": strategy.get("direction") or "long",
        "timeframe": strategy.get("timeframe") or "daily",
        "hold_bucket": hold_bucket(int(strategy.get("max_hold_days") or 5)),
        "instrument": strategy.get("instruments") or "equity",
    }


def _why(user: Dict[str, Any], other: Dict[str, Any]) -> str:
    parts = []
    if user["style"] == other["style"] and user["style"] != "custom":
        parts.append(f"same {user['style'].replace('_', ' ')} style")
    elif other["style"] in RELATED_STYLES.get(user["style"], set()):
        parts.append(f"related style ({other['style'].replace('_', ' ')})")
    if user["direction"] == other["direction"]:
        parts.append(f"{user['direction']} direction")
    if user["timeframe"] == other["timeframe"]:
        parts.append(f"{user['timeframe']} timeframe")
    if user["hold_bucket"] == other["hold_bucket"]:
        parts.append(f"{user['hold_bucket']} hold")
    return ", ".join(parts) if parts else "little overlap with the saved rules"


def _backtest_block(stats: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not stats or not stats.get("trade_count"):
        return None
    return {
        "label": "Backtest",
        "is_backtest": True,
        "trade_count": stats.get("trade_count"),
        "win_rate_percent": stats.get("win_rate_percent"),
        "avg_return_percent": stats.get("avg_return_percent"),
        "note": "Backtest from the built-in engine (fixed hold, not a stop). Not a probability.",
    }


def _rank_key(item: Dict[str, Any]) -> tuple:
    real = item.get("real") or {}
    backtest = item.get("backtest") or {}
    if real.get("reliable") and real.get("mean_net_return_percent") is not None:
        return (2, float(real["mean_net_return_percent"]), item["similarity"])
    if backtest.get("trade_count") and backtest.get("avg_return_percent") is not None:
        return (1, float(backtest["avg_return_percent"]), item["similarity"])
    return (0, 0.0, item["similarity"])


def _builtin_rows(profile: Dict[str, Any]) -> List[Dict[str, Any]]:
    real_returns = closed_trade_returns_by_strategy()
    calibration = get_calibration()
    backtests = calibration.get("strategy_stats") or {}
    rows = []
    for builtin in BUILTIN_PROFILES:
        if builtin["name"] not in BUILTIN_STRATEGY_NAMES:
            continue
        score = similarity(profile, builtin)
        evidence = evidence_from_returns(
            real_returns.get(builtin["name"], []),
            strategy=builtin["name"],
            source="reconciled closed trades of this built-in name, all accounts on this deployment",
        )
        rows.append(
            {
                "name": builtin["name"],
                "kind": "built_in",
                "similarity": score,
                "why": _why(profile, builtin),
                "real": evidence,
                "backtest": _backtest_block(backtests.get(builtin["name"])),
            }
        )
    rows.sort(key=_rank_key, reverse=True)
    return rows


def _timeframe_for(spec_timeframes: tuple, user_timeframe: str) -> Optional[str]:
    names = set(spec_timeframes)
    if user_timeframe == "daily":
        return "1d" if "1d" in names else None
    intraday = [name for name in spec_timeframes if name != "1d"]
    return intraday[0] if intraday else ("1d" if "1d" in names else None)


_FAMILY_STYLE = {
    "breakout": "breakout",
    "continuation": "pullback",
    "structure": "trend",
    "reversal": "reversal",
    "mean_reversion": "mean_reversion",
    "vwap": "vwap",
    "failure": "resistance",
    "range": "support",
    "converging": "custom",
}


def _setup_rows(profile: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = []
    for spec, _fn in registered():
        timeframe = _timeframe_for(spec.timeframes, profile["timeframe"])
        if timeframe is None:
            continue
        other = {
            "style": _FAMILY_STYLE.get(spec.family, "custom"),
            "direction": spec.direction if spec.direction in {"long", "short", "both"} else "both",
            "timeframe": "daily" if timeframe == "1d" else "intraday",
            "hold_bucket": profile["hold_bucket"],
            "instrument": "equity",
        }
        score = similarity(profile, other)
        if score < 40:
            continue
        evidence = evidence_lookup(spec.id, spec.version, timeframe)
        out_of_sample = evidence.get("out_of_sample") or {}
        forward = evidence.get("forward") or {}
        sample = int(out_of_sample.get("n") or 0)
        forward_n = int(forward.get("n") or 0)
        rows.append(
            {
                "name": spec.name,
                "detector_id": spec.id,
                "kind": "setup",
                "family": spec.family,
                "similarity": score,
                "why": f"{spec.family} setup, {spec.direction}, {timeframe}",
                "timeframe": timeframe,
                "status": evidence.get("status"),
                "status_note": "Setup-library status. Not a win probability from closed trades.",
                "out_of_sample": {
                    "label": "Setup library out-of-sample backtest" if sample else "No out-of-sample backtest stored",
                    "is_backtest": True,
                    "n": sample,
                    "mean_r": out_of_sample.get("mean_r") if sample else None,
                    "usable_for_rank": sample >= MIN_SETUP_SAMPLE,
                },
                "forward": {
                    "label": "Setup library forward results, after the signal. Not this account's closed trades.",
                    "n": forward_n,
                    "mean_r": forward.get("mean_r") if forward_n else None,
                    "usable_for_rank": forward_n >= MIN_SETUP_SAMPLE,
                },
            }
        )

    def setup_key(item: Dict[str, Any]) -> tuple:
        if item["forward"]["usable_for_rank"] and item["forward"]["mean_r"] is not None:
            return (2, float(item["forward"]["mean_r"]), item["similarity"])
        if item["out_of_sample"]["usable_for_rank"] and item["out_of_sample"]["mean_r"] is not None:
            return (1, float(item["out_of_sample"]["mean_r"]), item["similarity"])
        return (0, 0.0, item["similarity"])

    rows.sort(key=setup_key, reverse=True)
    return rows[:8]


def _cohort(profile: Dict[str, Any], exclude: set) -> Dict[str, Any]:
    rows = [
        row for row in aggregate_rows()
        if row.get("anon_id") not in exclude and row.get("style") == profile["style"] and profile["style"] != "custom"
    ]
    base = {
        "style": profile["style"],
        "strategies": len(rows),
        "minimum_to_show_numbers": MIN_COHORT,
        "shown": False,
        "note": "",
        "backtest": None,
        "real": None,
    }
    if profile["style"] == "custom":
        base["note"] = "A custom style is not grouped with other people, because the rules may not mean the same thing."
        return base
    if len(rows) < MIN_COHORT:
        base["note"] = (
            f"{len(rows)} other saved strateg{'y' if len(rows) == 1 else 'ies'} share this style. "
            f"Numbers stay hidden below {MIN_COHORT}, so one person's result cannot be read back out."
        )
        return base
    backtested = [row for row in rows if row.get("backtest_trade_count")]
    real_ready = [row for row in rows if int(row.get("real_trade_count") or 0) >= MIN_TRADES_FOR_PROBABILITY]
    pooled_real = sum(int(row.get("real_trade_count") or 0) for row in rows)
    base["shown"] = True
    base["note"] = "Anonymous aggregate of other users' strategies in this style. No names and no tickers."
    if len(backtested) >= MIN_COHORT:
        base["backtest"] = {
            "label": "Backtest",
            "is_backtest": True,
            "strategies": len(backtested),
            "median_avg_return_percent": round(median(float(row["backtest_avg_return_percent"]) for row in backtested), 2),
            "note": "Median of each strategy's own backtest average return. A backtest is not a probability.",
        }
    else:
        base["backtest"] = {
            "label": "Backtest",
            "is_backtest": True,
            "strategies": len(backtested),
            "median_avg_return_percent": None,
            "note": f"Fewer than {MIN_COHORT} of these strategies have a backtest, so no backtest median is shown.",
        }
    if pooled_real >= MIN_TRADES_FOR_PROBABILITY and real_ready:
        wins = sum(int(row.get("real_win_count") or 0) for row in real_ready)
        trials = sum(int(row.get("real_trade_count") or 0) for row in real_ready)
        base["real"] = {
            "strategies": len(real_ready),
            "sample_size": trials,
            "win_rate_percent": round(wins / trials * 100, 1) if trials else None,
            "median_avg_return_percent": round(median(float(row["real_avg_return_percent"]) for row in real_ready if row.get("real_avg_return_percent") is not None), 2),
            "reliable": True,
            "note": (
                "Pooled reconciled closed trades from other users' strategies that each already have "
                f"{MIN_TRADES_FOR_PROBABILITY} or more. Not your trades."
            ),
        }
    else:
        base["real"] = {
            "strategies": len(rows),
            "sample_size": pooled_real,
            "win_rate_percent": None,
            "reliable": False,
            "note": (
                f"Probability not reliably estimated: {pooled_real} reconciled closed trade(s) in this anonymous group, "
                f"{MIN_TRADES_FOR_PROBABILITY} needed, and each included strategy must itself have {MIN_TRADES_FOR_PROBABILITY}."
            ),
        }
    return base


def _recommendations(builtins: List[Dict[str, Any]], setups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    picks: List[Dict[str, Any]] = []
    for item in builtins:
        if item["similarity"] < 50:
            continue
        real = item["real"]
        backtest = item.get("backtest") or {}
        if real.get("reliable") and (real.get("mean_net_return_percent") or 0) > 0:
            picks.append(
                {
                    "name": item["name"],
                    "kind": "built_in",
                    "similarity": item["similarity"],
                    "basis": "real_closed_trades",
                    "sample_size": real["sample_size"],
                    "figure": real["mean_net_return_percent"],
                    "figure_label": "mean net return percent on reconciled closed trades",
                    "caution": "Fits the saved style. This is the built-in strategy's closed-trade record, not a promise it will repeat.",
                }
            )
        elif backtest.get("trade_count") and (backtest.get("avg_return_percent") or 0) > 0 and not real.get("reliable"):
            picks.append(
                {
                    "name": item["name"],
                    "kind": "built_in",
                    "similarity": item["similarity"],
                    "basis": "backtest",
                    "sample_size": backtest["trade_count"],
                    "figure": backtest["avg_return_percent"],
                    "figure_label": "backtest average return percent",
                    "caution": (
                        "Fits the saved style. This figure is a backtest. "
                        + (real.get("note") or "Probability not reliably estimated from closed trades.")
                    ),
                }
            )
    for item in setups:
        if item["similarity"] < 50:
            continue
        if item["forward"]["usable_for_rank"] and (item["forward"]["mean_r"] or 0) > 0:
            picks.append(
                {
                    "name": item["name"],
                    "kind": "setup",
                    "similarity": item["similarity"],
                    "basis": "setup_forward",
                    "sample_size": item["forward"]["n"],
                    "figure": item["forward"]["mean_r"],
                    "figure_label": "setup library forward mean R",
                    "caution": "Fits the saved style. Forward results are the setup library's, not this account's closed trades.",
                }
            )
        elif item["out_of_sample"]["usable_for_rank"] and (item["out_of_sample"]["mean_r"] or 0) > 0:
            picks.append(
                {
                    "name": item["name"],
                    "kind": "setup",
                    "similarity": item["similarity"],
                    "basis": "backtest",
                    "sample_size": item["out_of_sample"]["n"],
                    "figure": item["out_of_sample"]["mean_r"],
                    "figure_label": "setup library out-of-sample backtest mean R",
                    "caution": "Fits the saved style. This figure is a backtest.",
                }
            )
    picks.sort(key=lambda item: ({"real_closed_trades": 2, "setup_forward": 1, "backtest": 0}[item["basis"]], item["similarity"]), reverse=True)
    return picks[:3]


def own_record(user_id: str, strategy: Dict[str, Any]) -> Dict[str, Any]:
    """This user's reconciled closes and their imported fills, kept apart."""
    from autonomy.closed_trades import list_closed_trades
    from calibration import _pnl_percent_for_closed_trade

    from .model import BUILTIN_STRATEGY_NAMES

    name = str(strategy.get("name") or "")
    returns: List[float] = []
    for trade in list_closed_trades(user_id):
        if str(trade.get("strategy") or "").strip().casefold() != name.strip().casefold():
            continue
        percent = _pnl_percent_for_closed_trade(trade)
        if percent is not None:
            returns.append(percent)
    reconciled = evidence_from_returns(
        returns,
        strategy=name,
        source="your reconciled closed trades recorded under this exact name",
    )
    collision = next((builtin for builtin in BUILTIN_STRATEGY_NAMES if builtin.casefold() == name.strip().casefold()), None)
    if collision:
        reconciled["name_collision"] = (
            f"This name is also the built-in strategy {collision}. "
            "Trades the scan recorded under that name are included, and they may be the built-in strategy rather than only these rules."
        )
    else:
        reconciled["name_collision"] = ""
    reconciled["attribution"] = (
        "Matched only when the closed trade's recorded strategy name is exactly this name. "
        "The scan writes built-in names. A custom name matches only trades recorded under that same name."
    )

    imported_values = [
        float(trade["pnl_percent"])
        for trade in strategy.get("imported_trades") or []
        if trade.get("closed") and trade.get("pnl_percent") is not None
    ]
    wins = [value for value in imported_values if value > 0]
    imported = {
        "sample_size": len(imported_values),
        "win_count": len(wins),
        "imported_win_rate_percent": round(len(wins) / len(imported_values) * 100, 1) if imported_values else None,
        "mean_return_percent": round(sum(imported_values) / len(imported_values), 2) if imported_values else None,
        "win_probability_percent": None,
        "reliable": False,
        "note": (
            "Imported record. Not a reconciled closed trade, so this is not a probability. "
            + (
                f"Probability not reliably estimated: {len(imported_values)} imported round trip(s), {MIN_TRADES_FOR_PROBABILITY} reconciled trades would be needed for a probability."
                if len(imported_values) < MIN_TRADES_FOR_PROBABILITY
                else "Even at this sample size, an import is not the reconciled closed-trade probability."
            )
        ),
    }
    backtest = strategy.get("last_backtest")
    return {
        "reconciled": reconciled,
        "imported": imported,
        "backtest": backtest,
        "honesty": [
            "Probability is shown only for reconciled closed trades of this strategy name, and only at 30 or more.",
            "Backtest results are simulated. They are not a probability and they are not closed trades.",
            "Imported fills are your own record. They are not the app's reconciled closed trades.",
        ],
    }


def compare_strategy(strategy: Dict[str, Any], exclude_anon_ids: set) -> Dict[str, Any]:
    profile = _user_profile(strategy)
    builtins = _builtin_rows(profile)
    setups = _setup_rows(profile)
    recommendations = _recommendations(builtins, setups)
    return {
        "profile": profile,
        "built_in": builtins,
        "setups": setups,
        "other_users": _cohort(profile, exclude_anon_ids),
        "recommendations": recommendations,
        "empty_recommendation_note": (
            ""
            if recommendations
            else "No similar strategy has a positive result with a large enough labeled sample. Nothing is recommended."
        ),
    }
