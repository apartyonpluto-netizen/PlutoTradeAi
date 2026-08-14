from __future__ import annotations

import os
from unittest.mock import patch

import app as pluto_app
import order_lifecycle as ol
from autonomy.overnight_orders import list_overnight_orders, record_overnight_order

CREDS = {"app_key": "key", "app_secret": "secret"}
ACCOUNT_ID = "acct-1"


def _orphan_id(user_id: str, ticker: str = "AAPL", day: str | None = None) -> str:
    """The REAL deterministic id this user's own _submit_and_protect_entry
    would have generated - strong attribution requires an EXACT hash
    match, not a prefix, so every test must use a genuinely-computed id
    for the (user_id, ticker, day) it claims to represent."""
    day = day or pluto_app._trading_day_key()
    return ol.deterministic_client_order_id(user_id, ticker, day, "entry", attempt=1)


def _history_row(client_order_id: str, symbol: str = "AAPL", side: str = "BUY", quantity: float = 10, limit_price: float = 100.0) -> dict:
    return {"client_order_id": client_order_id, "symbol": symbol, "side": side, "total_quantity": str(quantity), "limit_price": str(limit_price)}


def test_discovers_a_genuine_orphan_and_freezes_new_entries(user_id):
    orphan_id = _orphan_id(user_id)
    with patch.object(pluto_app.webull_api, "get_order_history", return_value=[_history_row(orphan_id)]):
        discovered = pluto_app._discover_orphaned_broker_entries(user_id, CREDS, ACCOUNT_ID)

    assert discovered == 1
    orders = list_overnight_orders(user_id)
    assert len(orders) == 1
    orphan = orders[0]
    assert orphan["entry_client_order_id"] == orphan_id
    assert orphan["lifecycle_state"] == ol.UNKNOWN_SUBMISSION_STATE
    assert orphan["ticker"] == "AAPL"
    assert orphan["quantity"] == 10.0
    assert orphan["limit_price"] == 100.0
    assert orphan["stop"] == 0
    assert orphan["target"] == 0
    assert orphan["orphan_recovered"] is True

    # UNKNOWN_SUBMISSION_STATE is one of order_lifecycle.FROZEN_STATES -
    # this must freeze new entries account-wide immediately, same tick.
    assert pluto_app._has_unresolved_ambiguous_submission_locally(user_id) is True

    from alerts import load_manual_alerts
    critical_alerts = [a for a in load_manual_alerts(user_id) if a.get("type") == "orphan_entry_discovered"]
    assert len(critical_alerts) == 1
    assert critical_alerts[0]["priority"] == "critical"


def test_discovers_an_orphan_placed_on_an_earlier_trading_day(user_id):
    """The candidate-day search window must actually reach back, not just
    check today - a crash discovered a couple of ticks (or one weekend)
    after the original placement is still findable."""
    from datetime import timedelta
    earlier_day = pluto_app._trading_day_key(pluto_app._now_utc() - timedelta(days=2))
    orphan_id = _orphan_id(user_id, day=earlier_day)
    with patch.object(pluto_app.webull_api, "get_order_history", return_value=[_history_row(orphan_id)]):
        discovered = pluto_app._discover_orphaned_broker_entries(user_id, CREDS, ACCOUNT_ID)
    assert discovered == 1
    assert list_overnight_orders(user_id)[0]["entry_client_order_id"] == orphan_id


def test_ignores_a_sell_order_even_with_a_matching_id(user_id):
    """A SELL order is a protective leg (stop or target), never an entry -
    must never be misidentified as an orphaned entry even if its id
    happens to match this user's own deterministic entry id (which in
    practice it structurally cannot, since legs are hashed with a
    different `leg` value - but side is checked independently regardless,
    defense in depth)."""
    orphan_id = _orphan_id(user_id)
    with patch.object(pluto_app.webull_api, "get_order_history", return_value=[_history_row(orphan_id, side="SELL")]):
        discovered = pluto_app._discover_orphaned_broker_entries(user_id, CREDS, ACCOUNT_ID)
    assert discovered == 0
    assert list_overnight_orders(user_id) == []


def test_ignores_an_order_with_a_pt_prefix_that_does_not_exactly_match(user_id):
    """Strong attribution: a bare "pt" prefix (or any id that merely LOOKS
    like this app's format) must never be enough on its own - only an
    EXACT deterministic-hash match counts. This id starts with "pt" but
    is not the real hash for this user/ticker/day, so it must be ignored,
    not swept up as this user's orphan."""
    fake_id = "pt" + "0" * 30
    with patch.object(pluto_app.webull_api, "get_order_history", return_value=[_history_row(fake_id)]):
        discovered = pluto_app._discover_orphaned_broker_entries(user_id, CREDS, ACCOUNT_ID)
    assert discovered == 0
    assert list_overnight_orders(user_id) == []


