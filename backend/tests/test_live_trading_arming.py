from __future__ import annotations

import os
from unittest.mock import patch

import app as pluto_app
from autonomy import autonomous_controller as controller
from integrations import webull as webull_api

"""Real-money live trading is off (sandbox) unless BOTH
PLUTO_WEBULL_TRADING_ENVIRONMENT=live and PLUTO_LIVE_TRADING_CONFIRMATION
are set together in the process environment - see is_live_trading_armed's
own docstring for why two independent env vars rather than one. These
tests prove: (1) is_live_trading_armed only returns True for that exact
combination, (2) _get_trade_client/_get_data_client route to the live vs.
sandbox host based on it, and (3) get_autonomy_status's live_trading_locked
is never out of sync with it."""

ENV_VAR = webull_api._LIVE_TRADING_ENVIRONMENT_VAR
CONFIRMATION_VAR = webull_api._LIVE_TRADING_CONFIRMATION_VAR
CONFIRMATION_VALUE = webull_api._LIVE_TRADING_CONFIRMATION_VALUE


def _clear_env():
    os.environ.pop(ENV_VAR, None)
    os.environ.pop(CONFIRMATION_VAR, None)


def test_defaults_to_not_armed_with_no_env_vars_set():
    _clear_env()
    assert webull_api.is_live_trading_armed() is False
    assert webull_api._resolve_endpoint() == webull_api._SANDBOX_ENDPOINT


def test_environment_var_alone_is_not_enough():
    _clear_env()
    os.environ[ENV_VAR] = "live"
    try:
        assert webull_api.is_live_trading_armed() is False
    finally:
        _clear_env()


def test_confirmation_var_alone_is_not_enough():
    _clear_env()
    os.environ[CONFIRMATION_VAR] = CONFIRMATION_VALUE
    try:
        assert webull_api.is_live_trading_armed() is False
    finally:
        _clear_env()


def test_wrong_confirmation_value_does_not_arm():
    _clear_env()
    os.environ[ENV_VAR] = "live"
    os.environ[CONFIRMATION_VAR] = "yes please"
    try:
        assert webull_api.is_live_trading_armed() is False
    finally:
        _clear_env()


def test_both_env_vars_set_correctly_arms_live_trading():
    _clear_env()
    os.environ[ENV_VAR] = "live"
    os.environ[CONFIRMATION_VAR] = CONFIRMATION_VALUE
    try:
        assert webull_api.is_live_trading_armed() is True
        assert webull_api._resolve_endpoint() == webull_api._LIVE_ENDPOINT
    finally:
        _clear_env()


def test_get_trade_client_adds_the_live_endpoint_when_armed():
    _clear_env()
    os.environ[ENV_VAR] = "live"
    os.environ[CONFIRMATION_VAR] = CONFIRMATION_VALUE
    app_key, app_secret = "live-arm-test-key", "live-arm-test-secret"
    added_endpoints = []
    try:
        with patch(
            "webull.core.http.initializer.client_initializer.ClientInitializer.initializer",
            return_value=None,
        ), patch(
            "webull.core.client.ApiClient.add_endpoint",
            side_effect=lambda region, endpoint: added_endpoints.append(endpoint),
        ):
            webull_api._get_trade_client(app_key, app_secret)
        assert added_endpoints == [webull_api._LIVE_ENDPOINT]
    finally:
        _clear_env()
        webull_api._trade_client_cache.pop((app_key, app_secret, webull_api._LIVE_ENDPOINT), None)


def test_get_autonomy_status_live_trading_locked_tracks_the_real_gate(user_id):
    _clear_env()
    try:
        status = controller.get_autonomy_status(user_id)
        assert status["live_trading_locked"] is True

        os.environ[ENV_VAR] = "live"
        os.environ[CONFIRMATION_VAR] = CONFIRMATION_VALUE
        status = controller.get_autonomy_status(user_id)
        assert status["live_trading_locked"] is False
    finally:
        _clear_env()


def test_emergency_stop_relocks_live_trading_even_when_armed(user_id):
    _clear_env()
    os.environ[ENV_VAR] = "live"
    os.environ[CONFIRMATION_VAR] = CONFIRMATION_VALUE
    try:
        assert controller.get_autonomy_status(user_id)["live_trading_locked"] is False
        controller.emergency_stop(user_id, reason="test")
        assert controller.get_autonomy_status(user_id)["live_trading_locked"] is True
    finally:
        _clear_env()


def test_dashboard_and_status_payloads_report_armed_state(user_id):
    _clear_env()
    try:
        assert pluto_app._broker_framework_status()["safety_defaults"]["live_trading_enabled"] is False

        os.environ[ENV_VAR] = "live"
        os.environ[CONFIRMATION_VAR] = CONFIRMATION_VALUE
        assert pluto_app._broker_framework_status()["safety_defaults"]["live_trading_enabled"] is True
    finally:
        _clear_env()
