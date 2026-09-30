"""Reversal formations on MAJOR swings: head and shoulders (and inverse),
double and triple tops/bottoms. Tops and bottoms share one implementation;
`s` is +1 for tops (peaks are highs) and -1 for bottoms (peaks are lows)."""

from __future__ import annotations

from typing import Any, Dict, List

from .. import model
from ..engine import build_detection, developing, resolve
from ._common import DAILY_AND_HOURLY, highest_before, line_through, lowest_before, volume_note, windows

_SOURCES = (
    "StockCharts ChartSchool - chart patterns (Head and Shoulders Top/Bottom, Double Top/Bottom, Triple Top/Bottom)",
    "T. Bulkowski, Encyclopedia of Chart Patterns (2nd ed.), and thepatternsite.com",
    "Edwards & Magee, Technical Analysis of Stock Trends",
)


def _hs_spec(top: bool) -> model.DetectorSpec:
    word = "top" if top else "bottom (inverse)"
    return model.DetectorSpec(
        id="head_and_shoulders_top" if top else "inverse_head_and_shoulders",
        version="1.0.0",
        name="Head and shoulders top" if top else "Inverse head and shoulders",
        family="reversal",
        direction="short" if top else "long",
        timeframes=DAILY_AND_HOURLY,
        min_bars=80,
        specificity=4,
        structure=("Five alternating major swings: left shoulder, trough, head, trough, right shoulder. The head extends at least "
                   "0.5 ATR beyond both shoulders; the shoulders are within 1.2 ATR of each other; the neckline is the line "
                   "through the two troughs and both shoulders sit beyond it." if top else
                   "Mirror of the top: three major swing lows with the middle (head) the lowest, the two peaks between them "
                   "forming the neckline."),
        prior_trend=f"The left shoulder is at least 2 ATR {'above the lowest low' if top else 'below the highest high'} of the 60 bars before it.",
        swing_rules="Major ATR zigzag swings (2.5 ATR daily/hourly). The right shoulder may be provisional only in the DEVELOPING state.",
        duration="Left to right shoulder 10-150 bars; head-to-shoulder spacing ratio between 1:3 and 3:1.",
        tolerances="Shoulder match 1.2 ATR; head excess 0.5 ATR; neckline rise/fall between troughs at most 1.5 ATR.",
        volume="Not required. Lower volume on the right shoulder than around the head is recorded as supporting evidence.",
        confirmation=f"A close {'below' if top else 'above'} the neckline after the right shoulder is confirmed.",
        invalidation=f"A close {'above' if top else 'below'} the right shoulder, before or after confirmation.",
        expiration="No neckline break within max(10, head-to-right-shoulder bars) of the right shoulder; the signal stays actionable 5 bars after confirmation; reaching the target ends it.",
        regimes=("any",),
        data_requirements="At least 80 bars of OHLCV on the timeframe.",
        interpretation=(f"Classic {word}: the target is the neckline minus (plus) the head's distance from the neckline, "
                        "measured at the head. Tolerances are ATR multiples rather than fixed percentages so the same rule works "
                        "across volatility levels."),
        ambiguity="Authors differ on shoulder symmetry, neckline slope limits and whether volume is required; this version records volume as evidence only.",
        sources=_SOURCES,
        parameters={"shoulder_tolerance_atr": 1.2, "head_excess_atr": 0.5, "neckline_slope_atr": 1.5, "prior_trend_atr": 2.0},
    )


