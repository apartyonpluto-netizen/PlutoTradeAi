from __future__ import annotations

import pytest

from autonomy import autonomous_controller as controller

"""Options-specific risk settings (2026-09-03) - option_target_gain_percent/
option_stop_loss_percent/option_close_days_before_expiration, added to the
same per-user autonomy_settings.json store update_risk_settings already
manages (not a new store), since a long option has no stop-distance the
way equity does - its exits are premium-percentage-based instead."""


def test_defaults_present_without_ever_calling_update_risk_settings(user_id):
    status = controller.get_autonomy_status(user_id)
    assert status["option_target_gain_percent"] == 50.0
    assert status["option_stop_loss_percent"] == 50.0
    assert status["option_close_days_before_expiration"] == 3


def test_update_risk_settings_can_change_option_thresholds(user_id):
    result = controller.update_risk_settings(
        user_id,
        option_target_gain_percent=75.0,
        option_stop_loss_percent=40.0,
        option_close_days_before_expiration=2,
    )
    assert result["option_target_gain_percent"] == 75.0
    assert result["option_stop_loss_percent"] == 40.0
    assert result["option_close_days_before_expiration"] == 2
    # Persisted, not just returned - a fresh read sees the same values.
    assert controller.get_autonomy_status(user_id)["option_target_gain_percent"] == 75.0


def test_update_risk_settings_leaves_option_fields_untouched_when_not_passed(user_id):
    controller.update_risk_settings(user_id, option_target_gain_percent=80.0)
    result = controller.update_risk_settings(user_id, risk_percent_of_balance=3.0)
    assert result["option_target_gain_percent"] == 80.0


def test_option_target_gain_percent_rejects_zero_or_negative(user_id):
    with pytest.raises(ValueError):
        controller.update_risk_settings(user_id, option_target_gain_percent=0)
    with pytest.raises(ValueError):
        controller.update_risk_settings(user_id, option_target_gain_percent=-10)


def test_option_stop_loss_percent_rejects_zero_and_over_100(user_id):
    with pytest.raises(ValueError):
        controller.update_risk_settings(user_id, option_stop_loss_percent=0)
    with pytest.raises(ValueError):
        controller.update_risk_settings(user_id, option_stop_loss_percent=101)


def test_option_stop_loss_percent_allows_exactly_100(user_id):
    # 100% stop = "close if the option's value goes to zero" - the natural
    # maximum for a long option, not an out-of-range value.
    result = controller.update_risk_settings(user_id, option_stop_loss_percent=100)
    assert result["option_stop_loss_percent"] == 100.0


def test_option_close_days_before_expiration_rejects_negative(user_id):
    with pytest.raises(ValueError):
        controller.update_risk_settings(user_id, option_close_days_before_expiration=-1)


def test_option_close_days_before_expiration_allows_zero(user_id):
    result = controller.update_risk_settings(user_id, option_close_days_before_expiration=0)
    assert result["option_close_days_before_expiration"] == 0
