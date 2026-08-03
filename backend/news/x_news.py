from __future__ import annotations

import csv
import os
import re
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

import requests
try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv() -> bool:  # type: ignore[misc]
        return False

BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("PLUTO_DATA_DIR", str(BASE_DIR / "data"))).resolve()
TRUSTED_ACCOUNTS_FILE = DATA_DIR / "trusted_x_accounts.csv"
X_RECENT_SEARCH_URL = "https://api.x.com/2/tweets/search/recent"

POSITIVE_WORDS = {
    "beat",
    "bullish",
    "breakout",
    "growth",
    "strong",
    "uptrend",
    "upgrade",
    "profit",
}
NEGATIVE_WORDS = {
    "downgrade",
    "bearish",
    "selloff",
    "lawsuit",
    "fraud",
    "weak",
    "loss",
    "miss",
}
HYPE_WORDS = {"guaranteed", "100x", "moon", "pump", "insider tip", "get rich", "no risk"}


def _ensure_trusted_accounts_file() -> None:
    TRUSTED_ACCOUNTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    if TRUSTED_ACCOUNTS_FILE.exists():
        return

    with TRUSTED_ACCOUNTS_FILE.open("w", newline="", encoding="utf-8") as file_handle:
        writer = csv.DictWriter(file_handle, fieldnames=["username", "enabled"])
        writer.writeheader()
        writer.writerows(
            [
                {"username": "stocktalkweekly", "enabled": "true"},
                {"username": "MarketWatch", "enabled": "true"},
                {"username": "unusual_whales", "enabled": "true"},
            ]
        )


def get_trusted_accounts() -> List[Dict[str, str]]:
    _ensure_trusted_accounts_file()
    with TRUSTED_ACCOUNTS_FILE.open(newline="", encoding="utf-8") as file_handle:
        rows = list(csv.DictReader(file_handle))
    accounts = []
    for row in rows:
        username = (row.get("username", "") or "").strip().lstrip("@")
        if not username:
            continue
        accounts.append({"username": username, "enabled": (row.get("enabled", "true").lower() == "true")})
    return accounts


def _write_trusted_accounts(rows: Sequence[Dict[str, str]]) -> None:
    _ensure_trusted_accounts_file()
    with TRUSTED_ACCOUNTS_FILE.open("w", newline="", encoding="utf-8") as file_handle:
        writer = csv.DictWriter(file_handle, fieldnames=["username", "enabled"])
        writer.writeheader()
        writer.writerows(rows)


def add_trusted_account(username: str) -> Dict[str, str]:
    normalized = (username or "").strip().lstrip("@")
    if not normalized:
        raise ValueError("Account username is required.")

    rows = get_trusted_accounts()
    if any(item["username"].lower() == normalized.lower() for item in rows):
        raise ValueError(f"@{normalized} is already trusted.")

    rows.append({"username": normalized, "enabled": True})
    _write_trusted_accounts(rows)
    return {"username": normalized, "enabled": True}


def remove_trusted_account(username: str) -> None:
    normalized = (username or "").strip().lstrip("@").lower()
    if not normalized:
        raise ValueError("Account username is required.")

    rows = get_trusted_accounts()
    filtered = [item for item in rows if item["username"].lower() != normalized]
    if len(rows) == len(filtered):
        raise ValueError(f"@{username} not found in trusted accounts.")
    _write_trusted_accounts(filtered)


def _score_sentiment(text: str) -> Tuple[str, int]:
    lowered = text.lower()
    positive = sum(1 for word in POSITIVE_WORDS if word in lowered)
    negative = sum(1 for word in NEGATIVE_WORDS if word in lowered)
    if positive > negative:
        return "positive", positive - negative
    if negative > positive:
        return "negative", negative - positive
    return "neutral", 0


def _hype_flags(text: str) -> List[str]:
    lowered = text.lower()
    return [word for word in HYPE_WORDS if word in lowered]


def _extract_ticker_mentions(text: str, watchlist_tickers: Sequence[str]) -> List[str]:
    mentions = set(re.findall(r"\$([A-Za-z]{1,6})", text))
    watchlist_set = {ticker.upper() for ticker in watchlist_tickers}
    direct_mentions = {ticker for ticker in watchlist_set if ticker in text.upper()}
    matched = sorted((mentions | direct_mentions) & watchlist_set)
    return matched


def _build_query(tickers: Sequence[str], trusted_accounts: Sequence[Dict[str, str]]) -> str:
    cashtag_query = " OR ".join(f"${ticker.upper()}" for ticker in tickers)
    account_query = " OR ".join(f"from:{account['username']}" for account in trusted_accounts if account["enabled"])
    return f"({cashtag_query}) ({account_query}) -is:retweet lang:en"


def fetch_x_news_for_watchlist(tickers: Sequence[str], limit: int = 30) -> Tuple[List[Dict[str, str]], List[str]]:
    clean_tickers = [ticker.upper().strip() for ticker in tickers if ticker]
    if not clean_tickers:
        return [], ["No watchlist tickers available for X intelligence."]

    trusted_accounts = [item for item in get_trusted_accounts() if item["enabled"]]
    if not trusted_accounts:
        return [], ["No trusted X accounts configured."]

    load_dotenv()
    bearer_token = os.getenv("X_BEARER_TOKEN") or os.getenv("X_API_BEARER_TOKEN")
    if not bearer_token:
        return [], ["Missing X bearer token in .env (X_BEARER_TOKEN)."]

    query = _build_query(clean_tickers, trusted_accounts)
    try:
        response = requests.get(
            X_RECENT_SEARCH_URL,
            headers={"Authorization": f"Bearer {bearer_token}"},
            params={
                "query": query,
                "max_results": min(100, max(10, limit)),
                "tweet.fields": "created_at,author_id,public_metrics,text",
                "expansions": "author_id",
                "user.fields": "username,name,verified",
            },
            timeout=15,
        )
        response.raise_for_status()
    except requests.RequestException as error:
        return [], [f"X API request failed: {error}"]

    payload = response.json()
    tweets = payload.get("data", [])
    users = {user["id"]: user for user in payload.get("includes", {}).get("users", [])}

    news_items: List[Dict[str, str]] = []
    for tweet in tweets:
        user = users.get(tweet.get("author_id", ""), {})
        text = tweet.get("text", "")
        matched_tickers = _extract_ticker_mentions(text=text, watchlist_tickers=clean_tickers)
        if not matched_tickers:
            continue

        sentiment, sentiment_strength = _score_sentiment(text=text)
        hype = _hype_flags(text=text)
        username = user.get("username", "unknown")
        tweet_id = tweet.get("id", "")

        news_items.append(
            {
                "id": f"x-{tweet_id}",
                "ticker": matched_tickers[0],
                "tickers": matched_tickers,
                "author": f"@{username}",
                "text": text,
                "created_at": tweet.get("created_at", ""),
                "url": f"https://x.com/{username}/status/{tweet_id}" if tweet_id else "",
                "sentiment": sentiment,
                "sentiment_strength": sentiment_strength,
                "hype_flags": hype,
                "is_scammy_language": bool(hype),
                "never_auto_trade": True,
                "decision_guardrail": "X sentiment is informational only and never trade-executable by itself.",
            }
        )

    news_items.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    return news_items[:limit], []
