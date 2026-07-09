import csv
from pathlib import Path
from typing import Dict, List, Sequence

BASE_DIR = Path(__file__).resolve().parents[1]
WATCHLIST_FILE = BASE_DIR / "data" / "watchlist.csv"
WATCHLIST_HEADERS = ["ticker", "category", "status", "ai_score", "notes"]


def _normalize_ticker(ticker: str) -> str:
    return (ticker or "").strip().upper()


def _normalize_row(row: Dict[str, str]) -> Dict[str, str]:
    ai_score_raw = str(row.get("ai_score", "0")).strip()
    try:
        ai_score = int(float(ai_score_raw))
    except ValueError:
        ai_score = 0
    ai_score = max(0, min(99, ai_score))
    return {
        "ticker": _normalize_ticker(row.get("ticker", "")),
        "category": (row.get("category", "Priority") or "Priority").strip(),
        "status": (row.get("status", "Active") or "Active").strip(),
        "ai_score": str(ai_score),
        "notes": (row.get("notes", "") or "").strip(),
    }


def _ensure_watchlist_file() -> None:
    WATCHLIST_FILE.parent.mkdir(parents=True, exist_ok=True)
    if WATCHLIST_FILE.exists():
        return

    with WATCHLIST_FILE.open("w", newline="", encoding="utf-8") as file_handle:
        writer = csv.DictWriter(file_handle, fieldnames=WATCHLIST_HEADERS)
        writer.writeheader()


def _write_watchlist(rows: List[Dict[str, str]]) -> None:
    _ensure_watchlist_file()
    with WATCHLIST_FILE.open("w", newline="", encoding="utf-8") as file_handle:
        writer = csv.DictWriter(file_handle, fieldnames=WATCHLIST_HEADERS)
        writer.writeheader()
        writer.writerows(rows)


def get_watchlist() -> List[Dict[str, str]]:
    _ensure_watchlist_file()
    with WATCHLIST_FILE.open(newline="", encoding="utf-8") as file_handle:
        rows = [_normalize_row(row) for row in csv.DictReader(file_handle)]
    return [row for row in rows if row["ticker"]]


def get_watchlist_tickers() -> List[str]:
    return [item["ticker"] for item in get_watchlist()]


def add_stock(payload: Dict[str, str]) -> Dict[str, str]:
    rows = get_watchlist()
    normalized = _normalize_row(payload)
    if not normalized["ticker"]:
        raise ValueError("Ticker is required.")

    if any(item["ticker"] == normalized["ticker"] for item in rows):
        raise ValueError(f"{normalized['ticker']} is already in the watchlist.")

    rows.append(normalized)
    _write_watchlist(rows)
    return normalized


def update_stock(ticker: str, payload: Dict[str, str]) -> Dict[str, str]:
    normalized_ticker = _normalize_ticker(ticker)
    if not normalized_ticker:
        raise ValueError("Ticker is required.")

    rows = get_watchlist()
    updated_row = None
    for index, row in enumerate(rows):
        if row["ticker"] != normalized_ticker:
            continue
        merged = {
            "ticker": normalized_ticker,
            "category": payload.get("category", row["category"]),
            "status": payload.get("status", row["status"]),
            "ai_score": payload.get("ai_score", row["ai_score"]),
            "notes": payload.get("notes", row["notes"]),
        }
        updated_row = _normalize_row(merged)
        rows[index] = updated_row
        break

    if not updated_row:
        raise ValueError(f"{normalized_ticker} was not found in the watchlist.")

    _write_watchlist(rows)
    return updated_row


def delete_stock(ticker: str) -> None:
    normalized_ticker = _normalize_ticker(ticker)
    if not normalized_ticker:
        raise ValueError("Ticker is required.")

    rows = get_watchlist()
    remaining = [row for row in rows if row["ticker"] != normalized_ticker]
    if len(remaining) == len(rows):
        raise ValueError(f"{normalized_ticker} was not found in the watchlist.")

    _write_watchlist(remaining)


