from __future__ import annotations

from typing import Dict, List


TRUSTED_NEWS_SOURCES: List[Dict[str, str]] = [
    {"name": "Official X API", "status": "ready", "integration": "backend/news/x_news.py"},
    {"name": "RSS Feeds", "status": "planned", "integration": "future_settings_sources"},
    {"name": "Yahoo Finance News", "status": "planned", "integration": "future_feed_adapter"},
    {"name": "MarketWatch", "status": "planned", "integration": "future_feed_adapter"},
    {"name": "Benzinga", "status": "future", "integration": "enterprise_license_required"},
]


def get_future_news_roadmap() -> Dict[str, object]:
    return {
        "sources": TRUSTED_NEWS_SOURCES,
        "guardrails": {
            "scraping_allowed": False,
            "trade_decision_from_social_only": False,
            "trusted_sources_editable_in_settings": True,
        },
    }
