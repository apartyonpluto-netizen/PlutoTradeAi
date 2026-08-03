from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Sequence

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
ALERTS_FILE = DATA_DIR / "alerts.json"
DISMISSED_FILE = DATA_DIR / "dismissed_alerts.json"
READ_FILE = DATA_DIR / "read_alerts.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_alert_id(alert_type: str, ticker: str, message: str) -> str:
    digest = hashlib.sha1(f"{alert_type}|{ticker}|{message}".encode("utf-8")).hexdigest()[:12]
    return f"{alert_type}-{ticker or 'global'}-{digest}"


def _ensure_files() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not ALERTS_FILE.exists():
        ALERTS_FILE.write_text("[]", encoding="utf-8")
    if not DISMISSED_FILE.exists():
        DISMISSED_FILE.write_text("[]", encoding="utf-8")
    if not READ_FILE.exists():
        READ_FILE.write_text("[]", encoding="utf-8")


def _load_json(path: Path) -> List[Dict[str, str]]:
    _ensure_files()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        return []
    return []


def _save_json(path: Path, payload: List[Dict[str, str]]) -> None:
    _ensure_files()
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_manual_alerts() -> List[Dict[str, str]]:
    return _load_json(ALERTS_FILE)


def add_manual_alert(payload: Dict[str, str]) -> Dict[str, str]:
    alert_type = (payload.get("type", "manual") or "manual").strip().lower()
    ticker = (payload.get("ticker", "") or "").strip().upper()
    message = (payload.get("message", "") or "").strip()
    if not message:
        raise ValueError("Alert message is required.")

    alert = {
        "id": _build_alert_id(alert_type=alert_type, ticker=ticker, message=message),
        "type": alert_type,
        "ticker": ticker,
        "message": message,
        "source": "manual",
        "created_at": _now_iso(),
    }

    alerts = load_manual_alerts()
    if not any(existing["id"] == alert["id"] for existing in alerts):
        alerts.insert(0, alert)
        _save_json(ALERTS_FILE, alerts)
    return alert


def dismiss_alert(alert_id: str) -> None:
    if not alert_id:
        raise ValueError("Alert ID is required.")
    dismissed = _load_json(DISMISSED_FILE)
    if not any(item.get("id") == alert_id for item in dismissed):
        dismissed.append({"id": alert_id, "dismissed_at": _now_iso()})
        _save_json(DISMISSED_FILE, dismissed)


def dismiss_alerts(alert_ids: Sequence[str]) -> int:
    valid_ids = [alert_id for alert_id in alert_ids if alert_id]
    if not valid_ids:
        raise ValueError("At least one alert ID is required.")

    dismissed = _load_json(DISMISSED_FILE)
    existing_ids = {item.get("id", "") for item in dismissed}
    newly_added = 0
    for alert_id in valid_ids:
        if alert_id in existing_ids:
            continue
        dismissed.append({"id": alert_id, "dismissed_at": _now_iso()})
        existing_ids.add(alert_id)
        newly_added += 1
    if newly_added:
        _save_json(DISMISSED_FILE, dismissed)
    return newly_added


def _dismissed_ids() -> set[str]:
    return {item.get("id", "") for item in _load_json(DISMISSED_FILE)}


def _read_ids() -> set[str]:
    return {item.get("id", "") for item in _load_json(READ_FILE)}


def _category_for_type(alert_type: str) -> str:
    mapping = {
        "watchlist-suggestion": "Watchlist",
        "volume-spike": "Market Scanner",
        "breakout-alert": "AI",
        "reversal-zone": "AI",
        "news-alert": "News",
        "manual": "System",
        "broker-status": "Broker",
        "system-status": "System",
        "mission-alert": "Mission Alert",
        "economic-event": "Economic Event",
        "tradingview-alert": "TradingView",
    }
    return mapping.get(alert_type, "System")