def search_watchlist(
    rows: Sequence[Dict[str, str]],
    query: str = "",
    category: str = "",
    status: str = "",
    min_score: str = "",
    max_score: str = "",
) -> List[Dict[str, str]]:
    normalized_query = (query or "").strip().upper()
    normalized_category = (category or "").strip().lower()
    normalized_status = (status or "").strip().lower()

    min_score_value = None
    if str(min_score).strip():
        try:
            min_score_value = int(float(str(min_score)))
        except ValueError as error:
            raise ValueError("min_score must be a number.") from error

    max_score_value = None
    if str(max_score).strip():
        try:
            max_score_value = int(float(str(max_score)))
        except ValueError as error:
            raise ValueError("max_score must be a number.") from error

    filtered: List[Dict[str, str]] = []
    for row in rows:
        ticker = _normalize_ticker(row.get("ticker", ""))
        category_value = str(row.get("category", "")).strip().lower()
        status_value = str(row.get("status", "")).strip().lower()
        notes_value = str(row.get("notes", "")).strip().upper()
        score_value = int(float(str(row.get("ai_score", "0"))))

        if normalized_query and normalized_query not in ticker and normalized_query not in notes_value:
            continue
        if normalized_category and normalized_category != category_value:
            continue
        if normalized_status and normalized_status != status_value:
            continue
        if min_score_value is not None and score_value < min_score_value:
            continue
        if max_score_value is not None and score_value > max_score_value:
            continue
        filtered.append(row)
    return filtered


def sort_watchlist(rows: Sequence[Dict[str, str]], sort_by: str = "ticker", direction: str = "asc") -> List[Dict[str, str]]:
    sort_key = (sort_by or "ticker").strip().lower()
    reverse = (direction or "asc").strip().lower() == "desc"

    if sort_key == "ai_score":
        return sorted(rows, key=lambda item: int(float(str(item.get("ai_score", "0")))), reverse=reverse)
    if sort_key in {"category", "status", "notes"}:
        return sorted(rows, key=lambda item: str(item.get(sort_key, "")).lower(), reverse=reverse)
    return sorted(rows, key=lambda item: _normalize_ticker(str(item.get("ticker", ""))), reverse=reverse)


def add_watchlist_ticker(payload: Dict[str, str]) -> Dict[str, str]:
    return add_stock(payload)


def update_watchlist_ticker(ticker: str, payload: Dict[str, str]) -> Dict[str, str]:
    return update_stock(ticker=ticker, payload=payload)


def delete_watchlist_ticker(ticker: str) -> None:
    delete_stock(ticker=ticker)


def build_watchlist_suggestions(
    scanner_rows: List[Dict[str, str]], watchlist_tickers: List[str], limit: int = 8
) -> List[Dict[str, str]]:
    watchlist_set = {ticker.upper() for ticker in watchlist_tickers}
    suggestions: List[Dict[str, str]] = []

    for row in sorted(
        scanner_rows,
        key=lambda item: (float(item.get("scanner_score", 0)), float(item.get("relative_volume", 0))),
        reverse=True,
    ):
        ticker = _normalize_ticker(str(row.get("ticker", "")))
        if not ticker or ticker in watchlist_set:
            continue

        score = float(row.get("scanner_score", 0))
        rel_volume = float(row.get("relative_volume", 0))
        pct_change = float(row.get("percent_change", 0))
        if score < 65:
            continue

        price = float(row.get("price", 0))
        reason = (
            f"Scanner score {score:.0f} with {rel_volume:.2f}x relative volume "
            f"and daily move {pct_change:+.2f}%."
        )
        suggestions.append(
            {
                "ticker": ticker,
                "current_price": round(price, 2),
                "percent_change": round(pct_change, 2),
                "relative_volume": round(rel_volume, 2),
                "scanner_score": int(score),
                "reason": reason,
                "on_watchlist": False,
                "category": "Scanner",
                "status": "Candidate",
                "ai_score": f"{int(score)}",
                "notes": reason,
            }
        )
        if len(suggestions) >= limit:
            break

    return suggestions