def test_ignores_another_users_deterministic_id_for_the_same_ticker_and_day(user_id, other_user_id):
    """The strongest possible proof of per-user specificity: a REAL,
    validly-computed deterministic id, but for a DIFFERENT user - must
    never be attributed to this user just because it's a well-formed
    entry id for the same ticker/day."""
    other_users_id = _orphan_id(other_user_id)
    with patch.object(pluto_app.webull_api, "get_order_history", return_value=[_history_row(other_users_id)]):
        discovered = pluto_app._discover_orphaned_broker_entries(user_id, CREDS, ACCOUNT_ID)
    assert discovered == 0
    assert list_overnight_orders(user_id) == []


def test_ignores_an_order_without_this_apps_id_prefix(user_id):
    """A human-placed or third-party order sharing this account must never
    be swept up as an orphan just because it's a BUY with no local
    record."""
    with patch.object(pluto_app.webull_api, "get_order_history", return_value=[_history_row("some-other-random-uuid")]):
        discovered = pluto_app._discover_orphaned_broker_entries(user_id, CREDS, ACCOUNT_ID)
    assert discovered == 0
    assert list_overnight_orders(user_id) == []


def test_ignores_an_order_already_known_locally_even_if_terminal(user_id):
    known_id = _orphan_id(user_id)
    entry = {"ticker": "AAPL", "quantity": 10, "limit_price": 100.0, "trading_day": "2026-08-11"}
    ol.initialize(entry, ol.ENTRY_SUBMITTED, entry_client_order_id=known_id)
    ol.transition(entry, ol.ENTRY_FAILED, error="rejected", filled_quantity=0)
    record_overnight_order(user_id, entry)

    with patch.object(pluto_app.webull_api, "get_order_history", return_value=[_history_row(known_id)]):
        discovered = pluto_app._discover_orphaned_broker_entries(user_id, CREDS, ACCOUNT_ID)

    assert discovered == 0
    assert len(list_overnight_orders(user_id)) == 1  # still just the one, pre-existing record


def test_idempotent_across_repeated_discovery_passes(user_id):
    orphan_id = _orphan_id(user_id)
    with patch.object(pluto_app.webull_api, "get_order_history", return_value=[_history_row(orphan_id)]):
        first = pluto_app._discover_orphaned_broker_entries(user_id, CREDS, ACCOUNT_ID)
        second = pluto_app._discover_orphaned_broker_entries(user_id, CREDS, ACCOUNT_ID)

    assert first == 1
    assert second == 0  # already known now - not rediscovered/duplicated
    assert len(list_overnight_orders(user_id)) == 1


def test_malformed_orphan_row_is_not_recorded_but_still_alerts(user_id):
    """A row with a REAL, confirmed-attributed id but an unparseable
    quantity - can't safely build a record, but the alert must still
    fire since attribution WAS confirmed."""
    orphan_id = _orphan_id(user_id)
    bad_row = _history_row(orphan_id, quantity=0)
    with patch.object(pluto_app.webull_api, "get_order_history", return_value=[bad_row]):
        discovered = pluto_app._discover_orphaned_broker_entries(user_id, CREDS, ACCOUNT_ID)

    assert discovered == 0
    assert list_overnight_orders(user_id) == []  # never fabricated a garbage record
    from alerts import load_manual_alerts
    alerts = [a for a in load_manual_alerts(user_id) if a.get("type") == "orphan_entry_could_not_be_recovered"]
    assert len(alerts) == 1
    assert alerts[0]["priority"] == "critical"


def test_a_row_with_no_symbol_is_never_attributed_or_alerted(user_id):
    """Without a ticker, attribution cannot even be attempted (the ticker
    feeds the hash) - correctly treated as "unknown whether this is
    ours", not "ours but malformed", so no alert fires either."""
    bad_row = _history_row("pt" + "z" * 30, symbol="", quantity=0)
    with patch.object(pluto_app.webull_api, "get_order_history", return_value=[bad_row]):
        discovered = pluto_app._discover_orphaned_broker_entries(user_id, CREDS, ACCOUNT_ID)
    assert discovered == 0
    assert list_overnight_orders(user_id) == []
    from alerts import load_manual_alerts
    assert load_manual_alerts(user_id) == []


def test_history_lookup_failure_is_best_effort_not_fatal(user_id):
    with patch.object(pluto_app.webull_api, "get_order_history", side_effect=RuntimeError("broker unreachable")):
        discovered = pluto_app._discover_orphaned_broker_entries(user_id, CREDS, ACCOUNT_ID)
    assert discovered == 0
    assert list_overnight_orders(user_id) == []


def test_one_bad_row_does_not_block_discovering_a_good_one(user_id):
    bad_id = _orphan_id(user_id, ticker="TSLA")
    good_id = _orphan_id(user_id, ticker="MSFT")
    rows = [
        _history_row(bad_id, symbol="TSLA", quantity=0),  # malformed
        _history_row(good_id, symbol="MSFT"),  # good
    ]
    with patch.object(pluto_app.webull_api, "get_order_history", return_value=rows):
        discovered = pluto_app._discover_orphaned_broker_entries(user_id, CREDS, ACCOUNT_ID)
    assert discovered == 1
    orders = list_overnight_orders(user_id)
    assert len(orders) == 1
    assert orders[0]["ticker"] == "MSFT"


