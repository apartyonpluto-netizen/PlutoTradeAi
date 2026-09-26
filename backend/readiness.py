"""One report answering "is this safe to run today?" - sandbox and live.

Pure function of its inputs so it is testable with no network or Flask.
The admin endpoint (/api/admin/readiness) feeds it real health data; the CLI
(scripts/readiness_check.py) feeds it only what it can see locally.

Statuses: "pass", "warn" (worth a look), "fail" (fix before relying on it),
"info" (context only), "skip" (could not be checked from here).
This report never arms live trading and never places an order.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Dict, List, Optional

try:
    from . import live_limits
    from .integrations import webull as webull_api
except ImportError:  # flat-import layout (gunicorn --chdir backend)
    import live_limits
    from integrations import webull as webull_api

REQUIRED_SECRETS = ("FLASK_SECRET_KEY", "CREDENTIAL_ENCRYPTION_KEY", "CRON_SECRET", "MONITOR_WORKER_SECRET")


def _check(check_id: str, label: str, status: str, detail: str, needed_for_live: bool = False) -> Dict[str, Any]:
    return {"id": check_id, "label": label, "status": status, "detail": detail, "needed_for_live": needed_for_live}


def _health_check(check_id: str, label: str, health: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if health is None:
        return _check(check_id, label, "skip", "Only checkable on the running web service.", True)
    if health.get("healthy"):
        return _check(check_id, label, "pass", "Running on schedule.", True)
    return _check(check_id, label, "fail", str(health.get("reason") or "Unhealthy."), True)


def build_report(
    *,
    fast_monitor: Optional[Dict[str, Any]] = None,
    full_scan: Optional[Dict[str, Any]] = None,
    continuous_monitor: Optional[Dict[str, Any]] = None,
    data_probe: Optional[Callable[[], Optional[float]]] = None,
    emergency_stop_enabled: Optional[bool] = None,
    webull_connected: Optional[bool] = None,
    sentry_active: Optional[bool] = None,
) -> Dict[str, Any]:
    armed = webull_api.is_live_trading_armed()
    checks: List[Dict[str, Any]] = []

    if armed:
        checks.append(_check("mode", "Trading mode", "warn", "LIVE ARMED - orders go to the real Webull account with real money."))
    else:
        checks.append(_check("mode", "Trading mode", "pass", "Sandbox. Live trading is not armed, no real money at risk."))

    caps = live_limits.get_caps()
    if live_limits.caps_configured():
        usage = live_limits.today_usage()
        checks.append(_check(
            "live_caps", "Live dollar caps", "pass",
            f"${caps['max_order_usd']:,.2f} per order, ${caps['max_daily_usd']:,.2f} per day "
            f"(${usage['spent_usd']:,.2f} used today).", True))
    else:
        checks.append(_check(
            "live_caps", "Live dollar caps", "fail" if armed else "info",
            f"Set {live_limits.MAX_ORDER_VAR} and {live_limits.MAX_DAILY_VAR}. Until both are set, live mode "
            "refuses every opening order." if armed else
            f"Not set. Live mode will refuse to open positions until {live_limits.MAX_ORDER_VAR} and "
            f"{live_limits.MAX_DAILY_VAR} are both set.", True))

    missing = [name for name in REQUIRED_SECRETS if not os.environ.get(name, "").strip()]
    checks.append(_check(
        "secrets", "Required secrets", "fail" if missing else "pass",
        f"Missing: {', '.join(missing)}." if missing else "All present.", True))

    if data_probe is None:
        checks.append(_check("market_data", "Market data (Alpaca)", "skip", "Not probed from here.", True))
    else:
        try:
            price = data_probe()
        except Exception as error:  # noqa: BLE001 - a failing probe is the finding
            checks.append(_check("market_data", "Market data (Alpaca)", "fail", f"Request failed: {str(error)[:160]}", True))
        else:
            if price:
                checks.append(_check("market_data", "Market data (Alpaca)", "pass", f"Live quote received (SPY ${price:,.2f}).", True))
            else:
                checks.append(_check("market_data", "Market data (Alpaca)", "warn", "Reachable but returned no price.", True))

    checks.append(_health_check("fast_monitor", "Fast order monitor", fast_monitor))
    checks.append(_health_check("full_scan", "Full autonomous scan", full_scan))
    checks.append(_health_check("continuous_monitor", "Continuous monitor worker", continuous_monitor))

    if webull_connected is None:
        checks.append(_check("webull", "Webull account connected", "skip", "Checked per signed-in user.", True))
    else:
        checks.append(_check(
            "webull", "Webull account connected", "pass" if webull_connected else "fail",
            "Credentials on file." if webull_connected else "No Webull app key/secret saved for this user.", True))

    if emergency_stop_enabled is None:
        checks.append(_check("emergency_stop", "Emergency stop", "skip", "Checked per signed-in user.", True))
    elif emergency_stop_enabled:
        checks.append(_check("emergency_stop", "Emergency stop", "warn", "ON - the agent will not trade until reset.", True))
    else:
        checks.append(_check("emergency_stop", "Emergency stop", "pass", "Off (ready to use if needed).", True))

    if sentry_active is None:
        checks.append(_check("sentry", "Error reporting (Sentry)", "skip", "Not checked here."))
    else:
        checks.append(_check(
            "sentry", "Error reporting (Sentry)", "pass" if sentry_active else "warn",
            "Reporting to Sentry." if sentry_active else "SENTRY_DSN not set - errors are only in Render logs."))

    legacy = os.environ.get("LIVE_TRADING_ENABLED", "").strip().lower()
    if legacy in {"1", "true", "yes", "on"}:
        checks.append(_check(
            "legacy_flag", "Old LIVE_TRADING_ENABLED flag", "warn",
            "Set to true, but it does nothing - only the two PLUTO_* variables arm live trading. Remove it to avoid confusion."))

    fails = [c for c in checks if c["status"] == "fail"]
    warns = [c for c in checks if c["status"] == "warn"]
    sandbox_ready = not [c for c in fails if c["id"] != "live_caps"]
    live_blockers = [c for c in checks if c["needed_for_live"] and c["status"] in {"fail", "skip"}]
    return {
        "live_armed": armed,
        "sandbox_ready": sandbox_ready,
        "live_ready": not live_blockers and not [c for c in warns if c["needed_for_live"]],
        "live_blockers": [c["id"] for c in live_blockers],
        "fail_count": len(fails),
        "warn_count": len(warns),
        "checks": checks,
        "note": "This report never arms live trading. Arming is two environment variables you set yourself.",
    }
