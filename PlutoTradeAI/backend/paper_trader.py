import csv
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).resolve().parents[1]
PAPER_TRADE_FILE = BASE_DIR / "data" / "paper_trades.csv"


def log_paper_trade(ticker, action, confidence, entry_price, reason):
    file_exists = PAPER_TRADE_FILE.exists()

    with open(PAPER_TRADE_FILE, mode="a", newline="") as file:
        fieldnames = [
            "date",
            "ticker",
            "action",
            "confidence",
            "entry_price",
            "reason",
            "status",
        ]

        writer = csv.DictWriter(file, fieldnames=fieldnames)

        if not file_exists:
            writer.writeheader()

        writer.writerow({
            "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "ticker": ticker,
            "action": action,
            "confidence": confidence,
            "entry_price": entry_price,
            "reason": reason,
            "status": "Open",
        })

    return f"Paper trade logged for {ticker}"


if __name__ == "__main__":
    print(log_paper_trade(
        "TSLA",
        "BUY",
        72,
        402.90,
        "Near support with strong scanner score"
    ))