"""The duplicate-position guard must not lock a ticker out because of a
record that was never an open entry (a skip, or a manual-close SELL)."""

import app as pluto_app
import order_lifecycle as ol


def _lifecycle(ticker, state):
    entry = {"ticker": ticker}
    ol.initialize(entry, state)
    return entry


def test_skipped_and_failed_candidates_do_not_block_their_ticker():
    orders = [
        {"ticker": "AAPL", "status": "skipped", "skip_category": "price_drift"},
        {"ticker": "NVDA", "status": "skipped", "skip_category": "llm_veto"},
        {"ticker": "AMD", "status": "failed", "error": "entry order failed"},
    ]
    assert pluto_app._tickers_with_open_positions(orders) == set()


def test_a_manual_close_sell_does_not_block_its_ticker():
    orders = [{"ticker": "TSLA", "side": "SELL", "status": "placed", "reason": "Manual close from Trade Journal"}]
    assert pluto_app._tickers_with_open_positions(orders) == set()


def test_open_lifecycle_entries_and_legacy_placed_entries_still_block():
    orders = [
        _lifecycle("MSFT", ol.ENTRY_SUBMITTED),
        _lifecycle("META", ol.PROTECTION_FAILED),
        {"ticker": "LEGACY", "side": "BUY", "status": "placed"},
        {"ticker": "SHORTY", "side": "SELL", "direction": "short", "status": "placed"},
    ]
    assert pluto_app._tickers_with_open_positions(orders) == {"MSFT", "META", "LEGACY", "SHORTY"}


def test_terminal_entries_do_not_block():
    orders = [_lifecycle("GOOG", ol.CLOSED), _lifecycle("AMZN", ol.ENTRY_FAILED)]
    assert pluto_app._tickers_with_open_positions(orders) == set()
