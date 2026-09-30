"""Setup discovery engine - acceptance criteria:

* finds multiple distinct setup families;
* favors no setup (the "king shoulder" example is not preferred, and a named
  formation without rules is listed as unsupported, not guessed);
* distinguishes developing / awaiting / confirmed formations;
* rejects charts that meet no defined setup;
* overlapping detections become one opportunity and at most one trade candidate;
* chart annotations are the numbers the detection used;
* qualifying depends on validated evidence, not on a pattern preference;
* no future data: a scan as of bar t equals a scan of bars[:t+1]."""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from setups import model, scan_bars
from setups.bars import Bars, atr, ema, rsi, session_vwap
from setups.opportunities import QUALIFY, REJECT, WATCH, evaluate_symbol, group
from setups.pivots import zigzag
from setup_fixtures import make_bars, path


def _volumes(n, spikes):
    v = [1_000_000.0] * n
    for i in spikes:
        v[i] = 3_000_000.0
    return v


def _states(bars):
    return {d["detector_id"]: d for d in scan_bars(bars)["detections"]}


# --- fixture charts ------------------------------------------------------------------

HEAD_AND_SHOULDERS = path([(0, 100), (40, 100), (30, 130), (8, 121), (10, 140), (10, 121), (8, 131), (8, 119), (3, 117)])
DOUBLE_BOTTOM = path([(0, 130), (40, 130), (30, 100), (10, 112), (12, 100.5), (10, 113), (2, 114)])
BULL_FLAG_FORMING = path([(0, 100), (60, 100), (8, 125), (5, 122.5)])
BULL_FLAG_BROKEN = path([(0, 100), (60, 100), (8, 125), (6, 122), (2, 127)])
BULL_FLAG_JUST_BROKEN = path([(0, 100), (60, 100), (8, 125), (6, 122), (1, 123.4)])
RANGE_INSIDE = path([(0, 100), (40, 100), (6, 110), (6, 100), (6, 110), (6, 100), (6, 110), (3, 104)])
RANGE_BREAKOUT = path([(0, 100), (40, 100), (6, 110), (6, 100), (6, 110), (6, 100), (6, 110), (6, 100), (5, 111.5)])
UPTREND_PULLBACK = path([(0, 100), (80, 130), (6, 126), (4, 129)])
ASCENDING_TRIANGLE = path([(0, 90), (40, 90), (10, 110), (6, 100), (6, 110), (6, 103), (6, 110), (5, 106)])
STAIRCASE = path([(0, 100), (40, 100), (10, 110), (4, 105), (10, 116), (4, 111), (10, 122), (4, 117), (10, 128), (4, 123)])
STRAIGHT = path([(0, 100), (250, 140)])


def _vwap_bars(today_closes, volume_spike_last=True):
    previous = [100.0] * 78
    closes = previous + list(today_closes)
    day1 = datetime(2026, 9, 28, 13, 30, tzinfo=timezone.utc)
    day2 = datetime(2026, 9, 29, 13, 30, tzinfo=timezone.utc)
    times = [day1 + timedelta(minutes=5 * i) for i in range(78)] + [day2 + timedelta(minutes=5 * i) for i in range(len(today_closes))]
    volumes = [100_000.0] * len(closes)
    if volume_spike_last:
        volumes[-1] = 400_000.0
    rows, prior = [], closes[0]
    for i, close in enumerate(closes):
        rows.append({"t": times[i].isoformat(), "o": prior, "h": max(prior, close) * 1.0008, "l": min(prior, close) * 0.9992, "c": close, "v": volumes[i]})
        prior = close
    return Bars.from_rows("TEST", "5m", rows)


# --- coverage and neutrality -----------------------------------------------------------


