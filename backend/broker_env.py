"""Which broker and environment a record belongs to.

Every NEW order record, closed trade and trade ticket is stamped with the
broker and environment ("sandbox" or "live") it was created under.
Decisions - reconciliation, entry freezes, duplicate-position checks,
ticket approval, performance - only ever use records from the CURRENT
environment, so a sandbox record is never reconciled against a live account,
never blocks a live entry, and never counts toward live performance (and
vice versa). Sandbox and live are separate books that must each match their
own broker records.

Records written before this stamping existed carry no environment. They are
treated as sandbox and marked `environment_inferred`: the live switch
(integrations/webull.is_live_trading_armed, commit 88c4b35) has never been
armed in production - the readiness report shows it off - so every earlier
record was created against the Webull sandbox. Stored records are never
rewritten to add the inference."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Tuple

SANDBOX = "sandbox"
LIVE = "live"
LEGACY_ENVIRONMENT = SANDBOX


def _live_armed() -> bool:
    try:
        from integrations import webull as webull_api
    except ImportError:  # pragma: no cover - package-relative import
        from .integrations import webull as webull_api  # type: ignore
    return bool(webull_api.is_live_trading_armed())


def current() -> Dict[str, str]:
    return {"broker": "webull", "environment": LIVE if _live_armed() else SANDBOX}


def stamp(record: Dict[str, Any]) -> Dict[str, Any]:
    """Adds broker/environment when absent (never overwrites). Returns record."""
    if not record.get("environment"):
        record.update(current())
    record.setdefault("broker", "webull")
    return record


def environment_of(record: Dict[str, Any]) -> Tuple[str, bool]:
    """(environment, inferred)."""
    value = str(record.get("environment") or "")
    return (value, False) if value else (LEGACY_ENVIRONMENT, True)


def in_current(record: Dict[str, Any], environment: str = "") -> bool:
    return environment_of(record)[0] == (environment or current()["environment"])


def only_current(records: Iterable[Dict[str, Any]], environment: str = "") -> List[Dict[str, Any]]:
    environment = environment or current()["environment"]
    return [r for r in records if isinstance(r, dict) and environment_of(r)[0] == environment]


def label(record: Dict[str, Any]) -> Dict[str, Any]:
    """Display fields for a record: environment and whether it was inferred."""
    environment, inferred = environment_of(record)
    return {"broker": record.get("broker") or "webull", "environment": environment, "environment_inferred": inferred}
