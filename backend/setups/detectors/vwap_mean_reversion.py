"""Intraday VWAP reclaim/loss/rejection, and objectively defined mean reversion."""

from __future__ import annotations

from typing import Any, Dict, List

import numpy as np

from .. import model
from ..bars import regular_session_mask, session_keys
from ..engine import build_detection, resolve
from ._common import INTRADAY_ONLY

_VWAP_SOURCES = ("Session VWAP (cumulative typical price x volume / volume, reset each regular session)",
                 "B. Shannon, Maximum Trading Gains with Anchored VWAP (VWAP as intraday reference level)")
RUN_BARS = 6


def _vwap_spec(kind: str) -> model.DetectorSpec:
    rules = {
        "vwap_reclaim": ("VWAP reclaim", "long", f"At least {RUN_BARS} consecutive closes below session VWAP, then a close above VWAP + 0.1 ATR on 1.2x the session's average volume so far.",
                         "the reclaim close", "a close back below VWAP - 0.5 ATR, or below the reclaim bar's low"),
        "vwap_loss": ("VWAP loss", "short", f"At least {RUN_BARS} consecutive closes above session VWAP, then a close below VWAP - 0.1 ATR on 1.2x the session's average volume so far.",
                      "the loss close", "a close back above VWAP + 0.5 ATR, or above the loss bar's high"),
        "vwap_rejection": ("VWAP rejection (from below)", "short", f"At least {RUN_BARS} consecutive closes below VWAP, then a bar whose high reaches within 0.1 ATR of VWAP and closes at least 0.1 ATR below it in the lower half of its range.",
                           "the rejection bar's close", "a close above the rejection bar's high"),
        "vwap_support_hold": ("VWAP hold (from above)", "long", f"At least {RUN_BARS} consecutive closes above VWAP, then a bar whose low reaches within 0.1 ATR of VWAP and closes at least 0.1 ATR above it in the upper half of its range.",
                              "the hold bar's close", "a close below the hold bar's low"),
    }
    name, direction, structure, confirmation, invalidation = rules[kind]
    return model.DetectorSpec(
        id=kind, version="1.0.0", name=name, family="vwap", direction=direction, timeframes=INTRADAY_ONLY, min_bars=20, specificity=2,
        structure=structure + " Regular-session bars only (09:30-16:00 ET); the setup must form within today's session.",
        prior_trend=f"The {RUN_BARS}-bar run on one side of VWAP.", swing_rules="None - VWAP is the reference.",
        duration=f"Run of {RUN_BARS}+ bars; signal within the same session.", tolerances="0.1 ATR buffers; 0.5 ATR invalidation buffer.",
        volume="Reclaim/loss bar at least 1.2x the session's mean bar volume so far." if kind in ("vwap_reclaim", "vwap_loss") else "Not required.",
        confirmation=confirmation.capitalize() + ".", invalidation=invalidation.capitalize() + ".",
        expiration="Actionable 3 bars after the signal bar and never past the session close.",
        regimes=("any",), data_requirements="Intraday bars with volume; needs today's regular session.",
        interpretation="VWAP as the session's volume-weighted fair price; a reclaim/loss flips control, a rejection/hold confirms it.",
        ambiguity="Run length and buffers are this library's choice; anchored VWAPs (from events) are not covered.",
        sources=_VWAP_SOURCES, parameters={"run_bars": RUN_BARS, "volume_ratio": 1.2},
    )


