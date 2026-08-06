from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, Optional

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("PLUTO_DATA_DIR", str(BASE_DIR / "data"))).resolve()
USER_DATA_ROOT = DATA_DIR / "users"

DEFAULT_VIRTUAL_STARTING_BALANCE = 2000.0


def _credentials_file(user_id: str) -> Path:
    if not user_id:
        raise ValueError("user_id is required.")
    path = USER_DATA_ROOT / user_id
    path.mkdir(parents=True, exist_ok=True)
    return path / "webull_credentials.json"


def _read(user_id: str) -> Dict[str, object]:
    creds_file = _credentials_file(user_id)
    if not creds_file.exists():
        return {}
    try:
        data = json.loads(creds_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _write(user_id: str, data: Dict[str, object]) -> None:
    _credentials_file(user_id).write_text(json.dumps(data, indent=2), encoding="utf-8")


def get_webull_credentials(user_id: str) -> Dict[str, str]:
    """Each user brings their own Webull OpenAPI app key/secret - there is no
    shared/global fallback, so one user can never see or trade on another
    user's Webull sandbox account."""
    data = _read(user_id)
    return {"app_key": str(data.get("app_key", "")), "app_secret": str(data.get("app_secret", ""))}


def is_webull_configured(user_id: str) -> bool:
    creds = get_webull_credentials(user_id)
    return bool(creds["app_key"] and creds["app_secret"])


def set_webull_credentials(user_id: str, app_key: str, app_secret: str) -> None:
    app_key = (app_key or "").strip()
    app_secret = (app_secret or "").strip()
    if not app_key or not app_secret:
        raise ValueError("Both App Key and App Secret are required.")
    data = _read(user_id)
    # Swapping to a different app key points at a different Webull sandbox
    # account entirely (each application gets its own paper account) - the
    # previously-recorded seed balance belongs to the old account and would
    # silently corrupt the virtual balance math for the new one, so drop it
    # and let it get recaptured fresh on the next successful sync.
    if data.get("app_key") != app_key:
        data.pop("seed_balance", None)
    data["app_key"] = app_key
    data["app_secret"] = app_secret
    _write(user_id, data)


def clear_webull_credentials(user_id: str) -> None:
    creds_file = _credentials_file(user_id)
    if creds_file.exists():
        creds_file.write_text(json.dumps({"app_key": "", "app_secret": ""}, indent=2), encoding="utf-8")


def record_seed_balance_if_unset(user_id: str, net_liquidation_value: float) -> None:
    """Captures the sandbox account's starting net liquidation value the
    first time a sync succeeds after connecting - this is what lets the
    virtual balance track real all-time P&L (current - seed) without needing
    a separate realized-P&L ledger. Only ever writes once per app key (see
    set_webull_credentials for the reset-on-key-change behavior)."""
    data = _read(user_id)
    if "seed_balance" in data:
        return
    data["seed_balance"] = float(net_liquidation_value)
    _write(user_id, data)


def get_virtual_starting_balance(user_id: str) -> float:
    data = _read(user_id)
    try:
        return float(data.get("virtual_starting_balance", DEFAULT_VIRTUAL_STARTING_BALANCE))
    except (TypeError, ValueError):
        return DEFAULT_VIRTUAL_STARTING_BALANCE


def set_virtual_starting_balance(user_id: str, amount: float) -> None:
    amount = float(amount)
    if amount <= 0:
        raise ValueError("Virtual starting balance must be greater than zero.")
    data = _read(user_id)
    data["virtual_starting_balance"] = amount
    _write(user_id, data)


def get_virtual_net_account_value(user_id: str, real_net_liquidation_value: float) -> Optional[float]:
    """Returns what the account "should" be worth if it had started at the
    user's chosen virtual balance instead of Webull's real sandbox seed -
    virtual_starting_balance + all-time P&L, where all-time P&L is just
    (current real value - recorded seed). Returns None if no seed has been
    recorded yet (e.g. before the first successful sync), so callers can
    fall back to showing the real value instead of a wrong one."""
    data = _read(user_id)
    seed_balance = data.get("seed_balance")
    if seed_balance is None:
        return None
    all_time_pnl = float(real_net_liquidation_value) - float(seed_balance)
    return get_virtual_starting_balance(user_id) + all_time_pnl
