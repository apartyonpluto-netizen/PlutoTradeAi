from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import wraps
import logging
from typing import Any, Dict, Optional

from flask import Flask, jsonify


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class PlutoTradeError(Exception):
    message: str
    status_code: int = 400
    error_code: str = "plutotrade_error"
    details: Dict[str, Any] = field(default_factory=dict)

    def to_api_error(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "message": self.message,
            "code": self.error_code,
            "details": self.details,
            "timestamp": _timestamp(),
        }
        return payload


class ValidationError(PlutoTradeError):
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(
            message=message,
            status_code=400,
            error_code="validation_error",
            details=details or {},
        )


class IntegrationError(PlutoTradeError):
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(
            message=message,
            status_code=502,
            error_code="integration_error",
            details=details or {},
        )


class ServiceUnavailableError(PlutoTradeError):
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(
            message=message,
            status_code=503,
            error_code="service_unavailable",
            details=details or {},
        )


def api_response(
    *,
    success: bool,
    data: Optional[Dict[str, Any]] = None,
    error: Optional[Dict[str, Any]] = None,
    status_code: int = 200,
):
    payload = {
        "success": success,
        "ok": success,
        "data": data or {},
        "error": error,
        "timestamp": _timestamp(),
    }
    return jsonify(payload), status_code


def handle_api_errors(logger: logging.Logger):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except PlutoTradeError as error:
                logger.warning(
                    "PlutoTradeError in %s: %s (%s)",
                    func.__name__,
                    error.message,
                    error.error_code,
                )
                return api_response(success=False, error=error.to_api_error(), status_code=error.status_code)
            except ValueError as error:
                logger.warning("Validation error in %s: %s", func.__name__, str(error))
                wrapped = ValidationError(str(error))
                return api_response(success=False, error=wrapped.to_api_error(), status_code=wrapped.status_code)
            except Exception:
                logger.exception("Unhandled API error in %s", func.__name__)
                generic = ServiceUnavailableError(
                    "Something went wrong on the server. Please retry shortly.",
                    details={"user_friendly": True},
                )
                return api_response(success=False, error=generic.to_api_error(), status_code=generic.status_code)

        return wrapper

    return decorator


def register_error_handlers(app: Flask, logger: logging.Logger) -> None:
    @app.errorhandler(404)
    def not_found(error):  # type: ignore[unused-argument]
        return api_response(
            success=False,
            error={
                "message": "The requested resource was not found.",
                "code": "not_found",
                "details": {},
            },
            status_code=404,
        )

    @app.errorhandler(405)
    def method_not_allowed(error):  # type: ignore[unused-argument]
        return api_response(
            success=False,
            error={
                "message": "Method not allowed for this endpoint.",
                "code": "method_not_allowed",
                "details": {},
            },
            status_code=405,
        )

    logger.info("API error handlers registered.")
