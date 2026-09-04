from __future__ import annotations

import pandas as pd

from brains.charting_brain import detect_trend_flags

"""detect_trend_flags - a direct port of analytics.py's detect_early_trends
onto Alpaca-sourced bars (2026-09-04), added to build_chart_levels's own
payload as `trend_flags` so the real dashboard chart can surface plain
trend/consolidation labels. Ported logic, not new analysis - these tests
exercise the port's own boolean outcomes directly against constructed
OHLCV frames, the same level the original module is naturally tested at."""


def _bars(closes, opens=None, highs=None, lows=None, volumes=None) -> pd.DataFrame:
    n = len(closes)
    opens = opens or [c - 0.5 for c in closes]
    highs = highs or [max(o, c) + 0.5 for o, c in zip(opens, closes)]
    lows = lows or [min(o, c) - 0.5 for o, c in zip(opens, closes)]
    volumes = volumes or [1_000_000] * n
    index = pd.date_range("2026-01-01", periods=n, freq="D")
    return pd.DataFrame({"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": volumes}, index=index)


def test_empty_history_returns_empty_dict():
    assert detect_trend_flags(pd.DataFrame(), support=90.0, resistance=110.0) == {}


def test_single_row_history_returns_empty_dict():
    assert detect_trend_flags(_bars([100.0]), support=90.0, resistance=110.0) == {}


def test_rising_highs_and_lows_flags_higher_highs_and_higher_lows():
    closes = [100, 101, 102, 103, 104, 105]
    highs = [c + 1 for c in closes]
    lows = [c - 1 for c in closes]
    flags = detect_trend_flags(_bars(closes, highs=highs, lows=lows), support=95.0, resistance=120.0)
    assert flags["higher_highs"] is True
    assert flags["higher_lows"] is True
    assert flags["lower_highs"] is False
    assert flags["lower_lows"] is False


def test_falling_highs_and_lows_flags_lower_highs_and_lower_lows():
    closes = [105, 104, 103, 102, 101, 100]
    highs = [c + 1 for c in closes]
    lows = [c - 1 for c in closes]
    flags = detect_trend_flags(_bars(closes, highs=highs, lows=lows), support=90.0, resistance=110.0)
    assert flags["lower_highs"] is True
    assert flags["lower_lows"] is True
    assert flags["higher_highs"] is False
    assert flags["higher_lows"] is False


def test_trend_continuation_true_when_higher_highs_and_lows_and_closing_up():
    closes = [100, 101, 102, 103, 104, 106]
    highs = [c + 1 for c in closes]
    lows = [c - 1 for c in closes]
    flags = detect_trend_flags(_bars(closes, highs=highs, lows=lows), support=95.0, resistance=120.0)
    assert flags["trend_continuation"] is True


def test_gap_up_detected_when_todays_low_above_yesterdays_high():
    closes = [100] * 5 + [110]
    opens = [100] * 5 + [109]
    highs = [101] * 5 + [111]
    lows = [99] * 5 + [108.5]  # 108.5 > previous day's high of 101
    flags = detect_trend_flags(_bars(closes, opens=opens, highs=highs, lows=lows), support=90.0, resistance=120.0)
    assert flags["gap_up"] is True


def test_gap_down_detected_when_todays_high_below_yesterdays_low():
    closes = [100] * 5 + [90]
    opens = [100] * 5 + [91]
    highs = [101] * 5 + [92]  # 92 < previous day's low of 99
    lows = [99] * 5 + [89]
    flags = detect_trend_flags(_bars(closes, opens=opens, highs=highs, lows=lows), support=80.0, resistance=110.0)
    assert flags["gap_down"] is True


def test_unusual_volume_flagged_on_a_large_spike():
    closes = [100] * 20 + [101]
    volumes = [1_000_000] * 20 + [3_000_000]
    flags = detect_trend_flags(_bars(closes, volumes=volumes), support=90.0, resistance=110.0)
    assert flags["unusual_volume"] is True


def test_volume_compression_flagged_on_a_dry_up():
    closes = [100] * 20 + [100.5]
    volumes = [1_000_000] * 20 + [500_000]
    flags = detect_trend_flags(_bars(closes, volumes=volumes), support=90.0, resistance=110.0)
    assert flags["volume_compression"] is True


def test_returns_every_expected_key():
    closes = [100 + i for i in range(10)]
    flags = detect_trend_flags(_bars(closes), support=95.0, resistance=120.0)
    expected_keys = {
        "volume_expansion", "volume_compression", "higher_highs", "higher_lows",
        "lower_highs", "lower_lows", "bull_flag", "bear_flag", "failed_breakout",
        "failed_breakdown", "trend_continuation", "trend_reversal", "institutional_buying",
        "sector_momentum", "relative_strength", "gap_up", "gap_down", "unusual_volume",
        "breakout_forming", "candle_reversal_near_support_resistance", "consolidating",
        "spinning_top",
    }
    assert expected_keys.issubset(flags.keys())
