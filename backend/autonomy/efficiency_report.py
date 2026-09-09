from __future__ import annotations

"""Trading-efficiency funnel report (2026-09-09) - the aggregation layer
for "the agent is trading more efficiently" turning into a number that
goes up, not a feeling. Sums the per-tick skip_categories/option_stats
tallies _summarize_scan_result_for_run_log (app.py) now attaches to every
persisted "processed" scan-run record (scan_run_log.py) across a rolling
window into one found -> qualifying -> placed funnel, a ranked skip-
reason breakdown, and options-path attempt/found stats.

Same "reporting only, never feeds back into the live scan" boundary as
performance_report.py/daily_digest.py - nothing here influences
_run_autonomous_trade_scan_locked or any other live decision path."""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from .scan_run_log import list_scan_runs

DEFAULT_WINDOW_DAYS = 7

# How many individual skip categories to surface by rank - a long tail of
# one-off categories would just be noise; the point of this report is
# "what are the few things actually costing conversions right now."
TOP_SKIP_CATEGORIES_LIMIT = 10


def _parse_timestamp(raw: object) -> Optional[datetime]:
    # Duplicated from daily_digest.py's own identical helper rather than
    # imported - matches this codebase's existing convention (see
    # scan_run_log.py's own redact_secret_values docstring) of keeping
    # each autonomy/*.py reporting module's small helpers self-contained
    # rather than cross-importing between sibling reporting modules.
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def _within_window(raw_timestamp: object, since: datetime) -> bool:
    parsed = _parse_timestamp(raw_timestamp)
    return parsed is not None and parsed >= since


def build_efficiency_report(user_id: str, days: int = DEFAULT_WINDOW_DAYS, now: Optional[datetime] = None) -> Dict[str, Any]:
    """now is accepted (not just datetime.now() internally) purely for
    deterministic tests - mirrors build_daily_digest's own signature."""
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=days)

    scan_runs = list_scan_runs(user_id)
    # Only "processed" ticks actually ran the opportunity scan/candidate
    # loop - "skipped" (wrong mode, no Webull configured, a concurrent-run
    # collision) and "failed" administrative records correctly carry no
    # funnel data of their own (see api_autonomy_cron_trigger's own
    # record_scan_run calls in app.py, which explicitly set
    # candidates_found/candidates_qualifying/orders_outcomes to None for
    # those statuses) and must never be silently counted as "0 candidates
    # found this tick" - that would understate the true found/qualifying
    # totals for a window that included any non-AUTONOMOUS-mode or failed
    # ticks.
    processed_runs = [
        run for run in scan_runs
        if run.get("status") == "processed" and _within_window(run.get("actual_start_time"), since)
    ]

    candidates_found = 0
    candidates_qualifying = 0
    placed = 0
    failed = 0
    unknown_submission_state = 0
    skip_categories: Dict[str, int] = {}
    option_attempted = 0
    option_contract_found = 0

    for run in processed_runs:
        candidates_found += int(run.get("candidates_found") or 0)
        candidates_qualifying += int(run.get("candidates_qualifying") or 0)
        outcomes = run.get("orders_outcomes") or {}
        placed += int(outcomes.get("placed") or 0)
        failed += int(outcomes.get("failed") or 0)
        unknown_submission_state += int(outcomes.get("unknown_submission_state") or 0)
        # A record written before skip_categories/option_stats existed
        # (pre-schema-version-2) simply has neither key - contributes
        # nothing here, not an error (see SCAN_RUN_LOG_SCHEMA_VERSION's
        # own "additive fields" convention in scan_run_log.py).
        for category, count in (run.get("skip_categories") or {}).items():
            skip_categories[category] = skip_categories.get(category, 0) + int(count or 0)
        option_stats = run.get("option_stats") or {}
        option_attempted += int(option_stats.get("attempted") or 0)
        option_contract_found += int(option_stats.get("contract_found") or 0)

    top_skip_categories: List[Dict[str, Any]] = [
        {"category": category, "count": count}
        for category, count in sorted(skip_categories.items(), key=lambda item: item[1], reverse=True)
    ][:TOP_SKIP_CATEGORIES_LIMIT]

    return {
        "window_days": days,
        "ticks_processed": len(processed_runs),
        "candidates_found": candidates_found,
        "candidates_qualifying": candidates_qualifying,
        "placed": placed,
        "failed": failed,
        "unknown_submission_state": unknown_submission_state,
        # placed / qualifying, not placed / found - a candidate that never
        # even qualified (below the confidence floor, or a non-CALL/PUT
        # recommendation) was never a real trade opportunity to begin
        # with, so counting it against conversion would make the rate
        # look artificially low for reasons that have nothing to do with
        # execution efficiency.
        "conversion_rate_percent": round(placed / candidates_qualifying * 100, 1) if candidates_qualifying else None,
        "skip_categories": skip_categories,
        "top_skip_categories": top_skip_categories,
        "option_stats": {
            "attempted": option_attempted,
            "contract_found": option_contract_found,
            "contract_found_rate_percent": (
                round(option_contract_found / option_attempted * 100, 1) if option_attempted else None
            ),
        },
    }
