from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

import regime


@pytest.fixture(autouse=True)
def _reset_vix_cache():
    # regime.py deliberately caches at module scope (see its own
    # docstring) - every test gets a clean cache rather than relying on
    # import/execution order between tests.
    regime._QUOTE_CACHE["quote"] = None
    regime._QUOTE_CACHE["cached_at"] = None
    yield
    regime._QUOTE_CACHE["quote"] = None
    regime._QUOTE_CACHE["cached_at"] = None


def _fake_history(price: float, bar_time: datetime) -> pd.DataFrame:
    index = pd.DatetimeIndex([bar_time])
    return pd.DataFrame({"Close": [price]}, index=index)


def _mock_ticker(price: float, bar_time: datetime):
    ticker = MagicMock()
    ticker.history.return_value = _fake_history(price, bar_time)
    return ticker


# --- get_vix_snapshot: fresh / caching --------------------------------------------


def test_fresh_quote_is_labeled_fresh_with_correct_age():
    now = datetime.now(timezone.utc)
    bar_time = now - timedelta(seconds=30)
    with patch.object(regime.yf, "Ticker", return_value=_mock_ticker(18.5, bar_time)):
        snapshot = regime.get_vix_snapshot()
    assert snapshot["vix_level"] == 18.5
    assert snapshot["status"] == "fresh"
    assert snapshot["used_stale_cache"] is False
    assert snapshot["source_time"] == bar_time
    assert 25 <= snapshot["age_seconds"] <= 35  # ~30s, allow for test execution time


def test_reuses_a_recent_fetch_without_hitting_yfinance_again():
    now = datetime.now(timezone.utc)
    with patch.object(regime.yf, "Ticker", return_value=_mock_ticker(18.5, now)) as mock_ticker_cls:
        first = regime.get_vix_snapshot()
        second = regime.get_vix_snapshot()
    assert first["vix_level"] == second["vix_level"] == 18.5
    mock_ticker_cls.assert_called_once()
    # Ordinary cache reuse, NOT a stale fallback - see the module docstring.
    assert second["used_stale_cache"] is False
    assert second["status"] == "fresh"


def test_force_refresh_bypasses_the_cache():
    now = datetime.now(timezone.utc)
    with patch.object(regime.yf, "Ticker", return_value=_mock_ticker(18.5, now)):
        regime.get_vix_snapshot()
    with patch.object(regime.yf, "Ticker", return_value=_mock_ticker(31.0, now)) as mock_ticker_cls:
        refreshed = regime.get_vix_snapshot(force_refresh=True)
    assert refreshed["vix_level"] == 31.0
    mock_ticker_cls.assert_called_once()


def test_refetches_once_the_refetch_interval_elapses():
    now = datetime.now(timezone.utc)
    with patch.object(regime.yf, "Ticker", return_value=_mock_ticker(18.5, now)):
        regime.get_vix_snapshot()
    regime._QUOTE_CACHE["cached_at"] = now - timedelta(seconds=regime.REFETCH_INTERVAL_SECONDS + 1)
    with patch.object(regime.yf, "Ticker", return_value=_mock_ticker(22.0, now)) as mock_ticker_cls:
        snapshot = regime.get_vix_snapshot()
    assert snapshot["vix_level"] == 22.0
    mock_ticker_cls.assert_called_once()


# --- get_vix_snapshot: stale-fallback vs unavailable ------------------------------


def test_failed_fetch_falls_back_to_cache_and_is_labeled_stale_not_fresh():
    now = datetime.now(timezone.utc)
    bar_time = now - timedelta(seconds=30)
    with patch.object(regime.yf, "Ticker", return_value=_mock_ticker(18.5, bar_time)):
        regime.get_vix_snapshot()
    regime._QUOTE_CACHE["cached_at"] = now - timedelta(seconds=regime.REFETCH_INTERVAL_SECONDS + 1)
    broken_ticker = MagicMock()
    broken_ticker.history.side_effect = RuntimeError("network down")
    with patch.object(regime.yf, "Ticker", return_value=broken_ticker):
        snapshot = regime.get_vix_snapshot()
    assert snapshot["vix_level"] == 18.5
    assert snapshot["used_stale_cache"] is True
    assert snapshot["status"] == "stale"  # never "fresh" - a fallback must not be treated as fully available


def test_never_fetched_and_fetch_fails_is_unavailable_not_stale():
    with patch.object(regime.yf, "Ticker", side_effect=RuntimeError("network down")):
        snapshot = regime.get_vix_snapshot()
    assert snapshot["vix_level"] is None
    assert snapshot["status"] == "unavailable"


