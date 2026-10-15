from __future__ import annotations

import suggestion_ledger


def test_grade_and_what_if_use_only_the_forward_bars(user_id):
    row = suggestion_ledger.append({
        "user_id": user_id,
        "trading_day": "2024-01-02",
        "ticker": "NVDA",
        "recommendation": "CALL",
        "confidence": 80,
        "decision": "skipped",
        "skip_category": "user_skip",
        "entry": 100,
        "stop": 90,
        "target": 110,
        "quantity": 2,
        "grade_on": "2024-01-02",
    })
    bars = [
        {"date": "2024-01-02", "open": 100, "high": 101, "low": 99, "close": 100},
        {"date": "2024-01-03", "open": 100, "high": 112, "low": 99, "close": 111},
        {"date": "2024-01-04", "open": 111, "high": 112, "low": 110, "close": 111},
        {"date": "2024-01-05", "open": 111, "high": 112, "low": 110, "close": 111},
        {"date": "2024-01-08", "open": 111, "high": 112, "low": 110, "close": 111},
        {"date": "2024-01-09", "open": 111, "high": 112, "low": 110, "close": 111},
    ]
    graded = suggestion_ledger.grade_due(lambda ticker: bars, now=suggestion_ledger._now().replace(year=2024, month=1, day=10))
    assert graded
    assert graded[0]["hit_target"] is True
    assert graded[0]["forward_pnl_dollars"] == 20.0
    line = suggestion_ledger.what_if_line(suggestion_ledger.list_rows(user_id)[0])
    assert line == "NVDA setup you skipped would have made +$20."
    assert row["id"]


def test_hit_rates_group_the_confidence_known_at_the_time(user_id):
    suggestion_ledger.append({
        "user_id": user_id,
        "ticker": "AAPL",
        "confidence": 82,
        "decision": "skipped",
        "trading_day": "2024-01-02",
    })
    suggestion_ledger.update(
        suggestion_ledger.list_rows(user_id)[0]["id"],
        graded_at="2024-01-10T00:00:00+00:00",
        forward_return_percent=1.5,
    )
    buckets = {item["bucket"]: item for item in suggestion_ledger.hit_rates(user_id)}
    assert buckets["75-84"]["n"] == 1
    assert buckets["75-84"]["hit_rate"] == 1.0
