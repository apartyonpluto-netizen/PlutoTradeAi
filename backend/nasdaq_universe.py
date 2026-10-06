"""Nasdaq-listed symbol directory, cached on disk.

The scanner used to score a fixed curated list. The full scan needs every
Nasdaq-listed name, refreshed from Nasdaq's public symbol directory and
reusable when that download fails. The file is pipe-delimited text:

    https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt

Test issues are dropped. Symbols that are not a plain 1–5 letter ticker
(warrants, preferreds, units) are dropped. Nasdaq-listed ETFs such as QQQ
stay in. NYSE names such as SPY are not in this file; callers that still
need them pass a fallback list, which is used only when both the download
and the last good cache are unavailable.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

import requests

NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
CACHE_TTL_SECONDS = 24 * 60 * 60
_SYMBOL_RE = re.compile(r"^[A-Z]{1,5}$")


def _data_dir() -> Path:
    return Path(os.environ.get("PLUTO_DATA_DIR", str(Path(__file__).resolve().parents[1] / "data"))).resolve()


def _cache_path() -> Path:
    path = _data_dir() / "scanner"
    path.mkdir(parents=True, exist_ok=True)
    return path / "nasdaq_listed_symbols.json"


def parse_nasdaq_listed(text: str) -> List[str]:
    """Symbols from one nasdaqlisted.txt body, in file order, de-duplicated."""
    symbols: List[str] = []
    seen = set()
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("Symbol|") or line.startswith("File Creation Time"):
            continue
        parts = line.split("|")
        if len(parts) < 4:
            continue
        symbol = parts[0].strip().upper()
        test_issue = parts[3].strip().upper()
        if test_issue == "Y" or not _SYMBOL_RE.match(symbol) or symbol in seen:
            continue
        seen.add(symbol)
        symbols.append(symbol)
    return symbols


def _read_cache() -> Optional[dict]:
    path = _cache_path()
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("symbols"), list):
        return None
    return payload


def _write_cache(symbols: Sequence[str]) -> None:
    path = _cache_path()
    body = {"fetched_at": time.time(), "symbols": list(symbols)}
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(body), encoding="utf-8")
    os.replace(tmp, path)


def _download() -> str:
    response = requests.get(NASDAQ_LISTED_URL, timeout=20)
    if response.status_code != 200:
        raise ValueError(f"Nasdaq symbol directory returned HTTP {response.status_code}")
    return response.text


def load_symbols(
    fallback: Sequence[str] | None = None,
    *,
    fetcher: Optional[Callable[[], str]] = None,
    now: Optional[float] = None,
) -> Tuple[List[str], str]:
    """Return (symbols, source).

    source is "nasdaq" after a fresh download, "cache" when a saved copy
    is reused (fresh, or stale because the download failed), or "fallback"
    when neither is available. fallback is the caller's last-resort list
    (the curated scan universe). It is not mixed into a real Nasdaq list.
    """
    clock = time.time() if now is None else now
    cached = _read_cache()
    cached_symbols = [str(symbol).upper() for symbol in (cached or {}).get("symbols", []) if symbol]
    fetched_at = float((cached or {}).get("fetched_at") or 0)
    if cached_symbols and clock - fetched_at < CACHE_TTL_SECONDS:
        return cached_symbols, "cache"

    try:
        text = (fetcher or _download)()
        symbols = parse_nasdaq_listed(text)
        if not symbols:
            raise ValueError("Nasdaq symbol directory parsed to an empty list")
        _write_cache(symbols)
        return symbols, "nasdaq"
    except Exception:
        if cached_symbols:
            return cached_symbols, "cache"
        fallback_symbols = []
        seen = set()
        for symbol in fallback or []:
            normalized = str(symbol or "").strip().upper()
            if normalized and normalized not in seen:
                seen.add(normalized)
                fallback_symbols.append(normalized)
        return fallback_symbols, "fallback"
