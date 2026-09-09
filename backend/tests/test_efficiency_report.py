from __future__ import annotations

from datetime import datetime, timedelta, timezone

import auth
import app as pluto_app
from autonomy.efficiency_report import build_efficiency_report
from autonomy.scan_run_log import record_scan_run

"""Trading-efficiency funnel report - the aggregation layer over the
skip_categories/option_stats now tallied onto every "processed" scan-run
record (see app.py's _summarize_scan_result_for_run_log and
_run_autonomous_trade_scan_locked). Writes real records through
record_scan_run and reads them back through build_efficiency_report,
matching this codebase's existing storage-layer test pattern (see
tests/test_performance_report.py, tests/test_orphan_entry_discovery.py)
rather than mocking the read path."""

NOW = datetime(2026, 9, 9, 15, 0, 0, tzinfo=timezone.utc)


def _processed_record(*, actual_start_time, candidates_found=0, candidates_qualifying=0,
                       placed=0, failed=0, unknown_submission_state=0,
                       skip_categories=None, option_attempted=0, option_contract_found=0):
    return {
        "trigger_source": "cron-trigger",
        "account_mode": "AUTONOMOUS",
        "actual_start_time": actual_start_time.isoformat(),
        "status": "processed",
        "error": None,
        "candidates_found": candidates_found,
        "candidates_qualifying": candidates_qualifying,
        "orders_attempted": placed + failed + unknown_submission_state,
        "orders_outcomes": {"placed": placed, "failed": failed, "unknown_submission_state": unknown_submission_state},
        "reason": "test record",
        "skip_categories": skip_categories or {},
        "option_stats": {"attempted": option_attempted, "contract_found": option_contract_found},
    }


def _skipped_record(actual_start_time):
    # A non-AUTONOMOUS-mode tick - correctly carries no funnel data at all
    # (see api_autonomy_cron_trigger's own record_scan_run calls).
    return {
        "trigger_source": "cron-trigger",
        "account_mode": "OFF",
        "actual_start_time": actual_start_time.isoformat(),
        "status": "skipped",
        "reason": "autonomy mode is OFF",
        "candidates_found": None,
        "candidates_qualifying": None,
        "orders_attempted": None,
        "orders_outcomes": None,
        "error": None,
    }


def _failed_record(actual_start_time):
    return {
        "trigger_source": "cron-trigger",
        "account_mode": "AUTONOMOUS",
        "actual_start_time": actual_start_time.isoformat(),
        "status": "failed",
        "reason": "the autonomous scan raised an unhandled error",
        "candidates_found": None,
        "candidates_qualifying": None,
        "orders_attempted": None,
        "orders_outcomes": None,
        "error": "boom",
    }


def test_empty_window_returns_all_zeros_without_crashing(user_id):
    report = build_efficiency_report(user_id, days=7, now=NOW)
    assert report["ticks_processed"] == 0
    assert report["candidates_found"] == 0
    assert report["candidates_qualifying"] == 0
    assert report["placed"] == 0
    assert report["conversion_rate_percent"] is None
    assert report["skip_categories"] == {}
    assert report["top_skip_categories"] == []
    assert report["option_stats"] == {"attempted": 0, "contract_found": 0, "contract_found_rate_percent": None}


def test_a_single_processed_tick_is_summed_correctly(user_id):
    record_scan_run(user_id, _processed_record(
        actual_start_time=NOW - timedelta(hours=1),
        candidates_found=5, candidates_qualifying=3, placed=1, failed=1,
        skip_categories={"llm_veto": 1}, option_attempted=2, option_contract_found=1,
    ))

    report = build_efficiency_report(user_id, days=7, now=NOW)
    assert report["ticks_processed"] == 1
    assert report["candidates_found"] == 5
    assert report["candidates_qualifying"] == 3
    assert report["placed"] == 1
    assert report["failed"] == 1
    assert report["skip_categories"] == {"llm_veto": 1}
    assert report["option_stats"] == {"attempted": 2, "contract_found": 1, "contract_found_rate_percent": 50.0}
    # 1 placed / 3 qualifying
    assert report["conversion_rate_percent"] == 33.3


def test_skip_categories_and_option_stats_are_merged_across_multiple_ticks(user_id):
    record_scan_run(user_id, _processed_record(
        actual_start_time=NOW - timedelta(hours=3),
        candidates_found=2, candidates_qualifying=2, placed=1,
        skip_categories={"sizing_too_small": 1}, option_attempted=1, option_contract_found=0,
    ))
    record_scan_run(user_id, _processed_record(
        actual_start_time=NOW - timedelta(hours=1),
        candidates_found=3, candidates_qualifying=1, placed=0,
        skip_categories={"sizing_too_small": 2, "llm_veto": 1}, option_attempted=1, option_contract_found=1,
    ))

    report = build_efficiency_report(user_id, days=7, now=NOW)
    assert report["ticks_processed"] == 2
    assert report["candidates_found"] == 5
    assert report["candidates_qualifying"] == 3
    assert report["placed"] == 1
    assert report["skip_categories"] == {"sizing_too_small": 3, "llm_veto": 1}
    assert report["option_stats"]["attempted"] == 2
    assert report["option_stats"]["contract_found"] == 1


def test_top_skip_categories_is_sorted_by_count_descending(user_id):
    record_scan_run(user_id, _processed_record(
        actual_start_time=NOW - timedelta(hours=1),
        candidates_qualifying=6,
        skip_categories={"llm_veto": 5, "sizing_too_small": 1, "price_drift": 3},
    ))

    report = build_efficiency_report(user_id, days=7, now=NOW)
    assert report["top_skip_categories"] == [
        {"category": "llm_veto", "count": 5},
        {"category": "price_drift", "count": 3},
        {"category": "sizing_too_small", "count": 1},
    ]