def _head_and_shoulders(ctx, spec: model.DetectorSpec, top: bool) -> List[Dict[str, Any]]:
    s = 1.0 if top else -1.0
    direction = "short" if top else "long"
    labels = ("left shoulder", "trough 1" if top else "peak 1", "head", "trough 2" if top else "peak 2", "right shoulder")
    for run in windows(ctx.swings("major"), "HLHLH" if top else "LHLHL"):
        ls, t1, head, t2, rs = run
        if any(p.provisional for p in (ls, t1, head, t2)):
            continue
        a = float(ctx.atr[head.index])
        excess = s * head.price - max(s * ls.price, s * rs.price)
        if excess < 0.5 * a or abs(ls.price - rs.price) > 1.2 * a or abs(t2.price - t1.price) > 1.5 * a:
            continue
        d1, d2 = head.index - ls.index, rs.index - head.index
        if d2 <= 0 or not (1 / 3 <= d1 / d2 <= 3) or not (10 <= rs.index - ls.index <= 150):
            continue
        neck = line_through(t1.index, t1.price, t2.index, t2.price)
        if s * (ls.price - neck(ls.index)) <= 0 or s * (rs.price - neck(rs.index)) <= 0:
            continue
        prior = lowest_before(ctx, ls.index, 60) if top else highest_before(ctx, ls.index, 60)
        if s * (ls.price - prior) < 2.0 * ctx.atr[ls.index]:
            continue
        height = s * (head.price - neck(head.index))
        target = lambda i, neck=neck, height=height: neck(i) - s * height
        if rs.provisional:
            if s * (ctx.c[ctx.last] - neck(ctx.last)) <= 0:
                continue
            resolved = developing("right shoulder is forming - its swing point is still provisional")
        else:
            resolved = resolve(ctx, direction=direction, ready_index=rs.confirmed_index, trigger=neck, invalidation=rs.price,
                               target=target, awaiting_bars=max(10, d2), signal_bars=5)
        evidence = [f"head extends {excess / a:.2f} ATR beyond the shoulders",
                    f"shoulders differ by {abs(ls.price - rs.price) / a:.2f} ATR"]
        good, bad = volume_note(ctx, "head", (head.index - 3, head.index + 3), "right shoulder", (rs.index - 3, rs.index + 3), want_lower_b=True)
        end = max(ctx.last, rs.index)
        return [build_detection(
            ctx, spec, direction=direction, resolved=resolved, anchor_index=ls.index, start_index=ls.index, end_index=rs.index,
            points=[ctx.pivot_point(label, p) for label, p in zip(labels, run)],
            lines=[ctx.segment("neckline", t1.index, t1.price, end, neck(end), kind="trigger")],
            trigger=neck, invalidation=rs.price, target=target,
            trigger_rule=f"close {'below' if top else 'above'} the neckline",
            measurements={"head_excess_atr": excess / a, "shoulder_gap_atr": abs(ls.price - rs.price) / a,
                          "pattern_height": height, "left_bars": d1, "right_bars": d2},
            evidence=evidence + good, counter=bad,
        )]
    return []


def _multi_peak_spec(top: bool, count: int) -> model.DetectorSpec:
    peak, trough = ("peaks", "trough") if top else ("troughs", "peak")
    name = ("Double" if count == 2 else "Triple") + (" top" if top else " bottom")
    return model.DetectorSpec(
        id=name.lower().replace(" ", "_"),
        version="1.0.0",
        name=name,
        family="reversal",
        direction="short" if top else "long",
        timeframes=DAILY_AND_HOURLY,
        min_bars=60,
        specificity=2 if count == 2 else 3,
        structure=(f"{count} consecutive major {peak} at the same level (spread at most {0.6 if count == 2 else 1.0} ATR) separated by "
                   f"{'a' if count == 2 else 'two'} {trough}{'' if count == 2 else 's'} at least {1.5 if count == 2 else 1.0} ATR away."),
        prior_trend=f"The first {peak[:-1]} is at least 2.5 ATR {'above the lowest low' if top else 'below the highest high'} of the 60 bars before it.",
        swing_rules=f"Major ATR zigzag swings. The last {peak[:-1]} may be provisional only in the DEVELOPING state.",
        duration=f"First to last {peak[:-1]}: {'10-120' if count == 2 else '15-200'} bars.",
        tolerances=f"Level match {0.6 if count == 2 else 1.0} ATR; {trough} depth {1.5 if count == 2 else 1.0} ATR.",
        volume="Not required; recorded as evidence when the last swing trades on lower volume than the first.",
        confirmation=f"A close {'below' if top else 'above'} the {trough} {'level' if count == 2 else 'furthest from the peaks'}.",
        invalidation=f"A close {'above the highest' if top else 'below the lowest'} {peak[:-1]}.",
        expiration="No confirmation within 20 bars of the last swing; actionable for 5 bars after confirmation; reaching the target ends it.",
        regimes=("any",),
        data_requirements="At least 60 bars of OHLCV.",
        interpretation=f"Target = confirmation level {'minus' if top else 'plus'} the pattern height (mean {peak} to {trough} level).",
        ambiguity="Sources use percentage tolerances (e.g. a few percent); ATR multiples are used here. A triple formation also contains a double - they are grouped as one opportunity.",
        sources=_SOURCES,
        parameters={"level_tolerance_atr": 0.6 if count == 2 else 1.0, "depth_atr": 1.5 if count == 2 else 1.0, "prior_trend_atr": 2.5},
    )


