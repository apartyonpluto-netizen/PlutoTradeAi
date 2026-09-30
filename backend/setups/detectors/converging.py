"""Triangles and wedges: lines fitted through confirmed minor swing highs
and lows, classified by their slopes."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .. import model
from ..engine import build_detection, resolve
from ._common import DAILY_AND_INTRADAY, fit_line

_SOURCES = ("StockCharts ChartSchool - Ascending/Descending/Symmetrical Triangle, Rising/Falling Wedge",
            "T. Bulkowski, Encyclopedia of Chart Patterns - triangles and wedges (breakout usually 2/3-3/4 of the way to the apex)")

SHAPES = {
    "ascending_triangle": ("Ascending triangle", "long", "flat highs (total change at most 0.6 ATR) and rising lows (at least 1.0 ATR)", 3),
    "descending_triangle": ("Descending triangle", "short", "flat lows (at most 0.6 ATR) and falling highs (at least 1.0 ATR)", 3),
    "symmetrical_triangle": ("Symmetrical triangle", "both", "falling highs and rising lows (each at least 0.6 ATR over the span)", 3),
    "rising_wedge": ("Rising wedge", "short", "highs and lows both rising at least 0.6 ATR, lows rising faster (converging)", 3),
    "falling_wedge": ("Falling wedge", "long", "highs and lows both falling at least 0.6 ATR, highs falling faster (converging)", 3),
}


def _spec(shape_id: str) -> model.DetectorSpec:
    name, direction, rule, specificity = SHAPES[shape_id]
    breaks = {"long": "a close above the upper line", "short": "a close below the lower line", "both": "a close beyond either line"}[direction]
    return model.DetectorSpec(
        id=shape_id, version="1.0.0", name=name, family="converging", direction=direction, timeframes=DAILY_AND_INTRADAY,
        min_bars=50, specificity=specificity,
        structure=f"At least 2 confirmed minor swing highs and 2 swing lows (4-6 alternating swings) whose fitted lines show {rule}; every swing within 0.5 ATR of its line and closes contained within 0.3 ATR of the lines; the lines converge ahead of the last swing.",
        prior_trend="Not required (wedges are read as reversals of the direction they slope in).",
        swing_rules="Confirmed minor swings only.", duration="At least 10 bars from first to last swing.",
        tolerances="Flat = total change of at most 0.6 ATR over the span; swing fit 0.5 ATR; containment 0.3 ATR; breakout buffer 0.1 ATR.",
        volume="Breakout bar volume at least 1.3x the prior 20-bar mean.",
        confirmation=f"{breaks[0].upper() + breaks[1:]} (plus 0.1 ATR) on breakout volume.",
        invalidation="Before confirming: a close beyond the opposite line. After: a close back beyond the last swing on the opposite side.",
        expiration="Expires unconfirmed once price has travelled 80% of the way from the first swing to the apex; actionable 3 bars after confirmation.",
        regimes=("any",), data_requirements="At least 50 bars.",
        interpretation="Target: pattern height at its start projected from the breakout (triangles); the wedge's starting level (wedges).",
        ambiguity="Some authors require 5 touches; this version requires 4 swings. Symmetrical triangles are direction-neutral until they break.",
        sources=_SOURCES,
        parameters={"flat_atr": 0.6, "sloped_atr": 1.0 if "triangle" in shape_id and shape_id != "symmetrical_triangle" else 0.6},
    )


def _classify(us_total: float, ls_total: float, a: float) -> Optional[str]:
    flat = 0.6 * a
    if abs(us_total) <= flat and ls_total >= 1.0 * a:
        return "ascending_triangle"
    if abs(ls_total) <= flat and us_total <= -1.0 * a:
        return "descending_triangle"
    if us_total <= -flat and ls_total >= flat:
        return "symmetrical_triangle"
    if us_total >= flat and ls_total >= flat and ls_total > us_total:
        return "rising_wedge"
    if us_total <= -flat and ls_total <= -flat and us_total < ls_total:
        return "falling_wedge"
    return None


def _find(ctx) -> Optional[Dict[str, Any]]:
    swings = [p for p in ctx.confirmed_swings("minor") if p.index >= ctx.last - 120]
    for count in (6, 5, 4):
        if len(swings) < count:
            continue
        run = swings[-count:]
        highs = [(p.index, p.price) for p in run if p.kind == "H"]
        lows = [(p.index, p.price) for p in run if p.kind == "L"]
        if len(highs) < 2 or len(lows) < 2:
            continue
        span = run[-1].index - run[0].index
        if span < 10:
            continue
        a = float(ctx.atr[run[-1].index])
        us, ui = fit_line(highs)
        ls, li = fit_line(lows)
        if any(abs(price - (us * i + ui)) > 0.5 * a for i, price in highs) or any(abs(price - (ls * i + li)) > 0.5 * a for i, price in lows):
            continue
        shape = _classify(us * span, ls * span, a)
        if shape is None or us == ls:
            continue
        apex = (li - ui) / (us - ls)
        if apex <= run[-1].index:
            continue
        ready = run[-1].confirmed_index
        closes = ctx.c[run[0].index:ready + 1]
        idx = range(run[0].index, ready + 1)
        if any(c > us * i + ui + 0.3 * ctx.atr[i] or c < ls * i + li - 0.3 * ctx.atr[i] for i, c in zip(idx, closes)):
            continue
        return {"shape": shape, "run": run, "upper": (us, ui), "lower": (ls, li), "apex": apex, "atr": a, "ready": ready}
    return None


def _detect(ctx, spec) -> List[Dict[str, Any]]:
    found = _find(ctx)
    if not found or found["shape"] != spec.id:
        return []
    run, (us, ui), (ls, li), a = found["run"], found["upper"], found["lower"], found["atr"]
    upper = lambda i: us * i + ui
    lower = lambda i: ls * i + li
    start = run[0].index
    height = upper(start) - lower(start)
    expiry = int(start + 0.8 * (found["apex"] - start))
    last_high = max((p for p in run if p.kind == "H"), key=lambda p: p.index)
    last_low = max((p for p in run if p.kind == "L"), key=lambda p: p.index)
    wedge = "wedge" in spec.id

    def one(direction: str):
        up = direction == "long"
        trigger = upper if up else lower
        opposite = lower if up else upper
        post_invalidation = last_low.price if up else last_high.price
        target_level = (run[0].price if wedge else None)
        target = (lambda i: target_level) if wedge else (lambda i: trigger(i) + (height if up else -height))
        confirmed_at = None
        for i in range(found["ready"], ctx.last + 1):
            if (ctx.c[i] - trigger(i)) * (1 if up else -1) > 0.1 * ctx.atr[i]:
                confirmed_at = i
                break
        invalidation = (lambda i: opposite(i) if confirmed_at is None or i < confirmed_at else post_invalidation)
        return resolve(ctx, direction=direction, ready_index=found["ready"], trigger=trigger, invalidation=invalidation,
                       target=target, awaiting_bars=max(1, expiry - found["ready"]), signal_bars=3, volume_ratio=1.3,
                       trigger_buffer_atr=0.1), trigger, invalidation, target

    if spec.direction == "both":
        results = {d: one(d) for d in ("long", "short")}
        chosen = next((d for d, r in results.items() if r[0]["state"] in (model.CONFIRMED,) or r[0]["confirmed_index"] is not None), None)
        if chosen is None:
            resolved = dict(results["long"][0])
            if resolved["state"] == model.INVALIDATED:
                resolved = dict(results["short"][0])
            direction, trigger, invalidation, target = "both", upper, lower, None
        else:
            direction = chosen
            resolved, trigger, invalidation, target = results[chosen]
    else:
        direction = spec.direction
        resolved, trigger, invalidation, target = one(direction)
    line_end = max(ctx.last, run[-1].index)
    return [build_detection(
        ctx, spec, direction=direction, resolved=resolved, anchor_index=start, start_index=start, end_index=run[-1].index,
        points=[ctx.pivot_point("swing high" if p.kind == "H" else "swing low", p) for p in run],
        lines=[ctx.segment("upper line", start, upper(start), line_end, upper(line_end), kind="trigger" if direction in ("long", "both") else "boundary"),
               ctx.segment("lower line", start, lower(start), line_end, lower(line_end), kind="trigger" if direction in ("short", "both") else "boundary")],
        trigger=trigger, invalidation=invalidation, target=target,
        trigger_rule={"long": "close above the upper line + 0.1 ATR on 1.3x volume", "short": "close below the lower line - 0.1 ATR on 1.3x volume",
                      "both": "close beyond either line by 0.1 ATR on 1.3x volume"}[direction],
        measurements={"upper_change_atr": us * (run[-1].index - start) / a, "lower_change_atr": ls * (run[-1].index - start) / a,
                      "bars_to_apex": found["apex"] - ctx.last, "height_at_start": height},
        evidence=[f"{len(run)} swings fit within 0.5 ATR of the lines"],
    )]


for _shape in SHAPES:
    model.detector(_spec(_shape))(_detect)
