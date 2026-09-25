"""Optional Sentry error reporting.

Off unless SENTRY_DSN is set, so local runs and the test suite send nothing.
Everything that could identify an account or unlock one is stripped before an
event leaves the process: broker keys, tokens, cookies, the cron secret,
account numbers, request bodies, and any user identity.

Env vars:
    SENTRY_DSN                 enables reporting (leave unset to disable)
    SENTRY_ENVIRONMENT         default "production"
    SENTRY_TRACES_SAMPLE_RATE  default 0 (errors only, no performance data)
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, Optional

logger = logging.getLogger("observability")

_SENSITIVE_KEY = re.compile(
    r"(secret|token|password|passwd|authorization|cookie|api[-_]?key|app[-_]?key|"
    r"credential|signature|account[-_]?(number|id)|encryption|session|dsn)",
    re.IGNORECASE,
)
_REDACTED = "[redacted]"
_initialised = False


def _scrub(value: Any, depth: int = 0) -> Any:
    """Recursively redacts values whose key looks sensitive."""
    if depth > 8:
        return value
    if isinstance(value, dict):
        return {
            k: (_REDACTED if isinstance(k, str) and _SENSITIVE_KEY.search(k) else _scrub(v, depth + 1))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_scrub(v, depth + 1) for v in value]
    return value


def scrub_event(event: Dict[str, Any], hint: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """before_send hook: never let credentials, request bodies or user identity through."""
    request = event.get("request")
    if isinstance(request, dict):
        for field in ("data", "cookies", "query_string"):
            request.pop(field, None)
        if isinstance(request.get("headers"), dict):
            request["headers"] = _scrub(request["headers"])
        if isinstance(request.get("env"), dict):
            request["env"] = {}
    event.pop("user", None)
    for section in ("extra", "contexts", "tags", "breadcrumbs"):
        if section in event:
            event[section] = _scrub(event[section])
    return event


def init_sentry(service: str) -> bool:
    """Starts Sentry for `service` ("web" or "cron"). Returns True if it is active."""
    global _initialised
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn or _initialised:
        return _initialised
    try:
        import sentry_sdk
    except ImportError:
        logger.warning("SENTRY_DSN is set but sentry-sdk is not installed; error reporting is off")
        return False
    try:
        rate = float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0") or 0)
    except ValueError:
        rate = 0.0
    sentry_sdk.init(
        dsn=dsn,
        environment=os.environ.get("SENTRY_ENVIRONMENT", "production"),
        release=os.environ.get("RENDER_GIT_COMMIT") or None,
        send_default_pii=False,
        traces_sample_rate=rate,
        max_request_body_size="never",
        include_local_variables=False,
        before_send=scrub_event,
    )
    sentry_sdk.set_tag("service", service)
    _initialised = True
    return True


def cron_checkin(slug: str, schedule: str, status: str, check_in_id: Optional[str] = None) -> Optional[str]:
    """Reports a cron run to Sentry Cron Monitoring ("in_progress", "ok" or "error").

    Sentry creates the monitor from `schedule` on first check-in and alerts on
    a missed or failed run. Returns the check-in id to pass to the closing call.
    """
    if not _initialised:
        return None
    try:
        from sentry_sdk.crons import capture_checkin

        return capture_checkin(
            monitor_slug=slug,
            check_in_id=check_in_id,
            status=status,
            monitor_config={"schedule": {"type": "crontab", "value": schedule}, "timezone": "UTC", "checkin_margin": 5, "max_runtime": 5},
        )
    except Exception:  # monitoring must never break the job it watches
        logger.debug("sentry cron check-in failed", exc_info=True)
        return None
