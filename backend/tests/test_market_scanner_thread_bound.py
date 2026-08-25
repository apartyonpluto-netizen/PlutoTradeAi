from __future__ import annotations

from unittest.mock import patch

import pandas as pd

import market_scanner

"""Found live 2026-08-25 via this app's own MEMORY_PROFILE diagnostic
logging (still-active tracemalloc instrumentation, see app.py): peewee.py
and multitasking/__init__.py - both internal to yfinance, not this app's
own code - were the two allocation sites growing fastest across repeated
snapshots, even after fixing this app's own outer thread-pool leak
(_BACKGROUND_FETCH_EXECUTOR). Root cause one layer deeper: scan_market's
two yf.download() calls passed threads=True, handing yfinance's OWN
internal multitasking-based thread pool one thread per ticker (up to 48
here) - entirely separate from and beneath this app's own outer
_run_with_hard_deadline wrapping. If that outer deadline gives up on a
slow/rate-limited batch, every one of yfinance's own inner threads is
abandoned too, unbounded. This test proves the fix: a bounded thread
count is actually passed, not just documented - a plain "was
threads= truthy" check would pass for threads=True just as easily as
threads=8, which is exactly the bug."""


def test_scan_market_bounds_yfinance_internal_thread_count():
    captured_kwargs = []

    def _fake_download(**kwargs):
        captured_kwargs.append(kwargs)
        return pd.DataFrame()

    with patch.object(market_scanner.yf, "download", side_effect=_fake_download):
        market_scanner.scan_market(tickers=["AAPL", "MSFT"])

    assert len(captured_kwargs) == 2, "expected one call each for daily and intraday"
    for kwargs in captured_kwargs:
        threads = kwargs.get("threads")
        assert threads is not True, "threads=True hands yfinance an unbounded internal thread pool"
        assert isinstance(threads, int) and 1 <= threads <= 16, f"expected a small bounded int, got {threads!r}"