def test_empty_history_is_unavailable():
    ticker = MagicMock()
    ticker.history.return_value = pd.DataFrame()
    with patch.object(regime.yf, "Ticker", return_value=ticker):
        snapshot = regime.get_vix_snapshot()
    assert snapshot["vix_level"] is None
    assert snapshot["status"] == "unavailable"


def test_quote_older_than_max_usable_age_is_unavailable_but_keeps_the_level_for_audit():
    now = datetime.now(timezone.utc)
    bar_time = now - timedelta(seconds=regime.MAX_USABLE_AGE_SECONDS + 60)
    with patch.object(regime.yf, "Ticker", return_value=_mock_ticker(18.5, bar_time)):
        snapshot = regime.get_vix_snapshot()
    assert snapshot["status"] == "unavailable"
    assert snapshot["vix_level"] == 18.5  # audit-visible even though it must not be used as a signal
    assert snapshot["age_seconds"] > regime.MAX_USABLE_AGE_SECONDS


def test_quote_at_exactly_the_max_usable_age_boundary_is_still_usable():
    now = datetime.now(timezone.utc)
    bar_time = now - timedelta(seconds=regime.MAX_USABLE_AGE_SECONDS - 1)
    with patch.object(regime.yf, "Ticker", return_value=_mock_ticker(18.5, bar_time)):
        snapshot = regime.get_vix_snapshot()
    assert snapshot["status"] == "fresh"


def test_future_source_timestamp_is_unavailable_never_a_negative_age():
    now = datetime.now(timezone.utc)
    future_bar_time = now + timedelta(seconds=120)
    with patch.object(regime.yf, "Ticker", return_value=_mock_ticker(18.5, future_bar_time)):
        snapshot = regime.get_vix_snapshot()
    assert snapshot["status"] == "unavailable"
    assert snapshot["vix_level"] is None
    assert snapshot["age_seconds"] is None


def test_naive_bar_timestamp_is_treated_as_utc_not_rejected():
    now = datetime.now(timezone.utc)
    naive_bar_time = (now - timedelta(seconds=30)).replace(tzinfo=None)
    with patch.object(regime.yf, "Ticker", return_value=_mock_ticker(18.5, naive_bar_time)):
        snapshot = regime.get_vix_snapshot()
    assert snapshot["status"] == "fresh"
    assert snapshot["source_time"].tzinfo is not None


# --- compute_shadow_adjustment ------------------------------------------------------


def test_shadow_adjustment_is_zero_when_unavailable():
    snapshot = {"vix_level": None, "status": "unavailable"}
    result = regime.compute_shadow_adjustment(snapshot)
    assert result["proposed_adjustment"] == 0
    assert result["mapping_version"] == regime.REGIME_MAPPING_VERSION


def test_shadow_adjustment_is_nonzero_for_a_genuine_stale_but_usable_quote():
    # status="stale" (within MAX_USABLE_AGE_SECONDS, served from a failed-
    # fetch fallback) still gets a real proposed adjustment - only
    # "unavailable" forces a hard zero. See compute_shadow_adjustment's
    # own docstring for why: it's still recorded, just clearly labeled.
    snapshot = {"vix_level": 32.0, "status": "stale"}
    result = regime.compute_shadow_adjustment(snapshot)
    assert result["proposed_adjustment"] == -15
    assert "stale" in result["reasoning"].lower()


@pytest.mark.parametrize(
    "vix_level,expected_adjustment",
    [
        (10.0, 0),
        (19.9, 0),
        (20.0, -5),
        (24.9, -5),
        (25.0, -10),
        (29.9, -10),
        (30.0, -15),
        (55.0, -15),
    ],
)
def test_shadow_adjustment_thresholds(vix_level, expected_adjustment):
    result = regime.compute_shadow_adjustment({"vix_level": vix_level, "status": "fresh"})
    assert result["proposed_adjustment"] == expected_adjustment


def test_shadow_adjustment_never_positive():
    for vix_level in (0.0, 5.0, 12.0, 19.99, 20.0, 45.0, 90.0):
        result = regime.compute_shadow_adjustment({"vix_level": vix_level, "status": "fresh"})
        assert result["proposed_adjustment"] <= 0


def test_mapping_version_is_labeled_as_unvalidated():
    # Explicit per the review that mandated shadow-only mode - the mapping
    # must be identifiable as an unearned research hypothesis, not a
    # calibrated production signal.
    assert "unvalidated" in regime.REGIME_MAPPING_VERSION
