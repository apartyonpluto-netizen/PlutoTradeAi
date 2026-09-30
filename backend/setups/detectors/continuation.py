"""Trend pullbacks and flag/pennant continuations."""

from __future__ import annotations

from typing import Any, Dict, List

import numpy as np

from .. import model
from ..engine import build_detection, developing, resolve
from ._common import DAILY_AND_INTRADAY, fit_line


def _pullback_spec(up: bool) -> model.DetectorSpec:
    return model.DetectorSpec(
        id="trend_pullback_long" if up else "trend_pullback_short", version="1.0.0",
        name="Trend pullback (long)" if up else "Trend pullback (short)", family="continuation",
        direction="long" if up else "short", timeframes=DAILY_AND_INTRADAY, min_bars=60, specificity=2,
        structure=(f"In an {'up' if up else 'down'}trend, price pulls back to the 20-period EMA (within 0.25 ATR) from a swing "
                   f"{'high' if up else 'low'} 1-5 ATR away, while closing {'above' if up else 'below'} the 50-period SMA."),
        prior_trend=(f"Close {'above' if up else 'below'} the 50 SMA, 50 SMA {'rising' if up else 'falling'} over 10 bars, and close "
                     f"{'above' if up else 'below'} the 200 SMA when 200 bars exist."),
        swing_rules=f"The pullback's origin is the {'highest high' if up else 'lowest low'} of the 15 bars before the touch.",
        duration="Touch within the last 8 bars; resumption within 5 bars of the touch.",
        tolerances="EMA touch 0.25 ATR; pullback depth 1-5 ATR; invalidation buffer 0.25 ATR.",
        volume="Not required. Pullback bars trading below average volume are recorded as supporting evidence.",
        confirmation=f"After the touch, a close {'above the prior bar high' if up else 'below the prior bar low'} (resumption).",
        invalidation=f"A close {'below' if up else 'above'} the pullback {'low' if up else 'high'} by 0.25 ATR, or {'below' if up else 'above'} the 50 SMA.",
        expiration="No resumption within 5 bars of the touch; actionable 3 bars after confirmation; the pullback's origin as target.",
        regimes=("uptrend",) if up else ("downtrend",),
        data_requirements="At least 60 bars (200 for the long-term filter).",
        interpretation="Buy-the-dip (sell-the-rip) in an established trend with moving averages defining trend and value area.",
        ambiguity="Which average defines 'value' varies (10/20 EMA, 20/50 SMA); the 20 EMA is used here.",
        sources=("StockCharts ChartSchool - Moving Averages; Trend pullbacks", "A. Elder, Trading for a Living (value zone between EMAs)"),
        parameters={"touch_atr": 0.25, "min_depth_atr": 1.0, "max_depth_atr": 5.0},
    )


def _pullback(ctx, spec, up: bool) -> List[Dict[str, Any]]:
    s = 1.0 if up else -1.0
    direction = "long" if up else "short"
    ema20, sma50, sma200 = ctx.ema(20), ctx.sma(50), ctx.sma(200)
    last = ctx.last
    for p in range(last, max(last - 8, 55), -1):
        if not np.isfinite(sma50[p]) or not np.isfinite(sma50[p - 10]):
            continue
        trend_ok = s * (ctx.c[p] - sma50[p]) > 0 and s * (sma50[p] - sma50[p - 10]) > 0
        if np.isfinite(sma200[p]):
            trend_ok = trend_ok and s * (ctx.c[p] - sma200[p]) > 0
        extreme = ctx.l[p] if up else ctx.h[p]
        touched = s * (extreme - ema20[p]) <= 0.25 * ctx.atr[p]
        if not (trend_ok and touched):
            continue
        origin_window = ctx.h[p - 15:p + 1] if up else ctx.l[p - 15:p + 1]
        origin_offset = int(np.argmax(origin_window) if up else np.argmin(origin_window))
        origin_index = p - 15 + origin_offset
        origin = float(origin_window[origin_offset])
        depth = s * (origin - extreme)
        if not (1.0 * ctx.atr[p] <= depth <= 5.0 * ctx.atr[p]) or origin_index == p:
            continue
        if any(s * (ctx.c[i] - ema20[i]) < 0 for i in range(max(origin_index - 5, 0), origin_index)):
            continue  # trend was not riding above the EMA before the pullback
        pullback_extreme = lambda i: float((ctx.l if up else ctx.h)[p:i + 1].min() if up else (ctx.h[p:i + 1].max()))
        invalidation = lambda i: (pullback_extreme(i) - s * 0.25 * ctx.atr[p]) if s * (pullback_extreme(i) - s * 0.25 * ctx.atr[p] - sma50[i]) > 0 else float(sma50[i])
        resolved = resolve(ctx, direction=direction, ready_index=p, trigger=lambda i: ctx.h[i - 1] if up else ctx.l[i - 1],
                           invalidation=invalidation, target=origin, awaiting_bars=5, signal_bars=3)
        good, bad = [], []
        pull_volume, base_volume = ctx.mean_volume(origin_index + 1, p), ctx.avg_volume()[p]
        if pull_volume and np.isfinite(base_volume) and base_volume > 0:
            ratio = pull_volume / base_volume
            (good if ratio < 1 else bad).append(f"pullback volume {ratio:.2f}x the 20-bar average")
        return [build_detection(ctx, spec, direction=direction, resolved=resolved, anchor_index=origin_index, start_index=origin_index,
                                end_index=p, points=[ctx.point("pullback origin", origin_index, origin), ctx.point("EMA touch", p, extreme)],
                                lines=[ctx.segment("20 EMA", origin_index, ema20[origin_index], last, ema20[last], kind="reference")],
                                trigger=lambda i: ctx.h[max(i - 1, 0)] if up else ctx.l[max(i - 1, 0)],
                                invalidation=invalidation, target=origin,
                                trigger_rule=f"a close {'above the prior bar high' if up else 'below the prior bar low'} after the touch",
                                measurements={"pullback_depth_atr": depth / ctx.atr[p]}, evidence=["trend filters hold at the touch"] + good,
                                counter=bad)]
    return []