# --- Wiring into the full scan --------------------------------------------------


def test_full_scan_calls_orphan_discovery_before_reconciling_unknown_submissions(user_id):
    call_order = []

    def _track(name):
        def _fn(*args, **kwargs):
            call_order.append(name)
            return 0 if name == "discover" else False
        return _fn

    with patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app, "is_webull_configured", return_value=True), \
         patch.object(pluto_app, "get_accounts", return_value=[{"platform": "webull", "status": "Connected"}]), \
         patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=[{"account_id": ACCOUNT_ID}]), \
         patch.object(pluto_app.webull_api, "find_individual_cash_account", return_value={"account_id": ACCOUNT_ID}), \
         patch.object(pluto_app, "_reconcile_exit_orders"), \
         patch.object(pluto_app, "_refresh_stop_confidence"), \
         patch.object(pluto_app, "_discover_orphaned_broker_entries", side_effect=_track("discover")) as mock_discover, \
         patch.object(pluto_app, "_reconcile_unknown_submissions", side_effect=_track("reconcile_unknown")), \
         patch.object(pluto_app, "_recover_incomplete_manual_resolutions", return_value=False), \
         patch.object(pluto_app, "_monitor_transitional_orders", return_value=False), \
         patch.object(pluto_app.webull_api, "get_account_balance", return_value={"total_net_liquidation_value": 100000.0, "total_day_profit_loss": 0.0, "account_currency_assets": [{"buying_power": "1000000"}]}), \
         patch.object(pluto_app, "_build_page_context", return_value={"upcoming_opportunities": []}):
        try:
            pluto_app._run_autonomous_trade_scan_locked(user_id)
        except Exception:
            pass  # only the call ORDER matters here, not a full successful scan

    mock_discover.assert_called_once()
    assert call_order[:2] == ["discover", "reconcile_unknown"]


# --- Wiring into the fast safety monitor, regardless of autonomy/local state ----


def test_fast_monitor_endpoint_discovers_an_orphan_with_autonomy_off_and_zero_local_state(user_id):
    """The literal restart scenario this was built for: a crash left a
    broker-accepted entry with NO local record at all, autonomy is OFF
    (so the full scan's own new-entry work wouldn't run anyway - this
    must not matter for discovery), and _user_needs_fast_monitor_pass
    would return False on its own (nothing local to find) - yet the fast
    monitor endpoint must still discover it, since local state alone
    cannot identify a missing local write."""
    assert list_overnight_orders(user_id) == []  # genuinely zero local state
    assert pluto_app._user_needs_fast_monitor_pass(user_id) is False  # confirms the gate WOULD skip this user on its own

    orphan_id = _orphan_id(user_id)
    with patch.object(pluto_app, "list_all_user_ids", return_value=[user_id]), \
         patch.object(pluto_app, "get_autonomy_status", return_value={"current_mode": "OFF"}), \
         patch.object(pluto_app, "is_webull_configured", return_value=True), \
         patch.object(pluto_app, "get_webull_credentials", return_value=CREDS), \
         patch.object(pluto_app, "get_accounts", return_value=[{"platform": "webull", "status": "Connected"}]), \
         patch.object(pluto_app.webull_api, "get_paper_accounts", return_value=[{"account_id": ACCOUNT_ID}]), \
         patch.object(pluto_app.webull_api, "find_individual_cash_account", return_value={"account_id": ACCOUNT_ID}), \
         patch.object(pluto_app.webull_api, "get_order_history", return_value=[_history_row(orphan_id)]), \
         patch.object(pluto_app, "_reconcile_exit_orders"), \
         patch.object(pluto_app, "_reconcile_unknown_submissions", return_value=False), \
         patch.object(pluto_app, "_recover_incomplete_manual_resolutions", return_value=False), \
         patch.object(pluto_app, "_monitor_transitional_orders", return_value=False):
        with pluto_app.app.test_client() as client:
            response = client.post(
                "/api/autonomy/fast-monitor-trigger",
                headers={"X-Cron-Secret": os.environ.get("CRON_SECRET", "")},
            )

    assert response.status_code == 200
    orders = list_overnight_orders(user_id)
    assert len(orders) == 1
    assert orders[0]["entry_client_order_id"] == orphan_id
    assert orders[0]["lifecycle_state"] == ol.UNKNOWN_SUBMISSION_STATE
    assert orders[0]["orphan_recovered"] is True
    # Discovered DESPITE autonomy OFF and zero starting local state -
    # exactly the restart scenario this endpoint change exists for.
    assert pluto_app._has_unresolved_ambiguous_submission_locally(user_id) is True