def _multi_peak(ctx, spec: model.DetectorSpec, top: bool, count: int) -> List[Dict[str, Any]]:
    s = 1.0 if top else -1.0
    direction = "short" if top else "long"
    kinds = ("HLH" if count == 2 else "HLHLH") if top else ("LHL" if count == 2 else "LHLHL")
    tolerance, depth_needed = (0.6, 1.5) if count == 2 else (1.0, 1.0)
    span_min, span_max = (10, 120) if count == 2 else (15, 200)
    for run in windows(ctx.swings("major"), kinds):
        peaks, troughs = run[0::2], run[1::2]
        if any(p.provisional for p in run[:-1]):
            continue
        last_peak = peaks[-1]
        a = float(ctx.atr[last_peak.index])
        prices = [p.price for p in peaks]
        if max(prices) - min(prices) > tolerance * a:
            continue
        if not (span_min <= last_peak.index - peaks[0].index <= span_max):
            continue
        neck = min(troughs, key=lambda p: s * p.price)          # trough furthest from the peaks
        nearest = max(troughs, key=lambda p: s * p.price)
        if min(s * price for price in prices) - s * nearest.price < depth_needed * a:
            continue
        prior = lowest_before(ctx, peaks[0].index, 60) if top else highest_before(ctx, peaks[0].index, 60)
        if s * (peaks[0].price - prior) < 2.5 * ctx.atr[peaks[0].index]:
            continue
        extreme = max(prices, key=lambda price: s * price)
        height = s * (sum(prices) / len(prices) - neck.price)
        target = neck.price - s * height
        if last_peak.provisional:
            confirmed_extreme = max((p.price for p in peaks[:-1]), key=lambda price: s * price)
            if s * (ctx.c[ctx.last] - neck.price) <= 0 or s * (ctx.c[ctx.last] - confirmed_extreme) >= 0:
                continue  # already through the confirmation level, or closing beyond the earlier peaks (a breakout, not a top)
            resolved = developing(f"the {'second' if count == 2 else 'third'} {'peak' if top else 'trough'} is forming - its swing point is still provisional")
        else:
            resolved = resolve(ctx, direction=direction, ready_index=last_peak.confirmed_index, trigger=neck.price,
                               invalidation=extreme, target=target, awaiting_bars=20, signal_bars=5)
        good, bad = volume_note(ctx, "first swing", (peaks[0].index - 2, peaks[0].index + 2), "last swing",
                                (last_peak.index - 2, last_peak.index + 2), want_lower_b=True)
        labels = [f"{'peak' if top else 'trough'} {k // 2 + 1}" if k % 2 == 0 else f"{'trough' if top else 'peak'} {k // 2 + 1}" for k in range(len(run))]
        end = max(ctx.last, last_peak.index)
        return [build_detection(
            ctx, spec, direction=direction, resolved=resolved, anchor_index=peaks[0].index, start_index=peaks[0].index,
            end_index=last_peak.index, points=[ctx.pivot_point(label, p) for label, p in zip(labels, run)],
            lines=[ctx.segment("confirmation level", neck.index, neck.price, end, neck.price, kind="trigger"),
                   ctx.segment("resistance" if top else "support", peaks[0].index, peaks[0].price, last_peak.index, last_peak.price)],
            trigger=neck.price, invalidation=extreme, target=target,
            trigger_rule=f"close {'below' if top else 'above'} {neck.price:.2f}",
            measurements={"level_spread_atr": (max(prices) - min(prices)) / a, "pattern_height": height,
                          "span_bars": last_peak.index - peaks[0].index},
            evidence=[f"{count} {'peaks' if top else 'troughs'} within {(max(prices) - min(prices)) / a:.2f} ATR"] + good,
            counter=bad,
        )]
    return []


for _top in (True, False):
    model.detector(_hs_spec(_top))(lambda ctx, spec, _t=_top: _head_and_shoulders(ctx, spec, _t))
    for _count in (2, 3):
        model.detector(_multi_peak_spec(_top, _count))(lambda ctx, spec, _t=_top, _c=_count: _multi_peak(ctx, spec, _t, _c))