def _flag_spec(up: bool, pennant: bool) -> model.DetectorSpec:
    shape = "pennant" if pennant else "flag"
    return model.DetectorSpec(
        id=f"{'bull' if up else 'bear'}_{shape}", version="1.0.0", name=f"{'Bull' if up else 'Bear'} {shape}", family="continuation",
        direction="long" if up else "short", timeframes=DAILY_AND_INTRADAY, min_bars=40, specificity=3,
        structure=(f"A pole: a move of at least 3 ATR in 2-15 bars to the {'highest high' if up else 'lowest low'} of the last 25 bars. "
                   f"Then 3-20 bars of consolidation retracing at most 50% of the pole, bounded by lines fitted to the consolidation "
                   + ("that converge (upper falling, lower rising)." if pennant else
                      f"that are roughly parallel and flat or drifting {'down' if up else 'up'} (against the pole).")),
        prior_trend="The pole itself.", swing_rules="Pole from bar extremes, not pivots (flags are too shallow to confirm a swing).",
        duration="Pole 2-15 bars; consolidation 3-20 bars.",
        tolerances="Slopes compared in ATR per bar: counter-drift at most +0.05 ATR/bar; parallel within 0.1 ATR/bar.",
        volume="Breakout bar volume at least 1.2x the prior 20-bar mean; lighter volume in the consolidation than in the pole is recorded as evidence.",
        confirmation=f"A close {'above the upper' if up else 'below the lower'} boundary on breakout volume.",
        invalidation=f"A close {'below' if up else 'above'} the consolidation's {'low' if up else 'high'}, or a retracement beyond 50% of the pole.",
        expiration="Consolidation longer than 20 bars; actionable 3 bars after confirmation; pole height projected from the breakout as target.",
        regimes=("any",), data_requirements="At least 40 bars.",
        interpretation="Target is the classic measured move (pole height added to the breakout).",
        ambiguity="Flag vs pennant vs small triangle overlap; each is reported by its own rule and grouped as one opportunity.",
        sources=("StockCharts ChartSchool - Flag, Pennant", "T. Bulkowski, Encyclopedia of Chart Patterns - Flags, Pennants"),
        parameters={"pole_atr": 3.0, "max_retrace": 0.5, "breakout_volume": 1.2},
    )


def _flag(ctx, spec, up: bool, pennant: bool) -> List[Dict[str, Any]]:
    """Tries candidate pole tops newest first: bars in the last 25 that are the
    extreme of the 15 bars before and 3 bars after them."""
    last = ctx.last
    extremes = ctx.h if up else ctx.l
    for top in range(last - 1, max(last - 25, 16), -1):
        window = extremes[top - 15:min(last, top + 3) + 1]
        if extremes[top] != (window.max() if up else window.min()):
            continue
        found = _flag_from(ctx, spec, up, pennant, top)
        if found is not None:
            return found
    return []


