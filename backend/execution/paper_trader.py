import csv
from pathlib import Path
from datetime import datetime

PAPER_TRADE_FILE = Path("data/paper_trades.csv")


def log_paper_options_trade(plan):
    file_exists = PAPER_TRADE_FILE.exists()

    with open(PAPER_TRADE_FILE, mode="a", newline="") as file:
        fieldnames = [
            "date",
            "ticker",
            "trade_type",
            "contract_type",
            "confidence",
            "entry_trigger",
            "stop_loss",
            "target_1",
            "target_2",
            "reason",
            "status",
        ]

        writer = csv.DictWriter(file, fieldnames=fieldnames)

        if not file_exists:
            writer.writeheader()

        writer.writerow({
            "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "ticker": plan["ticker"],
            "trade_type": plan["trade_type"],
            "contract_type": plan["contract_type"],
            "confidence": plan["confidence"],
            "entry_trigger": plan["entry_trigger"],
            "stop_loss": plan["stop_loss"],
            "target_1": plan["target_1"],
            "target_2": plan["target_2"],
            "reason": plan["reason"],
            "status": "Open",
        })

    return f"Paper options trade logged for {plan['ticker']}"
