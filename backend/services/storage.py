"""Shared, crash-safe JSON persistence helpers for the paper-trading state files.

Design rules (they exist because the state files hold financial records):

* A file that exists but cannot be parsed is NEVER treated as "empty". It is copied
  aside for forensics and a StorageCorruptionError is raised so the caller refuses to
  continue instead of overwriting the evidence with a fresh account.
* Writes go to a temp file in the same directory, are fsynced, then atomically renamed.
  NaN/Infinity are rejected on write and on read so they can never be persisted.
* CrossProcessRLock serialises read-modify-write sections across threads AND across
  processes (gunicorn workers, the worker dyno, a CLI run) with flock on a sidecar file.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable, Optional


class StorageCorruptionError(RuntimeError):
    """A persisted state file exists but is unreadable. Operator action is required."""


def _reject_constant(name: str) -> Any:
    raise ValueError(f"non-finite JSON constant {name!r} is not allowed")


def _quarantine(path: Path, raw: bytes) -> Optional[Path]:
    try:
        digest = hashlib.sha256(raw).hexdigest()[:10]
        target = path.with_name(f"{path.name}.corrupt-{digest}")
        if not target.exists():
            shutil.copy2(path, target)
        return target
    except OSError:
        return None


def read_json_strict(path: Path, default_factory: Callable[[], Any]) -> Any:
    """Return parsed JSON, or default_factory() when the file does not exist.

    Raises StorageCorruptionError when the file exists but is empty, unparsable, or
    contains non-finite numbers. The file is left in place (and copied aside)."""
    path = Path(path)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return default_factory()
    except OSError as exc:
        raise StorageCorruptionError(f"{path} could not be read: {exc}") from exc
    try:
        if not raw.strip():
            raise ValueError("file is empty")
        return json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
    except (ValueError, UnicodeDecodeError) as exc:
        copy = _quarantine(path, raw)
        where = f" (copy kept at {copy})" if copy else ""
        raise StorageCorruptionError(
            f"{path} is corrupted: {exc}{where}. Refusing to replace it with an empty record; "
            "restore it from a backup or move it aside deliberately."
        ) from exc


def atomic_write_json(path: Path, payload: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
            temp_name = handle.name
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        temp_name = None
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)


class CrossProcessRLock:
    """Re-entrant lock that is exclusive across threads and processes."""

    def __init__(self, lock_path: Path) -> None:
        self._path = Path(lock_path)
        self._rlock = threading.RLock()
        self._depth = 0
        self._handle = None

    def __enter__(self) -> "CrossProcessRLock":
        self._rlock.acquire()
        try:
            if self._depth == 0:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                handle = open(self._path, "a+")
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                except BaseException:
                    handle.close()
                    raise
                self._handle = handle
            self._depth += 1
        except BaseException:
            self._rlock.release()
            raise
        return self

    def __exit__(self, *_exc: Any) -> None:
        try:
            self._depth -= 1
            if self._depth == 0 and self._handle is not None:
                try:
                    fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
                finally:
                    self._handle.close()
                    self._handle = None
        finally:
            self._rlock.release()


def finite_number(value: Any) -> Optional[float]:
    """float(value) when it is a real, finite number (bools and strings like 'nan' rejected)."""
    import math

    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        result = float(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            result = float(text)
        except ValueError:
            return None
    else:
        return None
    return result if math.isfinite(result) else None


def data_file(env_name: str, filename: str) -> Path:
    """State file location: explicit env var, else $PLUTO_DATA_DIR/<filename>, else ./data/<filename>."""
    explicit = os.getenv(env_name)
    if explicit:
        return Path(explicit)
    return Path(os.getenv("PLUTO_DATA_DIR", "data")) / filename
