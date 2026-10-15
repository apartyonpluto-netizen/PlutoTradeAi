"""Remember a parsed file until its size or mtime changes.

Logged-in pages read the same JSON more than once (closed trades, the
research log, overnight orders, settings). The second read is a stat plus
a memory hit. A write changes the file's mtime, so the next read loads
the new bytes.

Some filesystems keep the previous mtime when a replacement file is the
same size. Writers in this process call forget() after a successful
write so the next read cannot reuse the old parse.
"""

from __future__ import annotations

import os
from typing import Callable, Dict, Tuple, TypeVar

T = TypeVar("T")

# mtime_ns, size, generation, parsed value
_CACHE: Dict[str, Tuple[int, int, int, object]] = {}
_GENERATION: Dict[str, int] = {}


def load(path: object, reader: Callable[[], T]) -> T:
    key = os.fspath(path)  # type: ignore[arg-type]
    generation = _GENERATION.get(key, 0)
    try:
        stat = os.stat(key)
    except OSError:
        return reader()
    token = (stat.st_mtime_ns, stat.st_size)
    hit = _CACHE.get(key)
    if hit is not None and hit[0] == token[0] and hit[1] == token[1] and hit[2] == generation:
        return hit[3]  # type: ignore[return-value]
    value = reader()
    try:
        after = os.stat(key)
    except OSError:
        return value
    # A write landed while we were reading. Don't remember the mix.
    if (after.st_mtime_ns, after.st_size) != token or _GENERATION.get(key, 0) != generation:
        return value
    _CACHE[key] = (token[0], token[1], generation, value)
    return value


def forget(path: object) -> None:
    key = os.fspath(path)  # type: ignore[arg-type]
    _CACHE.pop(key, None)
    _GENERATION[key] = _GENERATION.get(key, 0) + 1
