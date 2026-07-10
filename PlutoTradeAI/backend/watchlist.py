from __future__ import annotations

import csv
from pathlib import Path
from typing import Dict, List

BASE_DIR = Path(__file__).resolve().parents[1]
WATCHLIST_FILE = BASE_DIR / "data" / "watchlist.csv"
WATCHLIST_FIELDS = ["ticker", "category", "status", "ai_score", "notes"]


def _normalize_ticker(value: str) -> str:
    return (value or "").strip().upper()


def _normalize_row(payload: Dict[str, str]) -> Dict[str, str]:
    return {
        "ticker": _normalize_ticker(payload.get("ticker", "")),
        "category": (payload.get("category", "Priority") or "Priority").strip(),
        "status": (payload.get("status", "Active") or "Active").strip(),
        "ai_score": str(payload.get("ai_score", "0") or "0").strip(),
        "notes": (payload.get("notes", "") or "").strip(),
    }


def _ensure_file() -> None:
    WATCHLIST_FILE.parent.mkdir(parents=True, exist_ok=True)
    if WATCHLIST_FILE.exists():
        return
    with WATCHLIST_FILE.open("w", newline="", encoding="utf-8") as file_handle:
        writer = csv.DictWriter(file_handle, fieldnames=WATCHLIST_FIELDS)
        writer.writeheader()


def _write_rows(rows: List[Dict[str, str]]) -> None:
    _ensure_file()
    with WATCHLIST_FILE.open("w", newline="", encoding="utf-8") as file_handle:
        writer = csv.DictWriter(file_handle, fieldnames=WATCHLIST_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def get_watchlist() -> List[Dict[str, str]]:
    _ensure_file()
    with WATCHLIST_FILE.open("r", newline="", encoding="utf-8") as file_handle:
        rows = [_normalize_row(row) for row in csv.DictReader(file_handle)]
    return [row for row in rows if row["ticker"]]


def get_watchlist_tickers() -> List[str]:
    return [row["ticker"] for row in get_watchlist()]


def is_on_watchlist(ticker: str) -> bool:
    target = _normalize_ticker(ticker)
    return any(row["ticker"] == target for row in get_watchlist())


def add_stock(payload: Dict[str, str]) -> Dict[str, str]:
    rows = get_watchlist()
    new_row = _normalize_row(payload)
    if not new_row["ticker"]:
        raise ValueError("Ticker is required.")
    if any(row["ticker"] == new_row["ticker"] for row in rows):
        raise ValueError(f"{new_row['ticker']} is already in the watchlist.")
    rows.append(new_row)
    _write_rows(rows)
    return new_row


def update_stock(ticker: str, payload: Dict[str, str]) -> Dict[str, str]:
    target = _normalize_ticker(ticker)
    if not target:
        raise ValueError("Ticker is required.")
    rows = get_watchlist()
    for index, row in enumerate(rows):
        if row["ticker"] != target:
            continue
        merged = _normalize_row(
            {
                "ticker": target,
                "category": payload.get("category", row["category"]),
                "status": payload.get("status", row["status"]),
                "ai_score": payload.get("ai_score", row["ai_score"]),
                "notes": payload.get("notes", row["notes"]),
            }
        )
        rows[index] = merged
        _write_rows(rows)
        return merged
    raise ValueError(f"{target} not found in watchlist.")


def delete_stock(ticker: str) -> None:
    target = _normalize_ticker(ticker)
    if not target:
        raise ValueError("Ticker is required.")
    rows = get_watchlist()
    remaining = [row for row in rows if row["ticker"] != target]
    if len(remaining) == len(rows):
        raise ValueError(f"{target} not found in watchlist.")
    _write_rows(remaining)


def search_watchlist(query: str, rows: List[Dict[str, str]] | None = None) -> List[Dict[str, str]]:
    source_rows = rows if rows is not None else get_watchlist()
    value = (query or "").strip().upper()
    if not value:
        return source_rows
    return [
        row
        for row in source_rows
        if value in row["ticker"]
        or value in row["category"].upper()
        or value in row["status"].upper()
        or value in row["notes"].upper()
    ]


def sort_watchlist(rows: List[Dict[str, str]], sort_by: str = "ticker", descending: bool = False) -> List[Dict[str, str]]:
    key = (sort_by or "ticker").strip().lower()
    if key == "ai_score":
        return sorted(rows, key=lambda row: float(row.get("ai_score", "0") or 0), reverse=descending)
    if key not in {"ticker", "category", "status", "notes"}:
        key = "ticker"
    return sorted(rows, key=lambda row: row.get(key, "").upper(), reverse=descending)


def filter_watchlist(rows: List[Dict[str, str]], *, category: str = "", status: str = "") -> List[Dict[str, str]]:
    filtered = rows
    category_value = (category or "").strip().upper()
    status_value = (status or "").strip().upper()
    if category_value:
        filtered = [row for row in filtered if row["category"].upper() == category_value]
    if status_value:
        filtered = [row for row in filtered if row["status"].upper() == status_value]
    return filtered