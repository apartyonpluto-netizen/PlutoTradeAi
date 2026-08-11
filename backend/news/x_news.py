from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

import requests
try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv() -> bool:  # type: ignore[misc]
        return False

BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("PLUTO_DATA_DIR", str(BASE_DIR / "data"))).resolve()
USER_DATA_ROOT = DATA_DIR / "users"
X_RECENT_SEARCH_URL = "https://api.x.com/2/tweets/search/recent"
X_USER_LOOKUP_URL = "https://api.x.com/2/users/by/username/{username}"

DEFAULT_TRUSTED_ACCOUNTS = [
    {"username": "stocktalkweekly", "enabled": True},
    {"username": "MarketWatch", "enabled": True},
    {"username": "unusual_whales", "enabled": True},
]

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


def _trusted_accounts_file(user_id: str) -> Path:
    if not user_id:
        raise ValueError("user_id is required.")
    path = USER_DATA_ROOT / user_id
    path.mkdir(parents=True, exist_ok=True)
    return path / "trusted_x_accounts.json"


def get_trusted_accounts(user_id: str) -> List[Dict[str, Any]]:
    """Each user curates their own trusted X account list - starts from the
    same sensible defaults, but adding/removing here never affects anyone
    else's feed, same as every other per-user store in this app."""
    accounts_file = _trusted_accounts_file(user_id)
    if not accounts_file.exists():
        _write_trusted_accounts(user_id, DEFAULT_TRUSTED_ACCOUNTS)
        return [dict(item) for item in DEFAULT_TRUSTED_ACCOUNTS]
    try:
        data = json.loads(accounts_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    accounts = []
    for row in data:
        username = str(row.get("username", "")).strip().lstrip("@")
        if not username:
            continue
        accounts.append({"username": username, "enabled": bool(row.get("enabled", True))})
    return accounts


def _write_trusted_accounts(user_id: str, rows: Sequence[Dict[str, Any]]) -> None:
    _trusted_accounts_file(user_id).write_text(json.dumps(list(rows), indent=2), encoding="utf-8")


def add_trusted_account(user_id: str, username: str) -> Dict[str, Any]:
    normalized = (username or "").strip().lstrip("@")
    if not normalized:
        raise ValueError("Account username is required.")

    rows = get_trusted_accounts(user_id)
    if any(item["username"].lower() == normalized.lower() for item in rows):
        raise ValueError(f"@{normalized} is already trusted.")

    rows.append({"username": normalized, "enabled": True})
    _write_trusted_accounts(user_id, rows)
    return {"username": normalized, "enabled": True}


def remove_trusted_account(user_id: str, username: str) -> None:
    normalized = (username or "").strip().lstrip("@").lower()
    if not normalized:
        raise ValueError("Account username is required.")

    rows = get_trusted_accounts(user_id)
    filtered = [item for item in rows if item["username"].lower() != normalized]
    if len(rows) == len(filtered):
        raise ValueError(f"@{username} not found in trusted accounts.")
    _write_trusted_accounts(user_id, filtered)


def _get_bearer_token() -> str:
    load_dotenv()
    return os.getenv("X_BEARER_TOKEN") or os.getenv("X_API_BEARER_TOKEN") or ""


def lookup_x_user(username: str) -> Dict[str, Any]:
    """Confirms a handle is a real X account before it's trusted - the X API
    doesn't offer a general username-search/autocomplete endpoint at standard
    access tiers (that existed in the old v1.1 API and is now heavily
    restricted), so this is a verify-on-submit lookup rather than
    type-ahead: it looks up the exact handle and returns the real display
    name, verified status, and profile photo so the user can visually
    confirm it's the account they meant, not an impersonator or a typo."""
    normalized = (username or "").strip().lstrip("@")
    if not normalized:
        return {"found": False, "error": "Enter a username to verify."}

    bearer_token = _get_bearer_token()
    if not bearer_token:
        return {"found": False, "error": "X API isn't configured yet (missing X_BEARER_TOKEN)."}

    try:
        response = requests.get(
            X_USER_LOOKUP_URL.format(username=normalized),
            headers={"Authorization": f"Bearer {bearer_token}"},
            params={"user.fields": "name,username,verified,profile_image_url,description,public_metrics"},
            timeout=10,
        )
    except requests.RequestException as error:
        return {"found": False, "error": f"X API request failed: {error}"}

    if response.status_code == 404:
        return {"found": False, "error": f"No X account found for @{normalized}."}
    if response.status_code != 200:
        return {"found": False, "error": f"X API error: HTTP {response.status_code}"}

    user = response.json().get("data")
    if not user:
        return {"found": False, "error": f"No X account found for @{normalized}."}

    return {
        "found": True,
        "username": user.get("username", normalized),
        "name": user.get("name", ""),
        "verified": bool(user.get("verified", False)),
        "profile_image_url": user.get("profile_image_url", ""),
        "description": user.get("description", ""),
        "followers_count": (user.get("public_metrics") or {}).get("followers_count", 0),
    }


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


def _build_query(tickers: Sequence[str], trusted_accounts: Sequence[Dict[str, Any]]) -> str:
    cashtag_query = " OR ".join(f"${ticker.upper()}" for ticker in tickers)
    account_query = " OR ".join(f"from:{account['username']}" for account in trusted_accounts if account["enabled"])
    return f"({cashtag_query}) ({account_query}) -is:retweet lang:en"


def fetch_x_news_for_watchlist(user_id: str, tickers: Sequence[str], limit: int = 30) -> Tuple[List[Dict[str, Any]], List[str]]:
    clean_tickers = [ticker.upper().strip() for ticker in tickers if ticker]
    if not clean_tickers:
        return [], ["No watchlist tickers available for X intelligence."]

    trusted_accounts = [item for item in get_trusted_accounts(user_id) if item["enabled"]]
    if not trusted_accounts:
        return [], ["No trusted X accounts configured."]

    bearer_token = _get_bearer_token()
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

    news_items: List[Dict[str, Any]] = []
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
