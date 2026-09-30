"""Trend structure on MINOR swings: higher-high/higher-low (and lower-high/
lower-low) structure, and the stricter "staircase" (an informal name; the
interpretation used is documented in the spec)."""

from __future__ import annotations

from typing import Any, Dict, List

import numpy as np

from .. import model
from ..engine import build_detection, resolve
from ._common import DAILY_AND_INTRADAY


def _structure_spec(up: bool, staircase: bool) -> model.DetectorSpec:
    trend = "rising" if up else "falling"
    if staircase:
        name = f"{'Rising' if up else 'Falling'} staircase"
        structure = (f"At least three consecutive {'higher highs and three higher lows' if up else 'lower lows and three lower highs'} "
                     "in alternating confirmed minor swings, each pullback retracing 15-70% of the prior leg, with step sizes "
                     "reasonably even (coefficient of variation of leg sizes at most 0.6).")
        interpretation = ("'Staircase' is informal. Interpreted as an orderly trend: a longer run of the same higher-high/higher-low "
                          "structure with bounded, repeating pullbacks. It overlaps the plain structure detector by design and is "
                          "grouped with it as one opportunity.")
        ambiguity = "No standard definition exists; step count and retracement band are this library's choice."
    else:
        name = "Higher highs / higher lows" if up else "Lower highs / lower lows"
        structure = (f"The last two confirmed minor swing highs and the last two confirmed swing lows are both {trend} "
                     "by at least 0.25 ATR (market structure).")
        interpretation = "Dow-theory market structure applied to ATR-scaled swings. Continuation entry on a new swing extreme."
        ambiguity = "Structure is a context signal as much as an entry; entries on it alone are evaluated like any other setup."
    return model.DetectorSpec(
        id=("staircase_" if staircase else "structure_") + ("up" if up else "down"),
        version="1.0.0",
        name=name,
        family="structure",
        direction="long" if up else "short",
        timeframes=DAILY_AND_INTRADAY,
        min_bars=40,
        specificity=2 if staircase else 1,
        structure=structure,
        prior_trend="Defined by the structure itself.",
        swing_rules="Confirmed minor ATR zigzag swings only (1.5 ATR daily/hourly, 2 ATR intraday); provisional swings are never used.",
        duration="No minimum beyond the swings themselves.",
        tolerances="Each new swing must exceed the previous one by 0.25 ATR.",
        volume="Not used.",
        confirmation=f"A close {'above the last swing high' if up else 'below the last swing low'} (a new {'higher high' if up else 'lower low'}).",
        invalidation=f"A close {'below the last higher low' if up else 'above the last lower high'} (break of structure).",
        expiration="No confirmation within 20 bars of the last swing; actionable for 3 bars after confirmation; target (one prior leg) ends it.",
        regimes=("uptrend", "range") if up else ("downtrend", "range"),
        data_requirements="At least 40 bars.",
        interpretation=interpretation,
        ambiguity=ambiguity,
        sources=("C. Dow / Dow Theory (trend of highs and lows)", "StockCharts ChartSchool - Dow Theory; Support and Resistance"),
        parameters={"step_atr": 0.25, "min_steps": 3 if staircase else 2},
    )


def _structure(ctx, spec: model.DetectorSpec, up: bool, staircase: bool) -> List[Dict[str, Any]]:
    swings = ctx.confirmed_swings("minor")
    needed = 6 if staircase else 4
    if len(swings) < needed:
        return []
    run = swings[-needed:]
    highs = [p for p in run if p.kind == "H"]
    lows = [p for p in run if p.kind == "L"]
    a = float(ctx.atr[run[-1].index])
    s = 1.0 if up else -1.0
    step = 0.25 * a
    if not all(s * (b.price - x.price) >= step for x, b in zip(highs, highs[1:])):
        return []
    if not all(s * (b.price - x.price) >= step for x, b in zip(lows, lows[1:])):
        return []
    measurements: Dict[str, Any] = {"swings_used": needed}
    if staircase:
        legs = [s * (b.price - x.price) for x, b in zip(run, run[1:])]
        impulses = [leg for leg in legs if leg > 0]
        ratios = [-legs[k + 1] / legs[k] for k in range(len(legs) - 1) if legs[k] > 0 and legs[k + 1] < 0]
        if not ratios or not all(0.15 <= r <= 0.70 for r in ratios):
            return []
        cv = float(np.std(impulses) / np.mean(impulses)) if impulses and np.mean(impulses) > 0 else 1.0
        if cv > 0.6:
            return []
        measurements.update({"retracement_ratios": ", ".join(f"{r:.2f}" for r in ratios), "leg_size_cv": cv})
    last_extreme = highs[-1] if up else lows[-1]
    last_defence = lows[-1] if up else highs[-1]
    leg = abs(last_extreme.price - last_defence.price)
    target = last_extreme.price + s * leg
    resolved = resolve(ctx, direction="long" if up else "short", ready_index=run[-1].confirmed_index, trigger=last_extreme.price,
                       invalidation=last_defence.price, target=target, awaiting_bars=20, signal_bars=3)
    labels = [("HH" if up else "LH") if p.kind == "H" else ("HL" if up else "LL") for p in run]
    labels[0] = "swing high" if run[0].kind == "H" else "swing low"
    end = max(ctx.last, run[-1].index)
    return [build_detection(
        ctx, spec, direction="long" if up else "short", resolved=resolved, anchor_index=run[0].index, start_index=run[0].index,
        end_index=run[-1].index, points=[ctx.pivot_point(label, p) for label, p in zip(labels, run)],
        lines=[ctx.segment("trigger", last_extreme.index, last_extreme.price, end, last_extreme.price, kind="trigger"),
               ctx.segment("structure defence", last_defence.index, last_defence.price, end, last_defence.price, kind="invalidation")],
        trigger=last_extreme.price, invalidation=last_defence.price, target=target,
        trigger_rule=f"close {'above' if up else 'below'} {last_extreme.price:.2f}",
        measurements=measurements,
        evidence=[f"{len(highs)} {'rising' if up else 'falling'} swing highs and {len(lows)} {'rising' if up else 'falling'} swing lows"],
    )]


for _up in (True, False):
    for _stair in (False, True):
        model.detector(_structure_spec(_up, _stair))(lambda ctx, spec, _u=_up, _s=_stair: _structure(ctx, spec, _u, _s))