def _vwap(ctx, spec, kind: str) -> List[Dict[str, Any]]:
    if not ctx.bars.intraday:
        return []
    vwap = ctx.vwap()
    keys = session_keys(ctx.bars)
    mask = regular_session_mask(ctx.bars)
    last = ctx.last
    if not mask[last]:
        return []
    session = [i for i in range(last + 1) if keys[i] == keys[last] and mask[i]]
    if len(session) < RUN_BARS + 1:
        return []
    below_side = kind in ("vwap_reclaim", "vwap_rejection")
    for k in reversed(session[RUN_BARS:]):
        if last - k > 3:
            break
        prior = [i for i in session if i < k][-RUN_BARS:]
        if len(prior) < RUN_BARS or not all((ctx.c[i] < vwap[i]) if below_side else (ctx.c[i] > vwap[i]) for i in prior):
            continue
        a = ctx.atr[k]
        span = ctx.h[k] - ctx.l[k]
        session_volume = float(np.mean(ctx.v[session[0]:k])) if k > session[0] else 0.0
        volume_ok = session_volume <= 0 or ctx.v[k] >= 1.2 * session_volume
        if kind == "vwap_reclaim":
            hit = ctx.c[k] > vwap[k] + 0.1 * a and volume_ok
            direction, invalidation = "long", max(float(ctx.l[k]), float(vwap[k] - 0.5 * a))
        elif kind == "vwap_loss":
            hit = ctx.c[k] < vwap[k] - 0.1 * a and volume_ok
            direction, invalidation = "short", min(float(ctx.h[k]), float(vwap[k] + 0.5 * a))
        elif kind == "vwap_rejection":
            hit = span > 0 and ctx.h[k] >= vwap[k] - 0.1 * a and ctx.c[k] <= vwap[k] - 0.1 * a and (ctx.c[k] - ctx.l[k]) / span <= 0.5
            direction, invalidation = "short", float(ctx.h[k])
        else:
            hit = span > 0 and ctx.l[k] <= vwap[k] + 0.1 * a and ctx.c[k] >= vwap[k] + 0.1 * a and (ctx.h[k] - ctx.c[k]) / span <= 0.5
            direction, invalidation = "long", float(ctx.l[k])
        if not hit:
            continue
        s = 1.0 if direction == "long" else -1.0
        risk = abs(ctx.c[k] - invalidation)
        session_extreme = float(ctx.h[session[0]:k + 1].max() if s > 0 else ctx.l[session[0]:k + 1].min())
        target = session_extreme if s * (session_extreme - ctx.c[k]) > risk else float(ctx.c[k] + s * 2 * risk)
        signal_close = float(ctx.c[k])
        resolved = resolve(ctx, direction=direction, ready_index=k, trigger=signal_close - s * 1e-9, invalidation=invalidation,
                           target=target, awaiting_bars=0, signal_bars=min(3, session[-1] - k + 3))
        return [build_detection(ctx, spec, direction=direction, resolved=resolved, anchor_index=prior[0], start_index=prior[0], end_index=k,
                                points=[ctx.point("run start", prior[0], ctx.c[prior[0]]), ctx.point("signal bar", k, ctx.c[k])],
                                lines=[ctx.segment("session VWAP (at signal)", prior[0], vwap[prior[0]], k, vwap[k], kind="reference")],
                                trigger=signal_close, invalidation=invalidation, target=target,
                                trigger_rule=spec.confirmation, measurements={"vwap_at_signal": vwap[k],
                                                                              "signal_volume_vs_session": (ctx.v[k] / session_volume) if session_volume else None},
                                evidence=[f"{RUN_BARS}+ closes {'below' if below_side else 'above'} VWAP before the signal"])]
    return []


def _mean_reversion_spec(up: bool) -> model.DetectorSpec:
    return model.DetectorSpec(
        id="mean_reversion_long" if up else "mean_reversion_short", version="1.0.0",
        name="Oversold mean reversion (long)" if up else "Overbought mean reversion (short)", family="mean_reversion",
        direction="long" if up else "short", timeframes=("1d",), min_bars=210, specificity=2,
        structure=(f"Long-term trend filter: close {'above' if up else 'below'} the 200-day SMA. Stretch: RSI(2) "
                   f"{'below 10' if up else 'above 90'} and close at least 1.0 ATR {'below' if up else 'above'} the 20-day SMA."),
        prior_trend=f"Close {'above' if up else 'below'} the 200-day SMA.", swing_rules="None.",
        duration="Setup bar within the last 3 bars; turn within 3 bars.", tolerances="Stretch 1.0 ATR from the 20-day SMA.",
        volume="Not used.", confirmation=f"A close {'above the prior bar high' if up else 'below the prior bar low'} (the turn).",
        invalidation=f"A close {'below the setup bar low' if up else 'above the setup bar high'} by 0.5 ATR.",
        expiration="No turn within 3 bars; actionable 2 bars after the turn; the 20-day SMA as target.",
        regimes=("uptrend", "range") if up else ("downtrend", "range"), data_requirements="At least 210 daily bars.",
        interpretation="A short-term pullback in a long-term trend (L. Connors' RSI(2) work) with an ATR stretch filter and a price-turn trigger added so entries are not taken while still falling.",
        ambiguity="Connors' published rules enter on the close without a turn trigger and exit on a moving-average cross; this version differs deliberately and is labeled as such.",
        sources=("L. Connors & C. Alvarez, Short Term Trading Strategies That Work (RSI(2))", "StockCharts ChartSchool - RSI(2)"),
        parameters={"rsi_period": 2, "rsi_threshold": 10 if up else 90, "stretch_atr": 1.0},
    )


