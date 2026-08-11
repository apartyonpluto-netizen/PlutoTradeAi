from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List, Sequence

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("PLUTO_DATA_DIR", str(BASE_DIR / "data"))).resolve()
USER_DATA_ROOT = DATA_DIR / "users"

SECTIONS = ("candle_brain", "pattern_brain", "volume_intelligence", "support_resistance")
MAX_TICKERS_PER_SECTION = 8


def _check_section(section: str) -> None:
    if section not in SECTIONS:
        raise ValueError(f"Unknown analysis section: {section}")


def _store_file(user_id: str) -> Path:
    if not user_id:
        raise ValueError("user_id is required.")
    path = USER_DATA_ROOT / user_id
    path.mkdir(parents=True, exist_ok=True)
    return path / "analysis_lists.json"


def _read(user_id: str) -> Dict[str, List[str]]:
    store_file = _store_file(user_id)
    if not store_file.exists():
        return {}
    try:
        data = json.loads(store_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _write(user_id: str, data: Dict[str, List[str]]) -> None:
    _store_file(user_id).write_text(json.dumps(data, indent=2), encoding="utf-8")


def _normalize(tickers: Sequence[str]) -> List[str]:
    normalized: List[str] = []
    for raw in tickers:
        ticker = str(raw or "").strip().upper()
        if ticker and ticker not in normalized:
            normalized.append(ticker)
        if len(normalized) >= MAX_TICKERS_PER_SECTION:
            break
    return normalized


def get_section_tickers(user_id: str, section: str, default_tickers: Sequence[str]) -> List[str]:
    """The user's saved list for this section, or their current default
    derivation (e.g. watchlist-based) if they haven't customized it yet -
    customizing one section never affects the others."""
    _check_section(section)
    saved = _read(user_id).get(section)
    return saved if saved else _normalize(default_tickers)


def set_section_tickers(user_id: str, section: str, tickers: Sequence[str]) -> List[str]:
    _check_section(section)
    normalized = _normalize(tickers)
    data = _read(user_id)
    data[section] = normalized
    _write(user_id, data)
    return normalized


def add_section_ticker(user_id: str, section: str, ticker: str, default_tickers: Sequence[str]) -> List[str]:
    current = get_section_tickers(user_id, section, default_tickers)
    ticker = str(ticker or "").strip().upper()
    if not ticker:
        raise ValueError("Ticker is required.")
    if ticker in current:
        return current
    if len(current) >= MAX_TICKERS_PER_SECTION:
        raise ValueError(f"This section is capped at {MAX_TICKERS_PER_SECTION} tickers - remove one first.")
    return set_section_tickers(user_id, section, current + [ticker])


def add_focus_ticker(user_id: str, section: str, ticker: str, default_tickers: Sequence[str]) -> List[str]:
    """Like add_section_ticker, but used when the user navigates in from
    elsewhere with a specific ticker in mind (?ticker=...) - pins it to the
    front of the list instead of appending, bumping the oldest entry out if
    the section is already at its cap, so the ticker they came to look at is
    always visible without needing a second click."""
    current = get_section_tickers(user_id, section, default_tickers)
    ticker = str(ticker or "").strip().upper()
    if not ticker or current[:1] == [ticker]:
        return current
    reordered = [ticker] + [t for t in current if t != ticker]
    return set_section_tickers(user_id, section, reordered)


def remove_section_ticker(user_id: str, section: str, ticker: str, default_tickers: Sequence[str]) -> List[str]:
    current = get_section_tickers(user_id, section, default_tickers)
    ticker = str(ticker or "").strip().upper()
    return set_section_tickers(user_id, section, [t for t in current if t != ticker])
