from __future__ import annotations

import nasdaq_universe

SAMPLE = """Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares
AAPL|Apple Inc. - Common Stock|Q|N|N|100|N|N
QQQ|Invesco QQQ Trust|G|N|N|100|Y|N
ZWZZT|NASDAQ TEST STOCK|G|Y|N|100|N|N
AAPL.W|Warrant|Q|N|N|100|N|N
BRK.B|Not a nasdaq line but dotted|Q|N|N|100|N|N
SPY|This file would not contain NYSE, included only to show the parser keeps plain symbols|Q|N|N|100|N|N
File Creation Time: 1006202619:00
"""


def test_parser_keeps_common_stock_and_etfs_and_drops_tests_and_warrants():
    assert nasdaq_universe.parse_nasdaq_listed(SAMPLE) == ["AAPL", "QQQ", "SPY"]


def test_fresh_download_is_cached(monkeypatch, tmp_path):
    monkeypatch.setenv("PLUTO_DATA_DIR", str(tmp_path))
    symbols, source = nasdaq_universe.load_symbols(["FALL"], fetcher=lambda: SAMPLE)
    assert source == "nasdaq"
    assert symbols[0] == "AAPL"
    symbols_again, source_again = nasdaq_universe.load_symbols(["FALL"], fetcher=lambda: "not called")
    assert source_again == "cache"
    assert symbols_again == symbols


def test_download_failure_uses_the_saved_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("PLUTO_DATA_DIR", str(tmp_path))
    nasdaq_universe.load_symbols(fetcher=lambda: SAMPLE)
    cache = nasdaq_universe._cache_path()
    import json
    body = json.loads(cache.read_text(encoding="utf-8"))
    body["fetched_at"] = 0
    cache.write_text(json.dumps(body), encoding="utf-8")

    def _boom():
        raise RuntimeError("nasdaq down")

    symbols, source = nasdaq_universe.load_symbols(["FALL"], fetcher=_boom)
    assert source == "cache"
    assert "AAPL" in symbols


def test_download_failure_with_no_cache_uses_the_fallback(monkeypatch, tmp_path):
    monkeypatch.setenv("PLUTO_DATA_DIR", str(tmp_path))

    def _boom():
        raise RuntimeError("nasdaq down")

    symbols, source = nasdaq_universe.load_symbols(["spy", "QQQ", "spy"], fetcher=_boom)
    assert source == "fallback"
    assert symbols == ["SPY", "QQQ"]
