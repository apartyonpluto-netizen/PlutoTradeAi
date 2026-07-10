import csv
from pathlib import Path

WATCHLIST_FILE = Path("data/watchlist.csv")


def load_watchlist():
    if not WATCHLIST_FILE.exists():
        return []

    with open(WATCHLIST_FILE, mode="r", newline="") as file:
        reader = csv.DictReader(file)
        return list(reader)


def save_watchlist(watchlist):
    fieldnames = ["ticker", "category", "status", "ai_score", "notes"]

    with open(WATCHLIST_FILE, mode="w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(watchlist)


def get_watchlist():
    return load_watchlist()


def is_on_watchlist(ticker):
    ticker = ticker.upper()

    for stock in load_watchlist():
        if stock["ticker"].upper() == ticker:
            return True

    return False


def add_to_watchlist(ticker, category="AI Discovery", status="Pending", ai_score=0, notes=""):
    ticker = ticker.upper()
    watchlist = load_watchlist()

    if is_on_watchlist(ticker):
        return f"{ticker} is already on watchlist"

    watchlist.append({
        "ticker": ticker,
        "category": category,
        "status": status,
        "ai_score": str(ai_score),
        "notes": notes,
    })

    save_watchlist(watchlist)
    return f"{ticker} added to {category} watchlist"


if __name__ == "__main__":
    get_watchlist()
