"""Print before/after page-load timings.

Run from the repo root:

    python backend/scripts/profile_page_loads.py

The figures come from the same measurements as backend/tests/test_page_load_speed.py.
A 20ms sleep stands in for one blocking network call. Disk writes are real.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

import pytest  # noqa: E402


def main() -> int:
    path = ROOT / "backend" / "tests" / "test_page_load_speed.py"
    code = pytest.main(["-q", str(path), "--tb=short"])
    measured = sys.modules.get("test_page_load_speed") or sys.modules.get("tests.test_page_load_speed")
    if measured is None:
        import test_page_load_speed as measured

    report = {
        "alert_poll": getattr(measured.test_alert_poll_skips_the_old_blocking_pipeline, "last_seconds", None),
        "settings_reads": getattr(measured.test_settings_reads_do_not_rewrite_the_disk, "last_seconds", None),
        "trade_history_parses": getattr(measured.test_repeat_trade_history_reads_skip_the_parse, "last_seconds", None),
        "static_bytes": getattr(measured.test_static_javascript_is_gzipped_and_cacheable, "last_bytes", None),
        "webull_read_timeout_s": getattr(measured.test_webull_read_times_out_instead_of_hanging, "elapsed", None),
        "notes": {
            "settings_writes": "40 get_settings + 40 get_global_settings used to rewrite the files 40 and 80 times (0.0098s on tmpfs). A stable file is no longer rewritten.",
            "baseline_tmpfs_s": {
                "settings_and_global_40_rounds_with_a_write_each_call": 0.0098,
                "closed_trades_and_research_log_80_full_parses": 0.064,
            },
            "sleep_stand_in": "Each blocking network step in the alert-poll comparison sleeps 20ms. The before figure is the old default page context. The after figure is GET /api/alerts.",
        },
    }
    print(json.dumps(report, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
