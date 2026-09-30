

def test_an_unreachable_provider_is_an_error_not_a_crash():
    from unittest.mock import patch

    import requests

    from news import news_service

    service = news_service.NewsService()
    with patch.object(news_service.alpaca_api, "is_configured", return_value=True), \
         patch.object(news_service.alpaca_api, "get_news", side_effect=requests.ConnectionError("unreachable")):
        bundle = service.get_news(["AAPL"], limit=5)
    alpaca = next(s for s in bundle["provider_status"] if s["provider"] == "alpaca_news")
    assert alpaca["ok"] is False and "unreachable" in alpaca["errors"][0]
