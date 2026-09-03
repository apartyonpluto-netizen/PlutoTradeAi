from __future__ import annotations

from unittest.mock import patch

from autonomy import options_selector

"""select_option_contract - real-broker-data contract selection for options
trading (2026-09-03). Mocks integrations.webull.get_option_contracts/
get_option_snapshot directly (the shapes those two return were confirmed
live against the sandbox - see their own docstrings) rather than any
broker client, matching the level this module actually talks to."""


def _contract(symbol, strike, expiration, option_type="CALL"):
    return {
        "symbol": symbol,
        "strike_price": str(strike),
        "expiration_date": expiration,
        "option_type": option_type,
        "underlying_symbol": "ADBE",
    }


def _snapshot_row(symbol, bid, ask, delta="0.45"):
    return {"symbol": symbol, "bid": str(bid), "ask": str(ask), "delta": delta}


def test_returns_none_when_no_contracts_listed():
    with patch.object(options_selector.webull_api, "get_option_contracts", return_value=[]):
        result = options_selector.select_option_contract("k", "s", "ADBE", "CALL", 275.0)
    assert result is None


def test_returns_none_when_current_price_invalid():
    result = options_selector.select_option_contract("k", "s", "ADBE", "CALL", 0)
    assert result is None
    result = options_selector.select_option_contract("k", "s", "ADBE", "CALL", None)
    assert result is None


def test_picks_nearest_expiration_first():
    contracts = [
        _contract("ADBE261016C00280000", 280, "2026-10-16"),
        _contract("ADBE260918C00280000", 280, "2026-09-18"),
    ]
    with patch.object(options_selector.webull_api, "get_option_contracts", return_value=contracts), \
         patch.object(options_selector.webull_api, "get_option_snapshot", return_value=[_snapshot_row("ADBE260918C00280000", 4.90, 5.10)]):
        result = options_selector.select_option_contract("k", "s", "ADBE", "CALL", 275.0)
    assert result["option_symbol"] == "ADBE260918C00280000"
    assert result["expiration_date"] == "2026-09-18"


def test_picks_strike_closest_to_current_price_within_nearest_expiration():
    contracts = [
        _contract("ADBE260918C00290000", 290, "2026-09-18"),
        _contract("ADBE260918C00276000", 276, "2026-09-18"),
        _contract("ADBE260918C00260000", 260, "2026-09-18"),
    ]
    with patch.object(options_selector.webull_api, "get_option_contracts", return_value=contracts), \
         patch.object(options_selector.webull_api, "get_option_snapshot", return_value=[_snapshot_row("ADBE260918C00276000", 4.90, 5.10)]):
        result = options_selector.select_option_contract("k", "s", "ADBE", "CALL", 275.0)
    assert result["option_symbol"] == "ADBE260918C00276000"
    assert result["strike"] == 276.0


def test_returns_none_when_snapshot_has_no_bid():
    contracts = [_contract("ADBE260918C00276000", 276, "2026-09-18")]
    with patch.object(options_selector.webull_api, "get_option_contracts", return_value=contracts), \
         patch.object(options_selector.webull_api, "get_option_snapshot", return_value=[{"symbol": "ADBE260918C00276000", "bid": "0", "ask": "5.10"}]):
        result = options_selector.select_option_contract("k", "s", "ADBE", "CALL", 275.0)
    assert result is None


def test_returns_none_when_snapshot_has_no_data_at_all():
    contracts = [_contract("ADBE260918C00276000", 276, "2026-09-18")]
    with patch.object(options_selector.webull_api, "get_option_contracts", return_value=contracts), \
         patch.object(options_selector.webull_api, "get_option_snapshot", return_value=[]):
        result = options_selector.select_option_contract("k", "s", "ADBE", "CALL", 275.0)
    assert result is None


def test_returns_none_when_spread_too_wide():
    # bid $0.04 / ask $0.07 -> spread/mid = (0.07-0.04)/0.055 = ~54%, over
    # the default 25% max_spread_pct - the real deep-OTM contract found
    # live during this feature's own discovery pass.
    contracts = [_contract("ADBE260918C00420000", 420, "2026-09-18")]
    with patch.object(options_selector.webull_api, "get_option_contracts", return_value=contracts), \
         patch.object(options_selector.webull_api, "get_option_snapshot", return_value=[_snapshot_row("ADBE260918C00420000", 0.04, 0.07)]):
        result = options_selector.select_option_contract("k", "s", "ADBE", "CALL", 420.0)
    assert result is None


def test_accepts_a_tight_spread_within_the_default_threshold():
    contracts = [_contract("ADBE260918C00276000", 276, "2026-09-18")]
    with patch.object(options_selector.webull_api, "get_option_contracts", return_value=contracts), \
         patch.object(options_selector.webull_api, "get_option_snapshot", return_value=[_snapshot_row("ADBE260918C00276000", 4.90, 5.10)]):
        result = options_selector.select_option_contract("k", "s", "ADBE", "CALL", 275.0)
    assert result is not None
    assert result["bid"] == 4.90
    assert result["ask"] == 5.10
    assert result["mid"] == 5.00
    assert result["delta"] == 0.45


def test_put_direction_queries_put_option_type():
    contracts = [_contract("ADBE260918P00270000", 270, "2026-09-18", option_type="PUT")]
    with patch.object(options_selector.webull_api, "get_option_contracts", return_value=contracts) as mock_contracts, \
         patch.object(options_selector.webull_api, "get_option_snapshot", return_value=[_snapshot_row("ADBE260918P00270000", 4.90, 5.10)]):
        result = options_selector.select_option_contract("k", "s", "ADBE", "PUT", 275.0)
    assert result["option_type"] == "PUT"
    _, kwargs = mock_contracts.call_args
    assert kwargs["option_type"] == "PUT"


def test_strike_search_band_is_passed_through_to_the_contracts_query():
    with patch.object(options_selector.webull_api, "get_option_contracts", return_value=[]) as mock_contracts:
        options_selector.select_option_contract("k", "s", "ADBE", "CALL", 200.0, strike_band_pct=0.10)
    _, kwargs = mock_contracts.call_args
    assert kwargs["strike_price_gte"] == 180.0
    assert kwargs["strike_price_lte"] == 220.0
