"""Cache, SEC fair-access transport, and the shape of a missing vote.

SEC asks callers of EDGAR to send a descriptive User-Agent that includes
a contact email, and to stay under 10 requests per second. This module
sends that header and spaces requests at 5 per second. It does not call
EDGAR at all when the contact email is missing from config.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

# Stay under the published SEC fair-access ceiling of 10 requests/second.
SEC_MIN_INTERVAL_SECONDS = 0.2
SEC_DOWN_SECONDS = 45
HTTP_TIMEOUT = (2.5, 3.5)
_MISS = object()

_memory: Dict[str, Dict[str, Any]] = {}
_memory_lock = threading.Lock()
_sec_down_until = 0.0
_sec_down_lock = threading.Lock()
_limiter_lock = threading.Lock()
_limiter_next = 0.0
_clock: Callable[[], float] = time.monotonic
_sleeper: Callable[[float], None] = time.sleep


def data_dir() -> Path:
    base = Path(__file__).resolve().parents[2]
    return Path(os.environ.get("PLUTO_DATA_DIR", str(base / "data"))).resolve()


def cache_dir() -> Path:
    path = data_dir() / "research" / "market_intel_cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def contact_email() -> str:
    """Email from config for the SEC User-Agent. Empty means do not call EDGAR."""
    for key in ("PLUTO_SEC_CONTACT_EMAIL", "SEC_CONTACT_EMAIL"):
        value = os.environ.get(key, "").strip()
        if _valid_contact(value):
            return value
    return ""


def sec_headers(email: str) -> Dict[str, str]:
    return {
        "User-Agent": f"PlutoTrade research {email}",
        "Accept-Encoding": "gzip, deflate",
        "Accept": "application/json,application/xml,text/xml,*/*",
    }


def sec_is_down(now: Optional[float] = None) -> bool:
    moment = _clock() if now is None else now
    with _sec_down_lock:
        return moment < _sec_down_until


def mark_sec_down(seconds: float = SEC_DOWN_SECONDS) -> None:
    global _sec_down_until
    with _sec_down_lock:
        _sec_down_until = max(_sec_down_until, _clock() + seconds)


def acquire_sec_slot() -> None:
    """Block until this process may send another SEC request."""
    global _limiter_next
    with _limiter_lock:
        now = _clock()
        wait = _limiter_next - now
        if wait > 0:
            _sleeper(wait)
            now = _clock()
        _limiter_next = max(now, _limiter_next) + SEC_MIN_INTERVAL_SECONDS


def configure_clock(clock: Callable[[], float], sleeper: Callable[[float], None]) -> None:
    """Test hook. Production uses a monotonic clock and time.sleep."""
    global _clock, _sleeper
    _clock = clock
    _sleeper = sleeper


class HttpError(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class SecTransport:
    """One shared SEC client. `getter` is requests.get, or a fake in tests."""

    def __init__(self, email: str, getter: Optional[Callable[..., Any]] = None):
        self.email = email
        self._getter = getter

    def get(self, url: str) -> bytes:
        if not self.email:
            raise HttpError("sec_contact_email_unconfigured")
        if sec_is_down():
            raise HttpError("source_unavailable")
        acquire_sec_slot()
        getter = self._getter
        if getter is None:
            import requests

            getter = requests.get
        headers = sec_headers(self.email)
        try:
            response = getter(url, headers=headers, timeout=HTTP_TIMEOUT)
        except Exception as exc:
            mark_sec_down()
            raise HttpError("source_unavailable") from exc
        status = int(getattr(response, "status_code", 200) or 200)
        if status == 429 or status >= 500:
            mark_sec_down()
            raise HttpError("source_unavailable")
        if status >= 400:
            raise HttpError(f"status_{status}")
        body = getattr(response, "content", b"")
        if isinstance(body, str):
            body = body.encode("utf-8")
        return bytes(body or b"")

    def get_json(self, url: str) -> Any:
        body = self.get(url)
        return json.loads(body.decode("utf-8"))


def missing(signal: str, reason: str, detail: str = "") -> Dict[str, Any]:
    return {
        "signal": signal,
        "present": False,
        "direction": "neutral",
        "magnitude": 0,
        "points": 0,
        "detail": detail,
        "reason": reason,
    }


def observation(
    signal: str,
    *,
    direction: str,
    magnitude: int,
    detail: str,
    present: bool = True,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "signal": signal,
        "present": present,
        "direction": direction if present else "neutral",
        "magnitude": max(0, min(3, int(magnitude))) if present else 0,
        "points": 0,
        "detail": detail,
        "reason": reason,
    }


def cache_get(key: str) -> Any:
    now = time.time()
    with _memory_lock:
        hit = _memory.get(key)
        if hit and float(hit.get("expires_at") or 0) > now:
            return hit.get("value")
        if hit:
            _memory.pop(key, None)
    path = _cache_path(key)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _MISS
    if not isinstance(payload, dict) or float(payload.get("expires_at") or 0) <= now:
        return _MISS
    value = payload.get("value")
    with _memory_lock:
        _memory[key] = {"expires_at": payload["expires_at"], "value": value}
    return value


def cache_set(key: str, value: Any, ttl_seconds: float) -> None:
    expires_at = time.time() + max(1.0, float(ttl_seconds))
    with _memory_lock:
        _memory[key] = {"expires_at": expires_at, "value": value}
    path = _cache_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"expires_at": expires_at, "value": value}), encoding="utf-8")
    os.replace(tmp, path)


def cached(key: str, ttl_seconds: float, producer: Callable[[], Any], *, error_ttl: float = 60) -> Any:
    hit = cache_get(key)
    if hit is not _MISS:
        return hit
    try:
        value = producer()
    except Exception:
        value = None
        cache_set(key, {"_failed": True}, error_ttl)
        raise
    if isinstance(value, dict) and value.get("present") is False and value.get("reason") == "source_unavailable":
        cache_set(key, value, error_ttl)
    else:
        cache_set(key, value, ttl_seconds)
    return value


def clear_state() -> None:
    """Drop in-process cache, the SEC pause, and the rate-limit clock."""
    global _sec_down_until, _limiter_next, _clock, _sleeper
    with _memory_lock:
        _memory.clear()
    with _sec_down_lock:
        _sec_down_until = 0.0
    with _limiter_lock:
        _limiter_next = 0.0
    _clock = time.monotonic
    _sleeper = time.sleep
    directory = data_dir() / "research" / "market_intel_cache"
    if directory.exists():
        for child in directory.iterdir():
            try:
                child.unlink()
            except OSError:
                pass


def _cache_path(key: str) -> Path:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return cache_dir() / f"{digest}.json"


def _valid_contact(value: str) -> bool:
    if not value or any(ch.isspace() for ch in value):
        return False
    try:
        value.encode("ascii")
    except UnicodeEncodeError:
        return False
    if value.count("@") != 1:
        return False
    local, domain = value.split("@")
    return bool(local) and "." in domain and not domain.startswith(".") and not domain.endswith(".")
