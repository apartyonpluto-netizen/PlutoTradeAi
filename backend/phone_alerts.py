"""Phone alerts for fills, stops, halts, and errors.

Off unless a channel is configured. Nothing here is sent, and no URL, token,
or message body is written to logs.

  PLUTO_PUSHOVER_USER + PLUTO_PUSHOVER_TOKEN
      Pushover (https://api.pushover.net/1/messages.json).
  PLUTO_ALERT_WEBHOOK_URL
      POST. If PLUTO_ALERT_CHANNEL=ntfy, or the URL contains "ntfy", the body
      is plain text with a Title header (ntfy.sh). Otherwise the body is JSON
      {"event", "title", "message"} so an email or SMS gateway can accept it.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

_FILL = ("fill", "filled", "position_closed")
_STOP = ("stop",)
_HALT = ("halt", "emergency_stop", "daily_loss", "losing_streak", "kill_switch")
_ERROR = ("error", "failed", "unprotected", "ambiguous")


def channel() -> Optional[str]:
    """Which channel would send, or None when alerts are off."""
    user = os.environ.get("PLUTO_PUSHOVER_USER", "").strip()
    token = os.environ.get("PLUTO_PUSHOVER_TOKEN", "").strip()
    if user and token:
        return "pushover"
    if os.environ.get("PLUTO_ALERT_WEBHOOK_URL", "").strip():
        style = os.environ.get("PLUTO_ALERT_CHANNEL", "").strip().lower()
        url = os.environ.get("PLUTO_ALERT_WEBHOOK_URL", "").lower()
        if style == "ntfy" or "ntfy" in url:
            return "ntfy"
        return "webhook"
    return None


def configured() -> bool:
    return channel() is not None


def classify(alert_type: str, priority: str = "normal") -> Optional[str]:
    """fill, stop, halt, or error. None means this alert stays in the app only."""
    kind = (alert_type or "").strip().lower()
    priority = (priority or "normal").strip().lower()
    if any(token in kind for token in _HALT):
        return "halt"
    if any(token in kind for token in _ERROR) or priority == "critical":
        return "error"
    if any(token in kind for token in _STOP):
        return "stop"
    if any(token in kind for token in _FILL):
        return "fill"
    return None


def notify(event: str, title: str, message: str, *, opener=None, actions: Optional[str] = None) -> bool:
    """Sends one alert. Returns False when unconfigured or the send fails.
    Failures are swallowed: a phone outage must not change trading."""
    kind = channel()
    if kind is None:
        return False
    text = f"{title}: {message}".strip(": ")
    try:
        if kind == "pushover":
            _post_form(
                "https://api.pushover.net/1/messages.json",
                {
                    "token": os.environ.get("PLUTO_PUSHOVER_TOKEN", "").strip(),
                    "user": os.environ.get("PLUTO_PUSHOVER_USER", "").strip(),
                    "title": title[:250],
                    "message": message[:1024],
                    "priority": "1" if event in ("halt", "error") else "0",
                },
                opener,
            )
        elif kind == "ntfy":
            _post_bytes(
                os.environ.get("PLUTO_ALERT_WEBHOOK_URL", "").strip(),
                message[:1024].encode("utf-8"),
                {
                    "Title": title[:250],
                    "Priority": "high" if event in ("halt", "error") else "default",
                    **({"Actions": actions} if actions else {}),
                },
                opener,
            )
        else:
            body = json.dumps({"event": event, "title": title[:250], "message": message[:1024]}).encode("utf-8")
            _post_bytes(
                os.environ.get("PLUTO_ALERT_WEBHOOK_URL", "").strip(),
                body,
                {"Content-Type": "application/json"},
                opener,
            )
        return True
    except Exception:
        return False


def notify_alert(alert: Dict[str, Any], *, opener=None) -> bool:
    # Exit phones are the richer message from exit_alerts, once per close.
    if "position_closed" in str(alert.get("type") or "").lower():
        return False
    event = classify(str(alert.get("type") or ""), str(alert.get("priority") or "normal"))
    if event is None:
        return False
    ticker = str(alert.get("ticker") or "").strip()
    title = f"{event}: {ticker}" if ticker else event
    return notify(event, title, str(alert.get("message") or ""), opener=opener)


def _post_form(url: str, fields: Dict[str, str], opener) -> None:
    payload = urllib.parse.urlencode(fields).encode("utf-8")
    _post_bytes(url, payload, {"Content-Type": "application/x-www-form-urlencoded"}, opener)


def _post_bytes(url: str, payload: bytes, headers: Dict[str, str], opener) -> None:
    request = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    open_url = opener or urllib.request.urlopen
    with open_url(request, timeout=5) as response:
        response.read()

