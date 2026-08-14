from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from autonomy.research_log import (
    RESEARCH_LOG_SCHEMA_VERSION,
    list_research_decisions,
    record_research_decision,
)

BACKEND_DIR = Path(__file__).resolve().parents[1]


def test_record_and_list_round_trip(user_id):
    record_research_decision(user_id, {"ticker": "AAPL", "decision": "placed"})
    records = list_research_decisions(user_id)
    assert len(records) == 1
    assert records[0]["ticker"] == "AAPL"
    assert records[0]["decision"] == "placed"


def test_every_record_is_stamped_with_schema_version_and_a_timestamp(user_id):
    record_research_decision(user_id, {"ticker": "AAPL"})
    record = list_research_decisions(user_id)[0]
    assert record["schema_version"] == RESEARCH_LOG_SCHEMA_VERSION
    assert record.get("logged_at")


def test_records_are_never_overwritten_pure_append(user_id):
    record_research_decision(user_id, {"ticker": "AAPL", "decision": "skipped"})
    record_research_decision(user_id, {"ticker": "AAPL", "decision": "skipped"})
    record_research_decision(user_id, {"ticker": "MSFT", "decision": "placed"})
    records = list_research_decisions(user_id)
    assert len(records) == 3  # duplicates of the SAME ticker/decision are not merged or deduped


def test_list_is_empty_for_a_user_with_no_records(user_id):
    assert list_research_decisions(user_id) == []


def test_list_order_is_oldest_first_append_order(user_id):
    record_research_decision(user_id, {"ticker": "FIRST"})
    record_research_decision(user_id, {"ticker": "SECOND"})
    record_research_decision(user_id, {"ticker": "THIRD"})
    tickers = [r["ticker"] for r in list_research_decisions(user_id)]
    assert tickers == ["FIRST", "SECOND", "THIRD"]


def test_a_users_records_are_isolated_from_another_users(user_id, other_user_id):
    record_research_decision(user_id, {"ticker": "AAPL"})
    record_research_decision(other_user_id, {"ticker": "MSFT"})
    assert [r["ticker"] for r in list_research_decisions(user_id)] == ["AAPL"]
    assert [r["ticker"] for r in list_research_decisions(other_user_id)] == ["MSFT"]


def _write_worker_command(user_id: str, ticker: str) -> list[str]:
    script = (
        "import sys; "
        "from autonomy.research_log import record_research_decision; "
        "record_research_decision(sys.argv[1], {'ticker': sys.argv[2]})"
    )
    return [sys.executable, "-c", script, user_id, ticker]


def test_concurrent_writes_across_real_processes_lose_no_records(user_id):
    """Real threat model here is multiple GUNICORN WORKER PROCESSES -
    genuinely separate OS processes, not threads within one interpreter
    (see test_webull_stop_orders_locking.py's own note on why a
    thread-based version would be a weaker proof). Without the fcntl
    lock around the read-modify-write cycle, two separate processes can
    each read the same starting state, append their own record in
    memory, and write - the second write silently clobbers the first's
    addition. For a log that exists specifically to prevent silent
    selection-bias gaps, a lost record here would be exactly that kind
    of gap, so this proves the lock actually holds across real
    processes."""
    tickers = [f"T{i}" for i in range(20)]
    commands = [_write_worker_command(user_id, ticker) for ticker in tickers]
    env = dict(os.environ)
    processes = [subprocess.Popen(cmd, cwd=str(BACKEND_DIR), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for cmd in commands]
    for process in processes:
        stdout, stderr = process.communicate(timeout=30)
        assert process.returncode == 0, f"worker process failed: {stderr.decode('utf-8', errors='replace')}"

    recorded_tickers = {r["ticker"] for r in list_research_decisions(user_id)}
    assert recorded_tickers == set(tickers)  # every concurrent process's write survived - none lost
