"""Confirm-to-enter alerts. The scan does not open a paper trade until Enter.

Each button is a signed, single-use token. It dies after PLUTO_CONFIRM_TTL_SECONDS
(default 10 minutes) or when the price has moved further than
PLUTO_CONFIRM_PRICE_DRIFT (default 0.5%) from the alert. Skip only logs.

Live auto-entry is unchanged: confirm mode stays off while live trading is
armed. An Enter click that does run while live is armed still has to pass
the go-live gate inside order placement. This module never arms live trading.

Quiet hours (PLUTO_ALERT_QUIET_HOURS, default 20:00-08:00 America/New_York)
and the daily cap (PLUTO_SETUP_ALERTS_PER_DAY, default 5) apply to setup
alerts, including when the account holds no position in the ticker.
"""

from __future__ import annotations

import base64
import contextlib
import fcntl
import hashlib
import hmac
import json
import os
import uuid
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("PLUTO_DATA_DIR", str(BASE_DIR / "data"))).resolve()
USED_FILE = DATA_DIR / "research" / "confirm_tokens_used.json"
SENT_FILE = DATA_DIR / "research" / "setup_alert_counts.json"


class TokenError(Exception):
    """The button cannot be honored. Nothing was sent to the broker."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _live_armed() -> bool:
    try:
        from integrations.webull import is_live_trading_armed
    except ImportError:  # pragma: no cover
        from .integrations.webull import is_live_trading_armed  # type: ignore
    return bool(is_live_trading_armed())


def confirm_enabled() -> bool:
    """Paper default is on. Live stays on the existing entry path."""
    if _live_armed():
        return False
    return _flag("PLUTO_CONFIRM_TO_ENTER", True)


def ttl_seconds() -> int:
    try:
        value = int(os.environ.get("PLUTO_CONFIRM_TTL_SECONDS", "600"))
    except ValueError:
        return 600
    return value if value > 0 else 600


def drift_limit() -> float:
    try:
        value = float(os.environ.get("PLUTO_CONFIRM_PRICE_DRIFT", "0.005"))
    except ValueError:
        return 0.005
    return value if value > 0 else 0.005


def daily_cap() -> int:
    try:
        value = int(os.environ.get("PLUTO_SETUP_ALERTS_PER_DAY", "5"))
    except ValueError:
        return 5
    return value if value > 0 else 5


def _secret() -> bytes:
    key = os.environ.get("FLASK_SECRET_KEY", "").strip() or os.environ.get("CREDENTIAL_ENCRYPTION_KEY", "").strip()
    if not key:
        raise TokenError("unconfigured", "Confirm links are not configured.")
    return key.encode("utf-8")


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


@contextlib.contextmanager
def _locked(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.touch(exist_ok=True)
    with open(lock_path, "r+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _load(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:6]}")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(tmp, path)


def in_quiet_hours(now: datetime, window: Optional[str] = None, tz_name: Optional[str] = None) -> bool:
    """Clock window in the alert timezone. Overnight windows (20:00-08:00) are supported."""
    window = window if window is not None else os.environ.get("PLUTO_ALERT_QUIET_HOURS", "20:00-08:00")
    tz_name = tz_name or os.environ.get("PLUTO_ALERT_TIMEZONE", "America/New_York")
    if not str(window).strip():
        return False
    try:
        start_text, end_text = str(window).split("-", 1)
        start = time.fromisoformat(start_text.strip())
        end = time.fromisoformat(end_text.strip())
        zone = ZoneInfo(tz_name)
    except (ValueError, KeyError):
        return False
    moment = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    local = moment.astimezone(zone).time()
    if start == end:
        return False
    if start < end:
        return start <= local < end
    return local >= start or local < end


def reserve_alert_slot(user_id: str, day: str) -> bool:
    """True when this alert is within the daily cap. The slot is consumed."""
    with _locked(SENT_FILE):
        counts = _load(SENT_FILE)
        per_user = dict(counts.get(user_id) or {})
        current = int(per_user.get(day) or 0)
        if current >= daily_cap():
            return False
        per_user[day] = current + 1
        counts[user_id] = per_user
        _save(SENT_FILE, counts)
    return True


def delivery(user_id: str, day: str, now: datetime) -> Dict[str, Any]:
    """Whether to send the phone buttons, or why the setup stays logged only."""
    if in_quiet_hours(now):
        return {
            "send": False,
            "skip_category": "quiet_hours",
            "ledger_decision": "skipped",
            "reason": "quiet hours — setup logged, no phone alert",
        }
    if not reserve_alert_slot(user_id, day):
        return {
            "send": False,
            "skip_category": "alert_cap",
            "ledger_decision": "skipped",
            "reason": f"daily setup-alert cap ({daily_cap()}) already used",
        }
    return {
        "send": True,
        "skip_category": "awaiting_confirm",
        "ledger_decision": "awaiting_confirm",
        "reason": "waiting for Enter or Skip",
    }


def reentry_tickers(orders: Any, today_key: str, trading_day_of) -> set:
    """Tickers that fully closed today, so a new confirm may be offered."""
    grouped: Dict[str, list] = {}
    for order in orders or []:
        if not isinstance(order, dict):
            continue
        try:
            day = trading_day_of(order)
        except Exception:
            day = ""
        if day != today_key:
            continue
        ticker = str(order.get("ticker") or "").upper()
        if ticker:
            grouped.setdefault(ticker, []).append(order)
    eligible = set()
    for ticker, rows in grouped.items():
        if rows and all(
            str(row.get("lifecycle_state") or "") == "closed" or str(row.get("status") or "") == "closed"
            for row in rows
        ):
            eligible.add(ticker)
    return eligible


def setup_message(
    *,
    ticker: str,
    why: str,
    confidence: Any,
    regime: str,
    entry: float,
    stop: float,
    target: float,
    quantity: float,
    direction: str,
    reentry: bool = False,
) -> str:
    sign = -1.0 if direction == "short" else 1.0
    risk = abs(entry - stop) * quantity
    reward = max(0.0, sign * (target - entry) * quantity)
    line = (
        f"PlutoTrade spotted a setup on {ticker}: {why}. "
        f"Confidence {confidence}%, regime {regime}. "
        f"Entry ~${entry:.2f}, stop ${stop:.2f}, target ${target:.2f}, "
        f"risk ${risk:.0f} to make ${reward:.0f}."
    )
    if reentry:
        return "Re-entry. " + line
    return line


def issue_token(payload: Dict[str, Any], *, now: datetime) -> str:
    body = {
        "v": 1,
        "act": payload["act"],
        "uid": payload["uid"],
        "sid": payload.get("sid"),
        "exp": int(now.timestamp()) + ttl_seconds(),
        "nonce": uuid.uuid4().hex,
        "px": payload.get("px"),
        "sym": payload.get("sym"),
        "qty": payload.get("qty"),
        "lim": payload.get("lim"),
        "stop": payload.get("stop"),
        "tgt": payload.get("tgt"),
        "dir": payload.get("dir"),
        "day": payload.get("day"),
        "acct": payload.get("acct"),
        "strat": payload.get("strat"),
        "conf": payload.get("conf"),
    }
    raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
    sig = hmac.new(_secret(), raw, hashlib.sha256).hexdigest()
    return _b64(raw) + "." + sig


def _mark_used(nonce: str) -> bool:
    """False when this nonce was already used."""
    with _locked(USED_FILE):
        used = _load(USED_FILE)
        nonces = dict(used.get("nonces") or {})
        if nonce in nonces:
            return False
        nonces[nonce] = datetime.now(timezone.utc).isoformat()
        if len(nonces) > 5000:
            for key in list(nonces)[: len(nonces) - 5000]:
                nonces.pop(key, None)
        used["nonces"] = nonces
        _save(USED_FILE, used)
    return True


def redeem(token: str, *, now: datetime, current_price: Optional[float] = None, price_for=None) -> Dict[str, Any]:
    """Validate one button. A second use is a replay. Expiry and a moved price both refuse."""
    try:
        encoded, sig = str(token).split(".", 1)
        raw = _b64decode(encoded)
        expected = hmac.new(_secret(), raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig):
            raise TokenError("bad_signature", "This confirm link is not valid.")
        body = json.loads(raw.decode("utf-8"))
    except TokenError:
        raise
    except Exception as error:
        raise TokenError("bad_signature", "This confirm link is not valid.") from error
    nonce = str(body.get("nonce") or "")
    if not nonce or not _mark_used(nonce):
        raise TokenError("replay", "This confirm link was already used.")
    try:
        exp = int(body.get("exp") or 0)
    except (TypeError, ValueError):
        exp = 0
    if int(now.timestamp()) > exp:
        raise TokenError("expired", "This confirm link has expired.")
    if price_for is not None and current_price is None:
        try:
            current_price = price_for(body.get("sym"))
        except Exception:
            current_price = None
        if str(body.get("act")) == "enter" and current_price is None:
            raise TokenError("price_moved", "The price could not be confirmed, so the link was cancelled.")
    alert_price = body.get("px")
    if current_price is not None and alert_price not in (None, ""):
        try:
            base = float(alert_price)
            price = float(current_price)
        except (TypeError, ValueError):
            base = 0.0
            price = 0.0
        if base > 0 and abs(price - base) / base > drift_limit():
            raise TokenError("price_moved", "The price has moved away from this setup, so the link was cancelled.")
    return body


def ntfy_actions(base_url: str, enter_token: str, skip_token: str) -> str:
    """ntfy Actions header: two http buttons."""
    root = base_url.rstrip("/")
    enter = f"{root}/api/confirm-entry/{enter_token}"
    skip = f"{root}/api/confirm-entry/{skip_token}"
    return f"http, Enter, {enter}, method=POST; http, Skip, {skip}, method=POST"


def send_setup_alert(message: str, actions: Optional[str] = None) -> bool:
    try:
        import phone_alerts
    except ImportError:  # pragma: no cover
        from . import phone_alerts  # type: ignore
    return phone_alerts.notify("setup", "Setup", message, actions=actions)


def public_base_url() -> str:
    return os.environ.get("PLUTO_PUBLIC_BASE_URL", "").strip()