def test_a_tick_outside_the_window_is_excluded(user_id):
    record_scan_run(user_id, _processed_record(
        actual_start_time=NOW - timedelta(days=10),
        candidates_found=99, candidates_qualifying=99, placed=99,
    ))
    record_scan_run(user_id, _processed_record(
        actual_start_time=NOW - timedelta(hours=1),
        candidates_found=1, candidates_qualifying=1, placed=1,
    ))

    report = build_efficiency_report(user_id, days=7, now=NOW)
    assert report["ticks_processed"] == 1
    assert report["candidates_found"] == 1
    assert report["placed"] == 1


def test_skipped_and_failed_administrative_records_are_excluded_not_counted_as_zero(user_id):
    """A non-AUTONOMOUS-mode tick or a crashed tick correctly has no
    funnel data - it must be excluded from the window entirely, not
    silently folded in as "0 candidates found" (which would understate a
    window's true totals whenever any tick wasn't a real scan)."""
    record_scan_run(user_id, _skipped_record(NOW - timedelta(hours=2)))
    record_scan_run(user_id, _failed_record(NOW - timedelta(hours=1)))
    record_scan_run(user_id, _processed_record(
        actual_start_time=NOW - timedelta(minutes=30),
        candidates_found=4, candidates_qualifying=2, placed=1,
    ))

    report = build_efficiency_report(user_id, days=7, now=NOW)
    assert report["ticks_processed"] == 1
    assert report["candidates_found"] == 4
    assert report["candidates_qualifying"] == 2
    assert report["placed"] == 1


def test_a_pre_schema_bump_record_missing_the_new_fields_contributes_nothing_but_does_not_crash(user_id):
    """A record written before skip_category/option_stats existed
    (schema_version 1) simply has neither key - additive, per
    SCAN_RUN_LOG_SCHEMA_VERSION's own convention."""
    legacy_record = {
        "trigger_source": "cron-trigger",
        "account_mode": "AUTONOMOUS",
        "actual_start_time": (NOW - timedelta(hours=1)).isoformat(),
        "status": "processed",
        "error": None,
        "candidates_found": 2,
        "candidates_qualifying": 1,
        "orders_attempted": 1,
        "orders_outcomes": {"placed": 1, "failed": 0, "unknown_submission_state": 0},
        "reason": "legacy record, pre-schema-version-2",
    }
    record_scan_run(user_id, legacy_record)

    report = build_efficiency_report(user_id, days=7, now=NOW)
    assert report["ticks_processed"] == 1
    assert report["candidates_found"] == 2
    assert report["placed"] == 1
    assert report["skip_categories"] == {}
    assert report["option_stats"] == {"attempted": 0, "contract_found": 0, "contract_found_rate_percent": None}


def test_window_days_parameter_is_respected(user_id):
    record_scan_run(user_id, _processed_record(
        actual_start_time=NOW - timedelta(days=2),
        candidates_found=1, candidates_qualifying=1, placed=1,
    ))

    assert build_efficiency_report(user_id, days=1, now=NOW)["ticks_processed"] == 0
    assert build_efficiency_report(user_id, days=3, now=NOW)["ticks_processed"] == 1


# --- authenticated /api/autonomy/efficiency-report endpoint -----------------------------


def _registered_user(username_suffix: str) -> str:
    """A real, approved, logged-in-able account - mirrors
    test_cron_trigger_mode_independence.py's own helper; the
    before_request auth gate requires get_user_by_id to resolve and the
    account to be approved, which a bare fixture user_id string alone
    does not satisfy."""
    user = auth.register_user(f"efficiencyreport-{username_suffix}", "TestPassword123!")
    auth.approve_user(user["id"])
    return user["id"]


def test_efficiency_report_endpoint_returns_only_this_users_own_data(user_id, other_user_id):
    registered_user_id = _registered_user(user_id[:8])
    record_scan_run(registered_user_id, {
        "status": "processed", "actual_start_time": datetime.now(timezone.utc).isoformat(),
        "candidates_found": 5, "candidates_qualifying": 2,
        "orders_outcomes": {"placed": 1, "failed": 0, "unknown_submission_state": 0},
    })
    record_scan_run(other_user_id, {
        "status": "processed", "actual_start_time": datetime.now(timezone.utc).isoformat(),
        "candidates_found": 99, "candidates_qualifying": 99,
        "orders_outcomes": {"placed": 99, "failed": 0, "unknown_submission_state": 0},
    })

    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = registered_user_id
        response = client.get("/api/autonomy/efficiency-report")

    assert response.status_code == 200
    report = response.get_json()["data"]
    assert report["candidates_found"] == 5
    assert report["placed"] == 1


def test_efficiency_report_endpoint_requires_auth():
    with pluto_app.app.test_client() as client:
        response = client.get("/api/autonomy/efficiency-report")
    assert response.status_code in (401, 302, 403)


def test_efficiency_report_endpoint_respects_the_days_query_param(user_id):
    registered_user_id = _registered_user(user_id[:8] + "d")
    record_scan_run(registered_user_id, {
        "status": "processed", "actual_start_time": (datetime.now(timezone.utc) - timedelta(days=10)).isoformat(),
        "candidates_found": 1, "candidates_qualifying": 1,
        "orders_outcomes": {"placed": 1, "failed": 0, "unknown_submission_state": 0},
    })

    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = registered_user_id
        response = client.get("/api/autonomy/efficiency-report?days=1")

    assert response.status_code == 200
    assert response.get_json()["data"]["ticks_processed"] == 0
