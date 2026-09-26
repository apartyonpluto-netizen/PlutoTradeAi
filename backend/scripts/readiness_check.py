"""Local readiness check: python backend/scripts/readiness_check.py

Checks what is visible from this machine's environment (secrets, live-armed state, dollar caps,
market-data probe). Heartbeats and per-user checks only exist on the running service - use the
Admin page's Readiness panel on Render for those. Exit code 1 if anything fails.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

import readiness  # noqa: E402
from integrations import alpaca_data  # noqa: E402

ICONS = {"pass": "PASS", "warn": "WARN", "fail": "FAIL", "info": "INFO", "skip": "skip"}


def main() -> int:
    probe = alpaca_data.probe_latest_trade
    report = readiness.build_report(data_probe=probe)
    for check in report["checks"]:
        print(f"[{ICONS[check['status']]}] {check['label']}: {check['detail']}")
    print()
    print(f"Live armed: {'YES' if report['live_armed'] else 'no'}   Sandbox ready: {report['sandbox_ready']}")
    print(report["note"])
    return 1 if report["fail_count"] else 0


if __name__ == "__main__":
    sys.exit(main())
