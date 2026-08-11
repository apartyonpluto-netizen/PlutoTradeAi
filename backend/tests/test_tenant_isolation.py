from __future__ import annotations

import analysis_lists
import anthropic_credentials
import watchlist
import webull_credentials
import webull_stop_orders
from autonomy.overnight_orders import list_overnight_orders, record_overnight_order


def test_webull_credentials_isolated(user_id, other_user_id):
    webull_credentials.set_webull_credentials(user_id, "user-a-key", "user-a-secret")
    webull_credentials.set_webull_credentials(other_user_id, "user-b-key", "user-b-secret")

    assert webull_credentials.get_webull_credentials(user_id) == {
        "app_key": "user-a-key",
        "app_secret": "user-a-secret",
    }
    assert webull_credentials.get_webull_credentials(other_user_id) == {
        "app_key": "user-b-key",
        "app_secret": "user-b-secret",
    }


def test_webull_credentials_missing_for_a_fresh_user(other_user_id):
    webull_credentials.set_webull_credentials("some-other-configured-user", "key", "secret")
    assert webull_credentials.get_webull_credentials(other_user_id) == {"app_key": "", "app_secret": ""}
    assert webull_credentials.is_webull_configured(other_user_id) is False


def test_anthropic_key_isolated(user_id, other_user_id):
    anthropic_credentials.set_anthropic_api_key(user_id, "sk-ant-user-a")
    assert anthropic_credentials.get_anthropic_api_key(other_user_id) == ""
    assert anthropic_credentials.is_anthropic_configured(other_user_id) is False
    assert anthropic_credentials.get_anthropic_api_key(user_id) == "sk-ant-user-a"


def test_watchlist_isolated(user_id, other_user_id):
    watchlist.add_stock(
        user_id, {"ticker": "AAPL", "category": "Core", "status": "Watching", "ai_score": "80", "notes": ""}
    )
    assert watchlist.get_watchlist_tickers(other_user_id) == []
    assert watchlist.get_watchlist_tickers(user_id) == ["AAPL"]


def test_watchlist_delete_does_not_touch_other_user(user_id, other_user_id):
    watchlist.add_stock(
        user_id, {"ticker": "AAPL", "category": "Core", "status": "Watching", "ai_score": "80", "notes": ""}
    )
    watchlist.add_stock(
        other_user_id, {"ticker": "AAPL", "category": "Core", "status": "Watching", "ai_score": "80", "notes": ""}
    )
    watchlist.delete_stock(user_id, "AAPL")
    assert watchlist.get_watchlist_tickers(user_id) == []
    assert watchlist.get_watchlist_tickers(other_user_id) == ["AAPL"]


def test_webull_stop_orders_isolated(user_id, other_user_id):
    webull_stop_orders.record_exit_order(user_id, "AAPL", "order-a-1", "stop")
    assert webull_stop_orders.tracked_tickers(other_user_id) == []
    assert webull_stop_orders.tracked_tickers(user_id) == ["AAPL"]


def test_webull_stop_orders_pop_does_not_touch_other_user(user_id, other_user_id):
    webull_stop_orders.record_exit_order(user_id, "AAPL", "order-a-1", "stop")
    webull_stop_orders.record_exit_order(other_user_id, "AAPL", "order-b-1", "stop")

    popped = webull_stop_orders.pop_exit_orders(user_id, "AAPL")
    assert popped == [{"id": "order-a-1", "type": "stop"}]
    assert webull_stop_orders.tracked_tickers(user_id) == []
    assert webull_stop_orders.tracked_tickers(other_user_id) == ["AAPL"]


def test_overnight_orders_isolated(user_id, other_user_id):
    record_overnight_order(user_id, {"ticker": "AAPL", "side": "BUY", "status": "placed"})
    assert list_overnight_orders(other_user_id) == []
    orders = list_overnight_orders(user_id)
    assert len(orders) == 1
    assert orders[0]["ticker"] == "AAPL"


def test_analysis_lists_isolated(user_id, other_user_id):
    analysis_lists.add_section_ticker(user_id, "candle_brain", "AAPL", default_tickers=[])
    assert analysis_lists.get_section_tickers(other_user_id, "candle_brain", default_tickers=[]) == []
    assert analysis_lists.get_section_tickers(user_id, "candle_brain", default_tickers=[]) == ["AAPL"]


def test_analysis_lists_sections_do_not_bleed_into_each_other(user_id):
    analysis_lists.add_section_ticker(user_id, "candle_brain", "AAPL", default_tickers=[])
    analysis_lists.add_section_ticker(user_id, "pattern_brain", "MSFT", default_tickers=[])
    assert analysis_lists.get_section_tickers(user_id, "candle_brain", default_tickers=[]) == ["AAPL"]
    assert analysis_lists.get_section_tickers(user_id, "pattern_brain", default_tickers=[]) == ["MSFT"]
