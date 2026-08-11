from __future__ import annotations

import logging

from integrations import webull as webull_api


def _filtered_message(raw_message: str) -> str:
    record = logging.LogRecord(
        name="webull.core", level=logging.INFO, pathname=__file__, lineno=1, msg=raw_message, args=(), exc_info=None
    )
    webull_api._RedactSensitiveWebullFields().filter(record)
    return record.getMessage()


def test_redacts_signature_and_app_key():
    raw = (
        '{"x-app-key": "abc123app", "x-signature": "C6TOaQqFhT1gpntbNjFv8UAqK5mmRAfKoUsaxXfeUaU=", '
        '"x-signature-nonce": "some-nonce"}'
    )
    redacted = _filtered_message(raw)
    assert "abc123app" not in redacted
    assert "C6TOaQqFhT1gpntbNjFv8UAqK5mmRAfKoUsaxXfeUaU" not in redacted
    assert "***REDACTED***" in redacted


def test_leaves_non_sensitive_fields_untouched():
    raw = '{"symbol": "AAPL", "quantity": 10, "x-signature-algorithm": "HMAC-SHA256"}'
    redacted = _filtered_message(raw)
    assert '"symbol": "AAPL"' in redacted
    assert '"quantity": 10' in redacted


def test_message_without_sensitive_fields_passes_through_unchanged():
    raw = "Order placed successfully for AAPL"
    assert _filtered_message(raw) == raw


def test_get_trade_client_configures_logging_exactly_once():
    webull_api._webull_sdk_logging_configured = False
    sdk_logger = logging.getLogger("webull.core")
    handlers_before = list(sdk_logger.handlers)

    try:
        webull_api._get_trade_client("fake-app-key", "fake-app-secret")
        webull_api._get_trade_client("fake-app-key", "fake-app-secret")
        webull_api._get_trade_client("fake-app-key", "fake-app-secret")
    except Exception:
        pass

    # Only count FileHandlers - pytest's own log-capture machinery attaches
    # its own handler (e.g. a NullHandler) to loggers during test runs,
    # which isn't something webull.py added and isn't what this is testing.
    new_file_handlers = [h for h in sdk_logger.handlers if h not in handlers_before and isinstance(h, logging.FileHandler)]
    assert len(new_file_handlers) == 1, f"expected exactly one new file handler across 3 calls, got {len(new_file_handlers)}"


def test_real_sdk_call_does_not_leak_credentials_to_the_log_file():
    """End-to-end: trigger an actual (network-failing) SDK call with a
    distinctive fake credential and confirm it never lands in the log file
    the SDK writes to - this is what caught the original propagation bug,
    where a logger-level filter missed records from the SDK's child loggers."""
    webull_api._webull_sdk_logging_configured = False
    marker_key = "MARKER-APP-KEY-CANARY-6f2a"
    marker_secret = "MARKER-APP-SECRET-CANARY-9d1c"

    try:
        webull_api._get_trade_client(marker_key, marker_secret)
    except Exception:
        pass

    for handler in logging.getLogger("webull.core").handlers:
        if isinstance(handler, logging.FileHandler):
            handler.flush()
            log_path = handler.baseFilename
            break
    else:
        raise AssertionError("expected a FileHandler to have been installed")

    with open(log_path, encoding="utf-8") as f:
        content = f.read()
    assert marker_key not in content, "app key leaked into the log file"
    assert marker_secret not in content, "app secret leaked into the log file"