def build_system_alerts(
    suggestions: Sequence[Dict[str, str]],
    scanner_rows: Sequence[Dict[str, str]],
    reversal_rows: Sequence[Dict[str, str]],
    trend_rows: Sequence[Dict[str, str]],
    news_rows: Sequence[Dict[str, str]],
    opportunities: Sequence[Dict[str, object]] | None = None,
    market_phase: str = "",
    tradingview_alert: Dict[str, object] | None = None,
) -> List[Dict[str, str]]:
    alerts: List[Dict[str, str]] = []

    for suggestion in suggestions[:5]:
        ticker = suggestion.get("ticker", "")
        message = suggestion.get("notes", "Watchlist suggestion")
        alerts.append(
            {
                "id": _build_alert_id("watchlist-suggestion", ticker, message),
                "type": "watchlist-suggestion",
                "ticker": ticker,
                "message": message,
                "source": "system",
                "created_at": _now_iso(),
            }
        )

    for row in scanner_rows:
        if float(row.get("relative_volume", 0)) < 2.0:
            continue
        ticker = row.get("ticker", "")
        message = f"Volume spike detected: {row.get('relative_volume')}x relative volume."
        alerts.append(
            {
                "id": _build_alert_id("volume-spike", ticker, message),
                "type": "volume-spike",
                "ticker": ticker,
                "message": message,
                "source": "system",
                "created_at": _now_iso(),
            }
        )

    for item in (opportunities or [])[:5]:
        ticker = str(item.get("ticker", "")).upper()
        recommendation = str(item.get("recommendation", "WAIT")).upper()
        confidence = int(item.get("confidence", 0) or 0)
        if recommendation == "WAIT" or confidence < 70:
            continue
        level = item.get("ideal_entry", item.get("current_price", "n/a"))
        message = f"{ticker} entered ideal entry zone near {level}. {recommendation} setup confidence {confidence}%."
        alerts.append(
            {
                "id": _build_alert_id("mission-alert", ticker, message),
                "type": "mission-alert",
                "ticker": ticker,
                "message": message,
                "source": "system",
                "created_at": _now_iso(),
            }
        )

    reversal_by_ticker = {row["ticker"]: row for row in reversal_rows}
    for trend in trend_rows:
        ticker = trend.get("ticker", "")
        if trend.get("breakout_forming"):
            message = "Breakout is forming near resistance."
            alerts.append(
                {
                    "id": _build_alert_id("breakout-alert", ticker, message),
                    "type": "breakout-alert",
                    "ticker": ticker,
                    "message": message,
                    "source": "system",
                    "created_at": _now_iso(),
                }
            )
        if trend.get("candle_reversal_near_support_resistance"):
            reversal = reversal_by_ticker.get(ticker, {})
            message = f"Candle reversal near zone {reversal.get('reversal_watch_zone', 'support/resistance')}."
            alerts.append(
                {
                    "id": _build_alert_id("reversal-zone", ticker, message),
                    "type": "reversal-zone",
                    "ticker": ticker,
                    "message": message,
                    "source": "system",
                    "created_at": _now_iso(),
                }
            )

    if market_phase == "Premarket":
        message = "Premarket intelligence active. Monitoring potential opening breakouts and reversals."
        alerts.append(
            {
                "id": _build_alert_id("economic-event", "MARKET", message),
                "type": "economic-event",
                "ticker": "MARKET",
                "message": message,
                "source": "system",
                "created_at": _now_iso(),
            }
        )
    if market_phase == "After Hours":
        message = "After-hours analysis active. Gap probability and tomorrow watchlist are updating."
        alerts.append(
            {
                "id": _build_alert_id("economic-event", "MARKET", message),
                "type": "economic-event",
                "ticker": "MARKET",
                "message": message,
                "source": "system",
                "created_at": _now_iso(),
            }
        )

    if tradingview_alert:
        symbol = str(tradingview_alert.get("ticker", "") or "").upper()
        signal = str(tradingview_alert.get("signal", "") or "").upper()
        raw_message = str(tradingview_alert.get("raw_message", "") or "").strip()
        if symbol and signal and signal != "WAIT":
            action = f"{signal} signal for {symbol}"
        elif raw_message:
            action = raw_message[:140]
        elif symbol:
            action = f"Signal received for {symbol}"
        else:
            action = "Signal received (no ticker/signal parsed from payload)"
        message = f"TradingView alert: {action}"
        alerts.append(
            {
                "id": _build_alert_id("tradingview-alert", symbol, message),
                "type": "tradingview-alert",
                "ticker": symbol,
                "message": message,
                "source": "tradingview",
                "created_at": tradingview_alert.get("received_at", _now_iso()),
            }
        )

    for news in news_rows[:8]:
        ticker = (news.get("ticker", "") or "").upper()
        sentiment = news.get("sentiment", "neutral")
        message = f"X alert ({sentiment}): {news.get('text', '')[:140]}"
        alerts.append(
            {
                "id": _build_alert_id("news-alert", ticker, message),
                "type": "news-alert",
                "ticker": ticker,
                "message": message,
                "source": "x-news",
                "created_at": news.get("created_at", _now_iso()),
            }
        )

    return alerts


def get_alerts_snapshot(system_alerts: Sequence[Dict[str, str]]) -> List[Dict[str, str]]:
    dismissed = _dismissed_ids()
    read_ids = _read_ids()
    manual_alerts = [item for item in load_manual_alerts() if item.get("id") not in dismissed]
    deduped_system = []
    seen = set()
    for item in system_alerts:
        if item.get("id") in dismissed or item.get("id") in seen:
            continue
        deduped_system.append(item)
        seen.add(item.get("id"))

    combined = manual_alerts + deduped_system
    combined.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    normalized = []
    for alert in combined[:80]:
        alert_type = alert.get("type", "system")
        normalized.append(
            {
                **alert,
                "category": _category_for_type(alert_type),
                "read": alert.get("id", "") in read_ids,
            }
        )
    return normalized


def mark_alert_read(alert_id: str) -> None:
    if not alert_id:
        raise ValueError("Alert ID is required.")
    read_items = _load_json(READ_FILE)
    if any(item.get("id") == alert_id for item in read_items):
        return
    read_items.append({"id": alert_id, "read_at": _now_iso()})
    _save_json(READ_FILE, read_items)


def mark_all_read(alerts: Sequence[Dict[str, str]]) -> None:
    existing = _read_ids()
    rows = _load_json(READ_FILE)
    for alert in alerts:
        alert_id = alert.get("id", "")
        if not alert_id or alert_id in existing:
            continue
        rows.append({"id": alert_id, "read_at": _now_iso()})
    _save_json(READ_FILE, rows)


def unread_count(alerts: Sequence[Dict[str, str]]) -> int:
    return sum(1 for item in alerts if not item.get("read", False))