def _mean_reversion(ctx, spec, up: bool) -> List[Dict[str, Any]]:
    s = 1.0 if up else -1.0
    sma20, sma200, rsi2 = ctx.sma(20), ctx.sma(200), ctx.rsi(2)
    for k in range(ctx.last, max(ctx.last - 3, 0), -1):
        if not (np.isfinite(sma200[k]) and np.isfinite(rsi2[k])):
            continue
        trend = s * (ctx.c[k] - sma200[k]) > 0
        stretched = s * (sma20[k] - ctx.c[k]) >= 1.0 * ctx.atr[k]
        extreme_rsi = rsi2[k] < 10 if up else rsi2[k] > 90
        if not (trend and stretched and extreme_rsi):
            continue
        setup_extreme = float(ctx.l[k] if up else ctx.h[k])
        invalidation = setup_extreme - s * 0.5 * ctx.atr[k]
        target = lambda i: float(sma20[i])
        resolved = resolve(ctx, direction="long" if up else "short", ready_index=k + 1 if k < ctx.last else k,
                           trigger=lambda i: ctx.h[i - 1] if up else ctx.l[i - 1], invalidation=invalidation, target=target,
                           awaiting_bars=3, signal_bars=2)
        if k == ctx.last:
            resolved = {"state": model.AWAITING_CONFIRMATION, "reason": "stretched; waiting for the turn bar", "confirmed_index": None,
                        "ended_index": None, "expires_index": k + 3, "notes": []}
        return [build_detection(ctx, spec, direction="long" if up else "short", resolved=resolved, anchor_index=k, start_index=max(k - 20, 0),
                                end_index=k, points=[ctx.point("setup bar", k, setup_extreme)],
                                lines=[ctx.segment("20 SMA", max(k - 20, 0), sma20[max(k - 20, 0)], ctx.last, sma20[ctx.last], kind="reference")],
                                trigger=lambda i: ctx.h[max(i - 1, 0)] if up else ctx.l[max(i - 1, 0)], invalidation=invalidation,
                                target=target, trigger_rule=spec.confirmation,
                                measurements={"rsi2": rsi2[k], "stretch_atr": s * (sma20[k] - ctx.c[k]) / ctx.atr[k]},
                                evidence=[f"RSI(2) {rsi2[k]:.1f}, {s * (sma20[k] - ctx.c[k]) / ctx.atr[k]:.1f} ATR from the 20 SMA, long-term trend intact"])]
    return []


for _kind in ("vwap_reclaim", "vwap_loss", "vwap_rejection", "vwap_support_hold"):
    model.detector(_vwap_spec(_kind))(lambda ctx, spec, _k=_kind: _vwap(ctx, spec, _k))
for _up in (True, False):
    model.detector(_mean_reversion_spec(_up))(lambda ctx, spec, _u=_up: _mean_reversion(ctx, spec, _u))

model.register_pending_definition(
    "King's crown", aliases=("king's shoulder", "king shoulder", "kings crown"),
    note=("Informal name without an established published definition. Not detected until its rules are written down "
          "(an example chart with the swing points marked is enough). The standard head-and-shoulders formations are covered."),
)