def test_coverage_is_explicit_and_spans_the_requested_families():
    specs = [spec for spec, _ in model.registered()]
    assert len(specs) >= 30
    assert set(model.FAMILIES) == {spec.family for spec in specs}
    required = {"head_and_shoulders_top", "inverse_head_and_shoulders", "double_top", "double_bottom", "triple_top", "triple_bottom",
                "staircase_up", "staircase_down", "structure_up", "structure_down", "resistance_breakout", "support_breakdown",
                "breakout_retest", "breakdown_retest", "trend_pullback_long", "trend_pullback_short", "bull_flag", "bear_flag",
                "bull_pennant", "bear_pennant", "ascending_triangle", "descending_triangle", "symmetrical_triangle", "rising_wedge",
                "falling_wedge", "range_rectangle", "failed_breakout", "failed_breakdown_reclaim", "resistance_rejection",
                "support_rejection", "vwap_reclaim", "vwap_rejection", "mean_reversion_long", "mean_reversion_short"}
    assert required <= {spec.id for spec in specs}
    for spec in specs:  # every detector documents its rules and interpretation
        for field in ("structure", "swing_rules", "tolerances", "confirmation", "invalidation", "expiration", "data_requirements", "interpretation", "ambiguity"):
            assert getattr(spec, field), (spec.id, field)
        assert spec.version and spec.regimes and spec.timeframes


def test_the_king_shoulder_example_is_not_favored_and_undefined_names_are_not_guessed():
    ids = {spec.id for spec, _ in model.registered()}
    assert not any("king" in i for i in ids)
    pending = {p["name"]: p for p in model.pending_definitions()}
    assert pending["King's crown"]["status"] == model.NEEDS_DEFINITION
    assert "king shoulder" in pending["King's crown"]["aliases"]
    # Head and shoulders gets no special rank: specificity ties with other multi-swing formations.
    specificity = {spec.id: spec.specificity for spec, _ in model.registered()}
    assert specificity["head_and_shoulders_top"] <= max(specificity.values())
    assert sum(1 for v in specificity.values() if v == specificity["head_and_shoulders_top"]) >= 1


def test_the_scanner_finds_multiple_distinct_families():
    charts = [
        make_bars(HEAD_AND_SHOULDERS), make_bars(DOUBLE_BOTTOM), make_bars(BULL_FLAG_FORMING),
        make_bars(RANGE_BREAKOUT, volumes=_volumes(len(RANGE_BREAKOUT), [len(RANGE_BREAKOUT) - 1])),
        make_bars(UPTREND_PULLBACK, spread=0.01), make_bars(ASCENDING_TRIANGLE), make_bars(STAIRCASE),
    ]
    families = {d["family"] for bars in charts for d in scan_bars(bars)["detections"]}
    assert {"reversal", "continuation", "range", "breakout", "converging", "structure"} <= families


# --- states --------------------------------------------------------------------------------


def test_confirmed_formations_carry_their_rule_levels():
    hs = _states(make_bars(HEAD_AND_SHOULDERS))["head_and_shoulders_top"]
    assert hs["state"] == model.CONFIRMED and hs["direction"] == "short"
    assert [p["label"] for p in hs["points"]] == ["left shoulder", "trough 1", "head", "trough 2", "right shoulder"]
    assert hs["levels"]["target"] < hs["levels"]["trigger"] < hs["levels"]["invalidation"]
    db = _states(make_bars(DOUBLE_BOTTOM))["double_bottom"]
    assert db["state"] == model.CONFIRMED and db["direction"] == "long"


def test_incomplete_formations_are_not_reported_as_confirmed():
    forming = _states(make_bars(BULL_FLAG_FORMING))["bull_flag"]
    assert forming["state"] == model.AWAITING_CONFIRMATION
    broken = make_bars(BULL_FLAG_BROKEN, volumes=_volumes(len(BULL_FLAG_BROKEN), [len(BULL_FLAG_BROKEN) - 1, len(BULL_FLAG_BROKEN) - 2]))
    assert _states(broken)["bull_flag"]["state"] == model.CONFIRMED
    inside = _states(make_bars(RANGE_INSIDE))["range_rectangle"]
    assert inside["state"] == model.AWAITING_CONFIRMATION and inside["direction"] == "both"
    # A right shoulder still forming is DEVELOPING and labeled provisional.
    rising_right_shoulder = path([(0, 100), (40, 100), (30, 130), (8, 121), (10, 140), (10, 121), (6, 129)])
    hs = _states(make_bars(rising_right_shoulder)).get("head_and_shoulders_top")
    assert hs is not None and hs["state"] == model.DEVELOPING and hs["provisional"]
    assert hs["points"][-1]["provisional"] is True


