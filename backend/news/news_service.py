from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Sequence, Tuple

from .x_news import fetch_x_news_for_watchlist


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ProviderResult:
    provider: str
    items: List[Dict[str, Any]]
    errors: List[str]


class MockNewsProvider:
    provider_name = "demo_news"

    def fetch(self, tickers: Sequence[str], limit: int = 20) -> ProviderResult:
        symbol = (tickers[0] if tickers else "SPY").upper()
        items = [
            {
                "id": f"demo-{symbol}-1",
                "ticker": symbol,
                "source": "demo",
                "provider": self.provider_name,
                "headline": f"{symbol} liquidity remains stable in after-hours session",
                "text": "Demo news item for integration scaffolding. Never auto-trade from news alone.",
                "sentiment": "neutral",
                "created_at": _now_iso(),
                "never_auto_trade": True,
            }
        ]
        return ProviderResult(provider=self.provider_name, items=items[:limit], errors=[])


class XNewsProvider:
    provider_name = "x_official_api"

    def fetch(self, tickers: Sequence[str], limit: int = 20) -> ProviderResult:
        items, errors = fetch_x_news_for_watchlist(tickers=list(tickers), limit=limit)
        enriched = [{**item, "provider": self.provider_name} for item in items]
        return ProviderResult(provider=self.provider_name, items=enriched, errors=errors)


class NewsService:
    def __init__(self) -> None:
        self.providers = [
            XNewsProvider(),
            MockNewsProvider(),
        ]
        self.provider_roadmap = [
            {"name": "official_x_api", "status": "ready"},
            {"name": "rss_adapter", "status": "planned"},
            {"name": "yahoo_finance_news", "status": "planned"},
            {"name": "future_provider_slot", "status": "planned"},
        ]

    def get_news(self, tickers: Sequence[str], limit: int = 20) -> Dict[str, Any]:
        results: List[ProviderResult] = [provider.fetch(tickers=tickers, limit=limit) for provider in self.providers]
        items: List[Dict[str, Any]] = []
        errors: List[str] = []
        provider_status: List[Dict[str, Any]] = []
        for result in results:
            items.extend(result.items)
            errors.extend(result.errors)
            provider_status.append(
                {
                    "provider": result.provider,
                    "ok": len(result.errors) == 0,
                    "item_count": len(result.items),
                    "errors": result.errors,
                }
            )

        items.sort(key=lambda item: item.get("created_at", ""), reverse=True)
        return {
            "items": items[:limit],
            "errors": errors,
            "provider_status": provider_status,
            "provider_roadmap": self.provider_roadmap,
            "guardrails": {
                "never_scrape": True,
                "never_auto_trade_from_news_alone": True,
            },
            "timestamp": _now_iso(),
        }


def fetch_news_bundle(tickers: Sequence[str], limit: int = 20) -> Dict[str, Any]:
    service = NewsService()
    return service.get_news(tickers=tickers, limit=limit)

