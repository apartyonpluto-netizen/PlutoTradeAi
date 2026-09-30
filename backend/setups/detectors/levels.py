"""Ranges, breakouts/breakdowns, retests, failed breakouts/reclaims and
rejections - all built on the same objective levels:

  * a RANGE is a run of confirmed minor swings whose highs sit within
    0.75 ATR of the run's top and whose lows sit within 0.75 ATR of its bottom;
  * a LEVEL is a confirmed major swing high (resistance) or low (support)
    from the last 150 bars, or a range boundary.

`s` is +1 for the upside (resistance, long breakout) and -1 for the mirror."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .. import model
from ..engine import build_detection, developing, resolve
from ._common import DAILY_AND_INTRADAY

_SOURCES = (
    "StockCharts ChartSchool - Support and Resistance; Rectangle; Breakouts and pullbacks",
    "T. Bulkowski, Encyclopedia of Chart Patterns - Rectangle tops/bottoms, throwbacks and pullbacks",
)
BREAKOUT_VOLUME = 1.5


def _spec(**kw) -> model.DetectorSpec:
    base = dict(version="1.0.0", timeframes=DAILY_AND_INTRADAY, min_bars=60, prior_trend="Not required.",
                data_requirements="At least 60 bars of OHLCV; volume rules use each bar's volume against the prior 20-bar mean.",
                sources=_SOURCES, regimes=("any",))
    base.update(kw)
    return model.DetectorSpec(**base)


# --- shared level logic ---------------------------------------------------------


def find_range(ctx) -> Optional[Dict[str, Any]]:
    swings = [p for p in ctx.confirmed_swings("minor") if p.index >= ctx.last - 150]
    for end in range(len(swings) - 1, max(len(swings) - 7, 2), -1):
        best = None
        for start in range(end - 3, -1, -1):
            run = swings[start:end + 1]
            highs = [p for p in run if p.kind == "H"]
            lows = [p for p in run if p.kind == "L"]
            if len(highs) < 2 or len(lows) < 2:
                continue
            a = float(ctx.atr[run[-1].index])
            top, bottom = max(p.price for p in highs), min(p.price for p in lows)
            if any(p.price < top - 0.75 * a for p in highs) or any(p.price > bottom + 0.75 * a for p in lows):
                break
            height = top - bottom
            if not (1.5 * a <= height <= 8 * a) or run[-1].index - run[0].index < 12:
                continue
            inside = ctx.c[run[0].index:run[-1].confirmed_index + 1]
            if inside.max() > top + 0.3 * a or inside.min() < bottom - 0.3 * a:
                break
            best = {"run": run, "top": top, "bottom": bottom, "height": height, "atr": a, "ready_index": run[-1].confirmed_index}
        if best:
            return best
    return None


def first_close_beyond(ctx, price: float, after: int, s: float, buffer_atr: float = 0.1) -> Optional[int]:
    for i in range(after + 1, ctx.last + 1):
        if s * (ctx.c[i] - price) > buffer_atr * ctx.atr[i]:
            return i
    return None


def levels(ctx, s: float) -> List[Dict[str, Any]]:
    """Resistance (s=+1) or support (s=-1) levels, newest first."""
    kind = "H" if s > 0 else "L"
    found: List[Dict[str, Any]] = []
    for p in reversed(ctx.confirmed_swings("major")):
        if p.kind == kind and p.index >= ctx.last - 150:
            found.append({"price": p.price, "index": p.index, "known_index": p.confirmed_index, "source": "major swing"})
    box = find_range(ctx)
    if box:
        found.append({"price": box["top"] if s > 0 else box["bottom"], "index": box["run"][0].index,
                      "known_index": box["ready_index"], "source": "range boundary"})
        found.sort(key=lambda lvl: -lvl["index"])
    unique: List[Dict[str, Any]] = []
    for lvl in found:
        if all(abs(lvl["price"] - kept["price"]) > 0.3 * ctx.atr[ctx.last] for kept in unique):
            unique.append(lvl)
    return unique


def _nearest_opposite(ctx, s: float, start: int, end: int) -> float:
    window_start = max(0, start)
    window = ctx.l[window_start:end + 1] if s > 0 else ctx.h[window_start:end + 1]
    return float(window.min() if s > 0 else window.max())


# --- range ------------------------------------------------------------------------

RANGE = _spec(
    id="range_rectangle", name="Range (rectangle) breakout / breakdown", family="range", direction="both", specificity=2,
    structure="At least two confirmed minor swing highs within 0.75 ATR of the range top and two swing lows within 0.75 ATR of the bottom; no close more than 0.3 ATR outside the box while it formed.",
    swing_rules="Confirmed minor swings only.", duration="At least 12 bars from first to last swing; box height 1.5-8 ATR.",
    tolerances="Boundary match 0.75 ATR; containment 0.3 ATR; breakout buffer 0.1 ATR.",
    volume=f"Breakout/breakdown bar volume at least {BREAKOUT_VOLUME}x the prior 20-bar mean.",
    confirmation="A close beyond the top (long) or bottom (short) plus 0.1 ATR on breakout volume.",
    invalidation="After a breakout: a close back beyond the box midpoint (or 1.5 ATR inside the broken boundary, whichever is nearer).",
    expiration="Awaiting a break for up to 60 bars after the box is recognized; actionable 3 bars after confirmation; target (box height projected) ends it.",
    regimes=("range", "any"),
    interpretation="Direction is undecided (both) until the box breaks; it then becomes a long or short setup.",
    ambiguity="Touch counts and tolerances vary between authors; this version needs two touches per side.",
    parameters={"boundary_tolerance_atr": 0.75, "breakout_volume": BREAKOUT_VOLUME},
)


@model.detector(RANGE)
def range_rectangle(ctx, spec):
    box = find_range(ctx)
    if not box:
        return []
    top, bottom, height, a = box["top"], box["bottom"], box["height"], box["atr"]
    mid = (top + bottom) / 2
    run = box["run"]
    points = [ctx.pivot_point("range high" if p.kind == "H" else "range low", p) for p in run]
    end = max(ctx.last, run[-1].index)
    lines = [ctx.segment("range top", run[0].index, top, end, top, kind="trigger"),
             ctx.segment("range bottom", run[0].index, bottom, end, bottom, kind="trigger")]
    measurements = {"range_height": height, "height_atr": height / a, "touches": len(run)}
    for i in range(box["ready_index"], ctx.last + 1):
        for s, direction, level in ((1.0, "long", top), (-1.0, "short", bottom)):
            if s * (ctx.c[i] - level) > 0.1 * ctx.atr[i]:
                ratio = ctx.volume_ratio(i)
                if ratio is not None and ratio < BREAKOUT_VOLUME:
                    continue
                invalidation = level - s * min(1.5 * a, abs(level - mid))
                target = level + s * height
                resolved = resolve(ctx, direction=direction, ready_index=i, trigger=level, invalidation=invalidation, target=target,
                                   awaiting_bars=0, signal_bars=3, volume_ratio=BREAKOUT_VOLUME, trigger_buffer_atr=0.1)
                return [build_detection(ctx, spec, direction=direction, resolved=resolved, anchor_index=run[0].index,
                                        start_index=run[0].index, end_index=run[-1].index, points=points, lines=lines,
                                        trigger=level, invalidation=invalidation, target=target,
                                        trigger_rule=f"close {'above' if s > 0 else 'below'} {level:.2f} + 0.1 ATR on {BREAKOUT_VOLUME}x volume",
                                        measurements=measurements, evidence=[f"{len(run)} swings contained in a {height / a:.1f} ATR box"])]
        if i - box["ready_index"] >= 60:
            return []
    resolved = {"state": model.AWAITING_CONFIRMATION, "reason": "inside the range - waiting for a breakout or breakdown",
                "confirmed_index": None, "ended_index": None, "expires_index": box["ready_index"] + 60, "notes": []}
    return [build_detection(ctx, spec, direction="both", resolved=resolved, anchor_index=run[0].index, start_index=run[0].index,
                            end_index=run[-1].index, points=points, lines=lines, trigger=top, invalidation=bottom, target=None,
                            trigger_rule=f"close above {top:.2f} (long) or below {bottom:.2f} (short) on {BREAKOUT_VOLUME}x volume",
                            measurements=measurements, evidence=[f"{len(run)} swings contained in a {height / a:.1f} ATR box"])]


# --- breakout / breakdown -----------------------------------------------------------


def _breakout_spec(up: bool) -> model.DetectorSpec:
    lvl = "resistance" if up else "support"
    return _spec(
        id="resistance_breakout" if up else "support_breakdown", name="Resistance breakout" if up else "Support breakdown",
        family="breakout", direction="long" if up else "short", specificity=1,
        structure=f"An untested {lvl} level: a confirmed major swing {'high' if up else 'low'} (or range boundary) from the last 150 bars that no close has crossed since it formed, at least 10 bars old.",
        swing_rules="Confirmed major swings.", duration="Level at least 10 bars old; approach watched within 1.5 ATR.",
        tolerances="Break buffer 0.1 ATR; approach zone 1.5 ATR.", volume=f"Break bar volume at least {BREAKOUT_VOLUME}x the prior 20-bar mean.",
        confirmation=f"A close {'above' if up else 'below'} the level + 0.1 ATR on breakout volume (within 5 bars of the first close through it).",
        invalidation=f"A close back {'below' if up else 'above'} the level by 1 ATR.",
        expiration="Actionable 3 bars after confirmation; target (the level's prior swing depth projected) ends it.",
        interpretation="Measured move = distance from the level to the extreme between the level and the break, projected from the level.",
        ambiguity="Which level 'matters' is subjective; this version uses only major swings and range boundaries, newest first.",
        parameters={"approach_atr": 1.5, "breakout_volume": BREAKOUT_VOLUME},
    )


def _breakout(ctx, spec, up: bool) -> List[Dict[str, Any]]:
    s = 1.0 if up else -1.0
    direction = "long" if up else "short"
    for lvl in levels(ctx, s):
        if ctx.last - lvl["index"] < 10:
            continue
        price = lvl["price"]
        broke = first_close_beyond(ctx, price, lvl["known_index"], s)
        point = [ctx.point(f"{'resistance' if up else 'support'} ({lvl['source']})", lvl["index"], price)]
        end = ctx.last
        line = [ctx.segment("level", lvl["index"], price, end, price, kind="trigger")]
        if broke is None:
            gap = s * (price - ctx.c[ctx.last])
            if 0 <= gap <= 1.5 * ctx.atr[ctx.last]:
                resolved = {"state": model.AWAITING_CONFIRMATION, "reason": f"price is {gap / ctx.atr[ctx.last]:.2f} ATR from an untested level",
                            "confirmed_index": None, "ended_index": None, "expires_index": None, "notes": []}
                depth = s * (price - _nearest_opposite(ctx, s, lvl["index"], ctx.last))
                return [build_detection(ctx, spec, direction=direction, resolved=resolved, anchor_index=lvl["index"],
                                        start_index=lvl["index"], end_index=ctx.last, points=point, lines=line, trigger=price,
                                        invalidation=price - s * 1.0 * ctx.atr[ctx.last], target=price + s * depth,
                                        trigger_rule=f"close {'above' if up else 'below'} {price:.2f} + 0.1 ATR on {BREAKOUT_VOLUME}x volume",
                                        measurements={"distance_atr": gap / ctx.atr[ctx.last]})]
            return []
        if ctx.last - broke > 25:
            continue
        depth = s * (price - _nearest_opposite(ctx, s, lvl["index"], broke))
        invalidation = price - s * 1.0 * ctx.atr[broke]
        target = price + s * depth
        resolved = resolve(ctx, direction=direction, ready_index=broke, trigger=price, invalidation=invalidation, target=target,
                           awaiting_bars=5, signal_bars=3, volume_ratio=BREAKOUT_VOLUME, trigger_buffer_atr=0.1)
        return [build_detection(ctx, spec, direction=direction, resolved=resolved, anchor_index=lvl["index"], start_index=lvl["index"],
                                end_index=broke, points=point, lines=line, trigger=price, invalidation=invalidation, target=target,
                                trigger_rule=f"close {'above' if up else 'below'} {price:.2f} + 0.1 ATR on {BREAKOUT_VOLUME}x volume",
                                measurements={"measured_depth": depth, "level_age_bars": broke - lvl["index"]})]
    return []


# --- retest ---------------------------------------------------------------------------


def _retest_spec(up: bool) -> model.DetectorSpec:
    return _spec(
        id="breakout_retest" if up else "breakdown_retest", name="Breakout and retest" if up else "Breakdown and retest",
        family="breakout", direction="long" if up else "short", specificity=3,
        structure=f"A volume-confirmed {'breakout above resistance' if up else 'breakdown below support'} (see that detector), then within 10 bars price returns to within 0.5 ATR of the broken level without closing more than 0.5 ATR back through it.",
        swing_rules="Level from confirmed major swings or a range boundary.", duration="Retest within 10 bars of the break.",
        tolerances="Retest zone 0.5 ATR; hold tolerance 0.5 ATR.", volume="Break bar as for the breakout detector; retest volume recorded as evidence.",
        confirmation=f"After the retest touch, a close {'above the prior bar high' if up else 'below the prior bar low'}.",
        invalidation=f"A close 0.5 ATR back {'below' if up else 'above'} the broken level.",
        expiration="Confirmation within 5 bars of the touch; actionable 3 bars after; the post-break extreme (or the measured move) as target.",
        interpretation="Also called throwback (after an upside breakout) or pullback (after a downside breakout) in Bulkowski's terms.",
        ambiguity="Retest depth and timing windows differ by author.",
        parameters={"retest_zone_atr": 0.5, "retest_window_bars": 10},
    )


def _retest(ctx, spec, up: bool) -> List[Dict[str, Any]]:
    s = 1.0 if up else -1.0
    direction = "long" if up else "short"
    for lvl in levels(ctx, s):
        price = lvl["price"]
        broke = first_close_beyond(ctx, price, lvl["known_index"], s)
        if broke is None or ctx.last - broke > 25:
            continue
        confirmed_break = None
        for i in range(broke, min(broke + 5, ctx.last) + 1):
            ratio = ctx.volume_ratio(i)
            if s * (ctx.c[i] - price) > 0.1 * ctx.atr[i] and (ratio is None or ratio >= BREAKOUT_VOLUME):
                confirmed_break = i
                break
        if confirmed_break is None:
            continue
        b = confirmed_break
        touch = None
        for r in range(b + 1, min(b + 10, ctx.last) + 1):
            if s * (price - ctx.c[r]) > 0.5 * ctx.atr[r]:
                touch = -1
                break
            extreme = ctx.l[r] if up else ctx.h[r]
            if s * (extreme - price) <= 0.5 * ctx.atr[r]:
                touch = r
                break
        if touch == -1:
            continue
        post_extreme = float(ctx.h[b:ctx.last + 1].max() if up else ctx.l[b:ctx.last + 1].min())
        invalidation = price - s * 0.5 * ctx.atr[b]
        depth = s * (price - _nearest_opposite(ctx, s, lvl["index"], b))
        target = post_extreme if s * (post_extreme - price) > 0.5 * ctx.atr[b] else price + s * depth
        points = [ctx.point("broken level", lvl["index"], price), ctx.point("break", b, ctx.c[b])]
        line = [ctx.segment("broken level", lvl["index"], price, ctx.last, price, kind="trigger")]
        if touch is None:
            pulled_back = s * (post_extreme - ctx.c[ctx.last]) >= 0.5 * ctx.atr[ctx.last]
            if ctx.last == b or not pulled_back or ctx.last - b > 10 or s * (ctx.c[ctx.last] - price) > 1.5 * ctx.atr[ctx.last]:
                continue
            resolved = developing("broke out and is pulling back toward the broken level", b + 10)
        else:
            points.append(ctx.point("retest", touch, ctx.l[touch] if up else ctx.h[touch]))
            resolved = resolve(ctx, direction=direction, ready_index=touch, trigger=lambda i: ctx.h[i - 1] if up else ctx.l[i - 1],
                               invalidation=invalidation, target=target, awaiting_bars=5, signal_bars=3)
        return [build_detection(ctx, spec, direction=direction, resolved=resolved, anchor_index=lvl["index"], start_index=lvl["index"],
                                end_index=touch if touch else b, points=points, lines=line,
                                trigger=(lambda i: ctx.h[max(i - 1, 0)]) if up else (lambda i: ctx.l[max(i - 1, 0)]),
                                invalidation=invalidation, target=target,
                                trigger_rule=f"after the retest, a close {'above the prior bar high' if up else 'below the prior bar low'}",
                                measurements={"break_volume_ratio": ctx.volume_ratio(b)},
                                evidence=[f"break confirmed on {ctx.volume_ratio(b) or 0:.2f}x volume"])]
    return []


# --- failed breakout / reclaim --------------------------------------------------------


def _failure_spec(bull_trap: bool) -> model.DetectorSpec:
    return _spec(
        id="failed_breakout" if bull_trap else "failed_breakdown_reclaim",
        name="Failed breakout" if bull_trap else "Failed breakdown (reclaim)",
        family="failure", direction="short" if bull_trap else "long", specificity=3,
        structure=f"A close {'above resistance' if bull_trap else 'below support'} (0.1 ATR beyond, any volume), followed within 5 bars by a close back {'below' if bull_trap else 'above'} the level by 0.1 ATR.",
        swing_rules="Level from confirmed major swings or a range boundary.", duration="Failure within 5 bars of the break; break within the last 15 bars.",
        tolerances="0.1 ATR either side of the level.", volume="Not required; high volume on the failed break is recorded as evidence of trapped traders.",
        confirmation=f"The close back {'below' if bull_trap else 'above'} the level.",
        invalidation=f"A close {'above the high' if bull_trap else 'below the low'} made during the failed break.",
        expiration="Actionable 3 bars after confirmation; the opposite side of the range (or the nearest swing) as target.",
        interpretation="'Bull trap' / 'bear trap'. Reported only once the failure has happened; an unresolved break near the level is ambiguous and shown as the breakout/retest setups instead.",
        ambiguity="Some traders require a close back inside a range specifically; any major-swing level is used here.",
        parameters={"failure_window_bars": 5},
    )


def _failure(ctx, spec, bull_trap: bool) -> List[Dict[str, Any]]:
    s = 1.0 if bull_trap else -1.0  # side of the level that was broken
    direction = "short" if bull_trap else "long"
    for lvl in levels(ctx, s):
        price = lvl["price"]
        broke = first_close_beyond(ctx, price, lvl["known_index"], s)
        if broke is None or ctx.last - broke > 15:
            continue
        fail = None
        for f in range(broke + 1, min(broke + 5, ctx.last) + 1):
            if s * (price - ctx.c[f]) > 0.1 * ctx.atr[f]:
                fail = f
                break
        if fail is None:
            continue
        trap = float(ctx.h[broke:fail + 1].max() if bull_trap else ctx.l[broke:fail + 1].min())
        opposite = _nearest_opposite(ctx, s, max(lvl["index"], broke - 30), broke)
        risk = abs(trap - ctx.c[fail])
        target = opposite if abs(price - opposite) > risk else ctx.c[fail] - s * 2 * risk
        resolved = resolve(ctx, direction=direction, ready_index=fail, trigger=price - s * 0.1 * ctx.atr[fail], invalidation=trap,
                           target=target, awaiting_bars=0, signal_bars=3)
        ratio = ctx.volume_ratio(broke)
        evidence = [f"broke {'above' if bull_trap else 'below'} {price:.2f} then closed back through it {fail - broke} bar(s) later"]
        if ratio and ratio >= BREAKOUT_VOLUME:
            evidence.append(f"the failed break traded {ratio:.2f}x average volume (trapped participants)")
        return [build_detection(ctx, spec, direction=direction, resolved=resolved, anchor_index=lvl["index"], start_index=lvl["index"],
                                end_index=fail, points=[ctx.point("level", lvl["index"], price), ctx.point("failed break", broke, ctx.c[broke]),
                                                         ctx.point("trap extreme", broke, trap), ctx.point("back through", fail, ctx.c[fail])],
                                lines=[ctx.segment("level", lvl["index"], price, ctx.last, price, kind="trigger")],
                                trigger=price - s * 0.1 * ctx.atr[fail], invalidation=trap, target=target,
                                trigger_rule=f"close back {'below' if bull_trap else 'above'} {price:.2f}",
                                measurements={"bars_outside": fail - broke}, evidence=evidence)]
    return []


# --- rejection at a level ---------------------------------------------------------------


def _rejection_spec(at_resistance: bool) -> model.DetectorSpec:
    return _spec(
        id="resistance_rejection" if at_resistance else "support_rejection",
        name="Rejection at resistance" if at_resistance else "Rejection at support (bounce)",
        family="failure", direction="short" if at_resistance else "long", specificity=2,
        structure=f"A bar in the last 3 whose {'high' if at_resistance else 'low'} reaches within 0.25 ATR of an untested {'resistance' if at_resistance else 'support'} level (at least 10 bars old), closes on the {'lower' if at_resistance else 'upper'} 40% of its range and back {'below' if at_resistance else 'above'} the level, with a {'upper' if at_resistance else 'lower'} wick of at least half the bar's range.",
        swing_rules="Level from confirmed major swings or a range boundary.", duration="Rejection bar within the last 3 bars.",
        tolerances="Test zone 0.25 ATR.", volume="Not required; recorded.",
        confirmation="The rejection bar itself (its close).",
        invalidation=f"A close {'above' if at_resistance else 'below'} the rejection bar's {'high' if at_resistance else 'low'}.",
        expiration="Actionable 3 bars after the rejection bar; nearest opposite swing (or 2R) as target.",
        interpretation="A single-bar rejection candle at an objective level (pin bar / shooting star / hammer shape), not a candlestick pattern on its own.",
        ambiguity="Wick and close thresholds differ between sources.",
        parameters={"test_zone_atr": 0.25, "min_wick_fraction": 0.5, "close_fraction": 0.4},
    )


def _rejection(ctx, spec, at_resistance: bool) -> List[Dict[str, Any]]:
    s = 1.0 if at_resistance else -1.0
    direction = "short" if at_resistance else "long"
    for lvl in levels(ctx, s):
        price = lvl["price"]
        if ctx.last - lvl["index"] < 10 or first_close_beyond(ctx, price, lvl["known_index"], s) is not None:
            continue
        for k in range(ctx.last, max(ctx.last - 3, lvl["known_index"]), -1):
            high, low, open_, close = ctx.h[k], ctx.l[k], ctx.o[k], ctx.c[k]
            span = high - low
            if span <= 0:
                continue
            extreme = high if at_resistance else low
            if s * (price - extreme) > 0.25 * ctx.atr[k] or s * (close - price) >= 0:
                continue
            close_position = (close - low) / span if at_resistance else (high - close) / span
            wick = (high - max(open_, close)) / span if at_resistance else (min(open_, close) - low) / span
            if close_position > 0.4 or wick < 0.5:
                continue
            opposite = _nearest_opposite(ctx, s, k - 30, k)
            risk = abs(extreme - close)
            target = opposite if abs(close - opposite) > risk else close - s * 2 * risk
            resolved = resolve(ctx, direction=direction, ready_index=k, trigger=price, invalidation=extreme, target=target,
                               awaiting_bars=0, signal_bars=3)
            return [build_detection(ctx, spec, direction=direction, resolved=resolved, anchor_index=lvl["index"], start_index=lvl["index"],
                                    end_index=k, points=[ctx.point("level", lvl["index"], price), ctx.point("rejection bar", k, extreme)],
                                    lines=[ctx.segment("level", lvl["index"], price, ctx.last, price, kind="trigger")],
                                    trigger=price, invalidation=extreme, target=target,
                                    trigger_rule=f"rejection bar closes back {'below' if at_resistance else 'above'} {price:.2f}",
                                    measurements={"wick_fraction": wick, "close_position": close_position},
                                    evidence=[f"wick {wick:.0%} of the bar's range at the level"])]
    return []


for _up in (True, False):
    model.detector(_breakout_spec(_up))(lambda ctx, spec, _u=_up: _breakout(ctx, spec, _u))
    model.detector(_retest_spec(_up))(lambda ctx, spec, _u=_up: _retest(ctx, spec, _u))
    model.detector(_failure_spec(_up))(lambda ctx, spec, _u=_up: _failure(ctx, spec, _u))
    model.detector(_rejection_spec(_up))(lambda ctx, spec, _u=_up: _rejection(ctx, spec, _u))
