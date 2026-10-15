"""Remember a parsed file until its size or mtime changes.

Logged-in pages read the same JSON more than once (closed trades, the
research log, overnight orders, settings). The second read is a stat plus
a memory hit. A write changes the file's mtime, so the next read loads
the new bytes.
"""

from __future__ import annotations

import os
from typing import Callable, Dict, Tuple, TypeVar

T = TypeVar("T")

_CACHE: Dict[str, Tuple[int, int, object]] = {}


def load(path: object, reader: Callable[[], T]) -> T:
    key = os.fspath(path)  # type: ignore[arg-type]
    try:
        stat = os.stat(key)
    except OSError:
        return reader()
    token = (stat.st_mtime_ns, stat.st_size)
    hit = _CACHE.get(key)
    if hit is not None and hit[0] == token[0] and hit[1] == token[1]:
        return hit[2]  # type: ignore[return-value]
    value = reader()
    try:
        after = os.stat(key)
    except OSError:
        return value
    # A write landed while we were reading. Don't remember the mix.
    if (after.st_mtime_ns, after.st_size) != token:
        return value
    _CACHE[key] = (token[0], token[1], value)
    return value


def forget(path: object) -> None:
    _CACHE.pop(os.fspath(path), None)  # type: ignore[arg-type]
