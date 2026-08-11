from __future__ import annotations

import webull_credentials as wc


def test_virtual_balance_none_before_first_sync(user_id):
    # No seed_balance recorded yet (never connected/synced) - callers must
    # fall back to the real Webull balance rather than showing a wrong
    # virtual number computed from a missing baseline.
    assert wc.get_virtual_net_account_value(user_id, 100000.0) is None


def test_virtual_balance_defaults_to_2000_starting():
    assert wc.DEFAULT_VIRTUAL_STARTING_BALANCE == 2000.0


def test_virtual_balance_tracks_unrealized_gain_after_seed(user_id):
    wc.record_seed_balance_if_unset(user_id, 100000.0)  # Webull's real inflated sandbox seed
    # Real balance grows by $500 (e.g. an open position's unrealized gain).
    assert wc.get_virtual_net_account_value(user_id, 100500.0) == 2000.0 + 500.0


def test_virtual_balance_reset_with_an_open_position_rebases_but_keeps_unrealized_pnl(user_id):
    """Documents the CURRENT behavior of set_virtual_starting_balance, which
    is not wired to any route or UI control (grepped templates/*.html and
    app.js - nothing calls it, so "reset virtual balance" is not actually
    reachable by a user today, despite being described as a live feature
    elsewhere). This test locks in what the underlying math does if/when
    that gets wired up, so the behavior is a deliberate choice rather than
    whatever the arithmetic happens to produce.

    set_virtual_starting_balance only changes the baseline - it does NOT
    touch seed_balance. So "resetting" while a position is open does not
    discard that position's unrealized P&L; it rebases it onto the new
    starting number instead. Whether that's the intended product behavior,
    versus a hard reset that also re-seeds to the current real balance and
    drops unrealized P&L, is a real design decision that hasn't been made -
    this test exists so that decision is explicit, not accidental.
    """
    wc.record_seed_balance_if_unset(user_id, 100000.0)
    # Position open, real balance up $500 unrealized.
    assert wc.get_virtual_net_account_value(user_id, 100500.0) == 2000.0 + 500.0

    # User "resets" their virtual starting balance to $3,000 while that
    # position is still open (real balance still 100500, unchanged).
    wc.set_virtual_starting_balance(user_id, 3000.0)

    # The $500 unrealized gain survives the reset, rebased onto the new
    # starting balance - not discarded, and not double-counted.
    assert wc.get_virtual_net_account_value(user_id, 100500.0) == 3000.0 + 500.0


def test_virtual_starting_balance_must_be_positive(user_id):
    try:
        wc.set_virtual_starting_balance(user_id, 0)
        assert False, "expected ValueError for a zero starting balance"
    except ValueError:
        pass
    try:
        wc.set_virtual_starting_balance(user_id, -100)
        assert False, "expected ValueError for a negative starting balance"
    except ValueError:
        pass


def test_seed_balance_resets_on_app_key_change_but_not_on_same_key_resave(user_id):
    wc.set_webull_credentials(user_id, "key-one", "secret-one")
    wc.record_seed_balance_if_unset(user_id, 100000.0)
    assert wc.get_virtual_net_account_value(user_id, 100500.0) == 2000.0 + 500.0

    # Re-saving the SAME key (e.g. re-entering an unchanged secret) must not
    # wipe the seed - covered again here because this was an actual bug
    # introduced (and caught) while adding credential encryption: comparing
    # the newly-encrypted value directly against the plaintext input always
    # looked like a "change" until the comparison was fixed to decrypt first.
    wc.set_webull_credentials(user_id, "key-one", "secret-one")
    assert wc.get_virtual_net_account_value(user_id, 100500.0) == 2000.0 + 500.0

    # Switching to a genuinely different app key points at a different
    # sandbox account - the old seed no longer means anything and must be
    # dropped so it doesn't silently corrupt the new account's virtual math.
    wc.set_webull_credentials(user_id, "key-two", "secret-two")
    assert wc.get_virtual_net_account_value(user_id, 999999.0) is None
