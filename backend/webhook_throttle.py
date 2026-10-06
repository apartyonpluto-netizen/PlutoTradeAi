"""Rate limit for the TradingView webhook.

The webhook is token-authenticated and does not execute trades, but it is
public to anyone who has (or guesses) a token. Limits are shared across
gunicorn workers via a small JSON file under PLUTO_DATA_DIR, same pattern
as login_throttle. Keys are salted hashes, never the raw token or address.

A storage failure fails open: the webhook is a log, and a broken throttle
file must not drop a legitimate alert.
"""

from __future__ import annotations

import contextlib
import functools
import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Dict, List, Optional

TOKEN_LIMIT = 30
IP_LIMIT = 60
WINDOW_SECONDS = 60


def _data_dir() -> Path:
    return Path(os.environ.get("PLUTO_DATA_DIR", str(Path(__file__).resolve().parents[1] / "data"))).resolve()


def _file() -> Path:
    path = _data_dir() / "security"
    path.mkdir(parents=True, exist_ok=True)
    return path / "webhook_hits.json"


def _key(kind: str, value: str) -> str:
    salt = os.environ.get("FLASK_SECRET_KEY", "pluto")
    return kind + ":" + hashlib.sha256(f"{salt}|webhook|{kind}|{value}".encode()).hexdigest()[:32]


@contextlib.contextmanager
def _locked():
    path = _file()
    with open(path.with_name(path.name + ".lock"), "a+") as handle:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            try:
                data: Dict[str, List[float]] = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
            now = time.time()
            data = {key: [stamp for stamp in stamps if now - stamp < WINDOW_SECONDS] for key, stamps in data.items()}
            data = {key: stamps for key, stamps in data.items() if stamps}
            yield data
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            os.replace(tmp, path)
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _fail_open(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as error:  # noqa: BLE001
            logging.getLogger(__name__).warning("webhook throttle unavailable: %s", error)
            return None
    return wrapper


@_fail_open
def retry_after(token: str, address: str) -> Optional[int]:
    """Seconds until another request is allowed, or None if this one is counted and allowed."""
    now = time.time()
    token_key = _key("token", token or "")
    ip_key = _key("ip", address or "")
    with _locked() as data:
        token_hits = data.get(token_key, [])
        ip_hits = data.get(ip_key, [])
        waits = []
        if len(token_hits) >= TOKEN_LIMIT and token_hits:
            waits.append(WINDOW_SECONDS - (now - min(token_hits)))
        if len(ip_hits) >= IP_LIMIT and ip_hits:
            waits.append(WINDOW_SECONDS - (now - min(ip_hits)))
        if waits:
            return max(1, int(max(waits)) + 1)
        token_hits.append(now)
        ip_hits.append(now)
        data[token_key] = token_hits
        data[ip_key] = ip_hits
    return None