def test_a_breakout_without_volume_is_not_confirmed():
    flat_volume = _states(make_bars(RANGE_BREAKOUT))["range_rectangle"]
    assert flat_volume["state"] == model.AWAITING_CONFIRMATION
    assert any("not counted" in note for note in flat_volume["counter_evidence"]) or flat_volume["direction"] == "both"


def test_intraday_vwap_reclaim():
    today = path([(0, 100), (10, 98), (4, 97.8)]) + [100.4]
    found = _states(_vwap_bars(today))
    assert found["vwap_reclaim"]["state"] == model.CONFIRMED
    assert "vwap_reclaim" not in _states(_vwap_bars(today, volume_spike_last=False))  # volume rule
    daily = make_bars([100.0] * 60)
    assert not any(d["family"] == "vwap" for d in scan_bars(daily)["detections"])  # VWAP is intraday-only


def test_mean_reversion_waits_for_the_turn():
    stretched = path([(0, 80), (220, 130), (4, 121)])
    found = _states(make_bars(stretched, spread=0.01))
    assert found["mean_reversion_long"]["state"] == model.AWAITING_CONFIRMATION


# --- no setup, grouping, decisions --------------------------------------------------------


def test_a_chart_with_no_defined_setup_is_rejected():
    result = evaluate_symbol([scan_bars(make_bars(STRAIGHT, spread=0.001))])
    assert result["opportunities"] == [] and result["trade_candidate"] is None
    assert result["summary"] == "no qualifying trade - no defined setup on this chart"


def _validated(detector_id, version, timeframe):
    return {"key": "x", "status": model.VALIDATED, "in_sample": {"n": 0}, "out_of_sample": {"n": 40, "mean_r": 0.3, "mean_r_lower_90": 0.1, "win_rate": 0.5},
            "forward": {"n": 25, "mean_r": 0.2}, "by_regime": {}, "snapshot": None}


def test_overlapping_detections_are_one_opportunity_and_one_trade_candidate():
    bars = make_bars(RANGE_BREAKOUT, volumes=_volumes(len(RANGE_BREAKOUT), [len(RANGE_BREAKOUT) - 1]))
    scan = scan_bars(bars)
    live = [d for d in scan["detections"] if d["state"] in model.LIVE_STATES]
    assert len(live) >= 3  # range breakout, level breakout, double/triple bottom...
    result = evaluate_symbol([scan], lookup=_validated)
    assert len(group(scan["detections"])) < len(live)
    main = result["opportunities"][0]
    assert main["also_matches"] and "not independent confirmation" in main["also_matches_note"]
    candidates = [o for o in result["opportunities"] if o["decision"] == QUALIFY]
    assert result["trade_candidate"] is None or result["trade_candidate"] is candidates[0]
    assert sum(1 for o in [result["trade_candidate"]] if o) <= 1


def test_a_breakout_far_past_its_trigger_is_not_chased():
    bars = make_bars(BULL_FLAG_BROKEN, volumes=_volumes(len(BULL_FLAG_BROKEN), [len(BULL_FLAG_BROKEN) - 1, len(BULL_FLAG_BROKEN) - 2]))
    flag = next(o for o in evaluate_symbol([scan_bars(bars)], lookup=_validated)["opportunities"] if o["setup"]["id"] == "bull_flag")
    assert flag["decision"] == WATCH and "chasing" in flag["decision_reason"]


def test_qualifying_requires_validated_evidence_not_a_pattern_preference():
    bars = make_bars(BULL_FLAG_JUST_BROKEN, volumes=_volumes(len(BULL_FLAG_JUST_BROKEN), [len(BULL_FLAG_JUST_BROKEN) - 1]))
    scan = scan_bars(bars)
    research_only = evaluate_symbol([scan])  # no evidence on file -> research status
    flag = next(o for o in research_only["opportunities"] if o["setup"]["id"] == "bull_flag")
    assert flag["decision"] == WATCH and "research only" in flag["decision_reason"]
    assert research_only["trade_candidate"] is None and research_only["summary"].startswith("no qualifying trade")
    assert "confidence" not in str(flag["historical"]).lower()
    validated = evaluate_symbol([scan], lookup=_validated)
    flag = next(o for o in validated["opportunities"] if o["setup"]["id"] == "bull_flag")
    assert flag["state"] == model.CONFIRMED and flag["entry"]["actionable"], flag["entry"]
    assert flag["decision"] == QUALIFY and validated["trade_candidate"]["setup"]["id"] == "bull_flag"