def _flag_from(ctx, spec, up: bool, pennant: bool, top: int):
    s = 1.0 if up else -1.0
    direction = "long" if up else "short"
    last = ctx.last
    base_window = ctx.l[max(top - 15, 0):top] if up else ctx.h[max(top - 15, 0):top]
    if not len(base_window):
        return None
    base = max(top - 15, 0) + int(np.argmin(base_window) if up else np.argmax(base_window))
    pole_top, pole_base = (ctx.h[top], ctx.l[base]) if up else (ctx.l[top], ctx.h[base])
    pole = s * (pole_top - pole_base)
    a = float(ctx.atr[top])
    if pole < 3.0 * a or not (2 <= top - base <= 15):
        return None
    points = [ctx.point("pole start", base, pole_base), ctx.point("pole end", top, pole_top)]

    def bounds(end: int):
        idx = list(range(top, end + 1))
        upper_slope, upper_icpt = fit_line([(i, ctx.h[i]) for i in idx])
        lower_slope, lower_icpt = fit_line([(i, ctx.l[i]) for i in idx[1:]] or [(top, ctx.l[top])])
        upper_icpt += max(ctx.h[i] - (upper_slope * i + upper_icpt) for i in idx)
        lower_icpt += min(ctx.l[i] - (lower_slope * i + lower_icpt) for i in idx)
        return upper_slope, upper_icpt, lower_slope, lower_icpt

    def shape_ok(end: int):
        us, ui, ls, li = bounds(end)
        us_a, ls_a = us / a, ls / a
        if pennant:
            ok = us_a < 0 and ls_a > 0 and us_a - ls_a <= -0.05
        else:
            drift = (us_a, ls_a) if up else (-us_a, -ls_a)
            ok = max(drift) <= 0.05 and abs(us_a - ls_a) <= 0.1
        return ok, (us, ui, ls, li)

    breakout = None
    for k in range(top + 3, last + 1):
        ok, (us, ui, ls, li) = shape_ok(k - 1)
        retrace = s * (pole_top - (ctx.l[top:k].min() if up else ctx.h[top:k].max()))
        if retrace > 0.5 * pole or not ok:
            return None
        if k - top > 20:
            return None
        edge = (us * k + ui) if up else (ls * k + li)
        if s * (ctx.c[k] - edge) > 0:
            ratio = ctx.volume_ratio(k)
            if ratio is None or ratio >= 1.2:
                breakout = (k, (us, ui, ls, li))
                break
    bars_in = last - top
    if breakout is None:
        drawdown = s * (pole_top - (ctx.l[top:last + 1].min() if up else ctx.h[top:last + 1].max()))
        if drawdown > 0.5 * pole:
            return None
        if bars_in < 3:
            return [build_detection(ctx, spec, direction=direction, resolved=developing("pole complete; consolidation just started"),
                                    anchor_index=base, start_index=base, end_index=last, points=points, trigger=pole_top,
                                    invalidation=pole_top - s * 0.5 * pole, target=pole_top + s * pole,
                                    trigger_rule="close beyond the consolidation boundary on 1.2x volume",
                                    measurements={"pole_atr": pole / a})]
        ok, (us, ui, ls, li) = shape_ok(last)
        if not ok or bars_in > 20:
            return None
        end = last
    else:
        end, (us, ui, ls, li) = breakout[0], breakout[1]
    trigger = (lambda i: us * i + ui) if up else (lambda i: ls * i + li)
    consolidation_extreme = float(ctx.l[top + 1:end + 1].min() if up else ctx.h[top + 1:end + 1].max()) if end > top else pole_top
    invalidation = max(consolidation_extreme, pole_top - 0.5 * pole) if up else min(consolidation_extreme, pole_top + 0.5 * pole)
    target = lambda i: trigger(i) + s * pole
    resolved = resolve(ctx, direction=direction, ready_index=top + 3, trigger=trigger, invalidation=invalidation, target=target,
                       awaiting_bars=17, signal_bars=3, volume_ratio=1.2)
    line_end = max(end, last)
    good, bad = [], []
    pole_volume, flag_volume = ctx.mean_volume(base, top), ctx.mean_volume(top + 1, end)
    if pole_volume and flag_volume:
        ratio = flag_volume / pole_volume
        (good if ratio < 1 else bad).append(f"consolidation volume {ratio:.2f}x pole volume")
    return [build_detection(ctx, spec, direction=direction, resolved=resolved, anchor_index=base, start_index=base, end_index=end,
                            points=points,
                            lines=[ctx.segment("upper boundary", top, us * top + ui, line_end, us * line_end + ui, kind="trigger" if up else "boundary"),
                                   ctx.segment("lower boundary", top, ls * top + li, line_end, ls * line_end + li, kind="boundary" if up else "trigger")],
                            trigger=trigger, invalidation=invalidation, target=target,
                            trigger_rule=f"close {'above the upper' if up else 'below the lower'} boundary on 1.2x volume",
                            measurements={"pole_atr": pole / a, "consolidation_bars": end - top,
                                          "upper_slope_atr_per_bar": us / a, "lower_slope_atr_per_bar": ls / a},
                            evidence=[f"pole of {pole / a:.1f} ATR in {top - base} bars"] + good, counter=bad)]


for _up in (True, False):
    model.detector(_pullback_spec(_up))(lambda ctx, spec, _u=_up: _pullback(ctx, spec, _u))
    for _pennant in (False, True):
        model.detector(_flag_spec(_up, _pennant))(lambda ctx, spec, _u=_up, _p=_pennant: _flag(ctx, spec, _u, _p))
