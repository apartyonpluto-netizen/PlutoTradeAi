from __future__ import annotations

import suggestion_ledger
from test_research_log_scan_integration import _opportunities, _run_scan_and_capture


def test_confirm_mode_logs_the_setup_and_does_not_place(user_id, monkeypatch):
    monkeypatch.setenv("PLUTO_CONFIRM_TO_ENTER", "1")
    monkeypatch.setenv("PLUTO_PUBLIC_BASE_URL", "https://pluto.example")
    monkeypatch.setenv("PLUTO_ALERT_QUIET_HOURS", "")
    monkeypatch.setenv("PLUTO_EVIDENCE_GATES", "0")
    result = _run_scan_and_capture(user_id, _opportunities())
    assert result["placed_count"] == 0
    rows = [row for row in suggestion_ledger.list_rows(user_id) if row.get("ticker") == "AAPL"]
    assert rows
    assert rows[-1]["decision"] == "awaiting_confirm"
    assert rows[-1]["entry"] == 100.0
    assert rows[-1]["stop"] == 50.0
    assert rows[-1]["target"] == 110.0