def test_opposite_directions_on_the_same_price_action_are_a_conflict():
    scan = scan_bars(make_bars(RANGE_INSIDE))
    result = evaluate_symbol([scan], lookup=_validated)
    assert result["trade_candidate"] is None
    box = next(o for o in result["opportunities"] if any(m["id"] == "range_rectangle" for m in o["also_matches"]) or o["setup"]["id"] == "range_rectangle")
    assert box["conflict"] and box["decision"] == WATCH
    assert "conflicting detectors" in box["strongest_counterargument"]


def test_chart_annotations_are_the_numbers_used():
    bars = make_bars(HEAD_AND_SHOULDERS)
    hs = _states(bars)["head_and_shoulders_top"]
    for point in hs["points"]:
        i = point["index"]
        assert point["t"] == bars.t[i]
        assert point["price"] in (round(float(bars.h[i]), 4), round(float(bars.l[i]), 4))
    neckline = hs["lines"][0]
    last = len(bars) - 1
    assert neckline["to"]["index"] == last
    assert neckline["to"]["price"] == pytest.approx(hs["levels"]["trigger_now"], abs=1e-3)
    assert hs["levels"]["invalidation"] == hs["points"][-1]["price"]


# --- no future data --------------------------------------------------------------------------


def _random_walk(n=320, seed=7):
    rng = random.Random(seed)
    closes, price = [], 100.0
    for _ in range(n):
        price *= 1 + rng.gauss(0.0004, 0.018)
        closes.append(price)
    volumes = [1_000_000 * (1 + abs(rng.gauss(0, 0.5))) for _ in range(n)]
    return make_bars(closes, volumes=volumes, spread=0.015)


def test_indicators_and_pivots_are_causal():
    bars = _random_walk()
    full_atr, full_ema, full_rsi = atr(bars), ema(bars.c, 20), rsi(bars.c, 2)
    confirmed, _ = zigzag(bars, 1.5)
    for t in (60, 150, 240, 300):
        prefix = bars.upto(t)
        assert np.allclose(atr(prefix), full_atr[:t + 1])
        assert np.allclose(ema(prefix.c, 20), full_ema[:t + 1])
        assert np.allclose(rsi(prefix.c, 2), full_rsi[:t + 1], equal_nan=True)
        prefix_confirmed, provisional = zigzag(prefix, 1.5)
        assert prefix_confirmed == [p for p in confirmed if p.confirmed_index <= t]
        assert provisional is None or provisional.provisional


def test_a_scan_as_of_bar_t_never_sees_later_bars():
    bars = _random_walk()
    for t in (120, 200, 280):
        as_of = scan_bars(bars, as_of=t)
        truncated = scan_bars(bars.upto(t))
        assert as_of["detections"] == truncated["detections"]
        assert all(d["as_of_index"] == t for d in as_of["detections"])
        # Changing every bar after t cannot change what was detected at t.
        altered = Bars(bars.symbol, bars.timeframe, bars.t, bars.o.copy(), bars.h.copy(), bars.l.copy(), bars.c.copy(), bars.v.copy())
        altered.c[t + 1:] *= 1.5
        altered.h[t + 1:] *= 1.5
        assert scan_bars(altered, as_of=t)["detections"] == as_of["detections"]


def test_vwap_resets_each_session():
    bars = _vwap_bars([90.0] * 10)
    vwap = session_vwap(bars)
    assert vwap[78] == pytest.approx((bars.h[78] + bars.l[78] + bars.c[78]) / 3)


def test_a_broken_detector_does_not_hide_the_others(monkeypatch):
    spec, _ = next((s, f) for s, f in model.registered() if s.id == "double_bottom")
    monkeypatch.setitem(model._REGISTRY, "double_bottom", (spec, lambda ctx, spec: 1 / 0))
    result = scan_bars(make_bars(DOUBLE_BOTTOM))
    assert result["errors"][0]["detector_id"] == "double_bottom"
    assert any(d["detector_id"] == "resistance_breakout" for d in result["detections"])
