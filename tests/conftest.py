"""Test isolation: nothing in the suite may read or write the real ./data directory.

backend.app builds an app (and a paper engine) at import time and several services use
cwd-relative "data/..." paths, so the working directory and every data-path environment
variable are redirected to a throwaway directory BEFORE backend is imported anywhere.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
_SANDBOX = tempfile.mkdtemp(prefix="pluto_test_data_")

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

(Path(_SANDBOX) / "data").mkdir(exist_ok=True)
for _name, _file in {
    "PLUTO_SIGNALS_FILE": "signals.json",
    "PLUTO_SIGNAL_APPROVALS_FILE": "signal_approvals.json",
    "PLUTO_PAPER_LEDGER_FILE": "paper_ledger.json",
}.items():
    os.environ[_name] = str(Path(_SANDBOX) / "data" / _file)
os.environ.setdefault("PLUTO_DISABLE_MARKET_STREAM", "true")
os.environ.setdefault("PLUTO_ENABLE_ORDER_MONITOR", "false")
os.environ.setdefault("PLUTO_SECRET_KEY", "test-secret-key")


import pytest  # noqa: E402


def pytest_sessionstart(session):  # noqa: ARG001
    # Done here (not at import) so pytest resolves testpaths from the real project directory first.
    os.chdir(_SANDBOX)


@pytest.fixture(autouse=True)
def _network_is_disabled(monkeypatch):
    """No test may reach a real market-data provider: tests that need prices inject them, everything else
    sees a deterministic outage (the app is required to degrade gracefully). This keeps the suite fast
    (it previously waited on rate-limited Yahoo calls for minutes) and independent of the internet."""
    import requests
    import yfinance

    def _offline(*_args, **_kwargs):
        raise ConnectionError("network access is disabled in tests")

    monkeypatch.setattr(yfinance, "download", _offline)
    monkeypatch.setattr(yfinance, "Ticker", _offline)
    monkeypatch.setattr(requests, "get", _offline)
    yield
