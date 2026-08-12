from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Sequence

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("PLUTO_DATA_DIR", str(BASE_DIR / "data"))).resolve()
USER_DATA_ROOT = DATA_DIR / "users"


def _user_dir(user_id: str) -> Path:
    if not user_id:
        raise ValueError("user_id is required.")
    path = USER_DATA_ROOT / user_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _alerts_file(user_id: str) -> Path:
    return _user_dir(user_id) / "alerts.json"


def _dismissed_file(user_id: str) -> Path:
    return _user_dir(user_id) / "dismissed_alerts.json"


def _read_file(user_id: str) -> Path:
    return _user_dir(user_id) / "read_alerts.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_alert_id(alert_type: str, ticker: str, message: str) -> str:
    digest = hashlib.sha1(f"{alert_type}|{ticker}|{message}".encode("utf-8")).hexdigest()[:12]
    return f"{alert_type}-{ticker or 'global'}-{digest}"


@contextlib.contextmanager
def _locked(path: Path):
    """Exclusive lock scoped to an entire read-modify-write cycle, not just
    the final write - gunicorn runs multiple WORKER PROCESSES (not threads
    within one process), so a plain threading.Lock would not help: two
    workers can each read the same starting alerts.json, append their own
    new alert in memory, and write, with the second write silently
    discarding the first worker's addition. fcntl.flock() is a real
    process-wide advisory lock that blocks a second caller until the first
    releases it, turning read + mutate + write back into one atomic unit.

    Locks a dedicated sidecar file (path + ".lock"), not the JSON data file
    itself, so the lock's underlying file descriptor is never invalidated by
    the atomic temp-file-then-rename _save_json performs on the data file
    while the lock is held - flock() locks are tied to the open file
    description, and os.replace()'ing a DIFFERENT (temp) file into the data
    file's path never touches the lock file's inode at all."""
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.touch(exist_ok=True)
    with open(lock_path, "r+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _load_json(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        path.write_text("[]", encoding="utf-8")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        return []
    return []


def _save_json(path: Path, payload: List[Dict[str, str]]) -> None:
    """Atomic: writes to a temp file in the same directory, then
    os.replace()s it into place, so a concurrent reader - or a process that
    dies mid-write - never observes a partially-written or truncated file.
    A plain write_text() writes in place and can leave exactly that kind of
    corrupt file behind if it's interrupted partway through. The temp
    filename includes the pid and a random suffix so two callers racing
    without holding _locked (a caller bug, not something this function can
    prevent) at least don't collide on the SAME temp filename."""
    tmp_path = path.with_name(f"{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}")
    tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)


def load_manual_alerts(user_id: str) -> List[Dict[str, str]]:
    return _load_json(_alerts_file(user_id))


def add_manual_alert(user_id: str, payload: Dict[str, str]) -> Dict[str, str]:
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

    alerts_file = _alerts_file(user_id)
    with _locked(alerts_file):
        alerts = load_manual_alerts(user_id)
        if not any(existing["id"] == alert["id"] for existing in alerts):
            alerts.insert(0, alert)
            _save_json(alerts_file, alerts)
    return alert


def dismiss_alert(user_id: str, alert_id: str) -> None:
    if not alert_id:
        raise ValueError("Alert ID is required.")
    dismissed_file = _dismissed_file(user_id)
    with _locked(dismissed_file):
        dismissed = _load_json(dismissed_file)
        if not any(item.get("id") == alert_id for item in dismissed):
            dismissed.append({"id": alert_id, "dismissed_at": _now_iso()})
            _save_json(dismissed_file, dismissed)


def dismiss_alerts(user_id: str, alert_ids: Sequence[str]) -> int:
    valid_ids = [alert_id for alert_id in alert_ids if alert_id]
    if not valid_ids:
        raise ValueError("At least one alert ID is required.")

    dismissed_file = _dismissed_file(user_id)
    with _locked(dismissed_file):
        dismissed = _load_json(dismissed_file)
        existing_ids = {item.get("id", "") for item in dismissed}
        newly_added = 0
        for alert_id in valid_ids:
            if alert_id in existing_ids:
                continue
            dismissed.append({"id": alert_id, "dismissed_at": _now_iso()})
            existing_ids.add(alert_id)
            newly_added += 1
        if newly_added:
            _save_json(dismissed_file, dismissed)
    return newly_added


def _dismissed_ids(user_id: str) -> set[str]:
    return {item.get("id", "") for item in _load_json(_dismissed_file(user_id))}


def _read_ids(user_id: str) -> set[str]:
    return {item.get("id", "") for item in _load_json(_read_file(user_id))}


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
        "exit-signal": "Trade Journal",
    }
    return mapping.get(alert_type, "System")


def _target_stop_by_ticker(overnight_orders: Sequence[Dict[str, object]]) -> Dict[str, Dict[str, object]]:
    target_stop: Dict[str, Dict[str, object]] = {}
    for order in overnight_orders:
        if order.get("status") != "placed":
            continue
        ticker = str(order.get("ticker", "")).upper()
        if not ticker or ticker in target_stop:
            continue  # overnight_orders is newest-first, so the first hit per ticker is the most recent order
        target_stop[ticker] = {"target": order.get("target"), "stop": order.get("stop")}
    return target_stop


def _exit_signal_for_position(position: Dict[str, object], levels: Dict[str, object] | None) -> Dict[str, object] | None:
    """Returns {status, target, stop, last_price} if price has crossed the
    recorded target/stop for this position, else None. Never auto-closes
    anything - purely informational."""
    if not levels:
        return None
    try:
        last_price = float(position.get("last_price", 0) or 0)
        target = float(levels["target"]) if levels.get("target") not in (None, "") else None
        stop = float(levels["stop"]) if levels.get("stop") not in (None, "") else None
    except (TypeError, ValueError):
        return None
    if not last_price:
        return None

    if target is not None and last_price >= target:
        return {"status": "TARGET_HIT", "target": target, "stop": stop, "last_price": last_price}
    if stop is not None and last_price <= stop:
        return {"status": "STOP_HIT", "target": target, "stop": stop, "last_price": last_price}
    return None


def annotate_positions_with_exit_signal(
    positions: Sequence[Dict[str, object]],
    overnight_orders: Sequence[Dict[str, object]],
) -> List[Dict[str, object]]:
    target_stop = _target_stop_by_ticker(overnight_orders)
    annotated = []
    for position in positions:
        ticker = str(position.get("symbol", "")).upper()
        signal = _exit_signal_for_position(position, target_stop.get(ticker))
        annotated.append({**position, "exit_signal": signal["status"] if signal else None})
    return annotated


def build_exit_signal_alerts(
    positions: Sequence[Dict[str, object]],
    overnight_orders: Sequence[Dict[str, object]],
) -> List[Dict[str, str]]:
    """Compares each open Webull sandbox position's live price against the
    target/stop recorded when that ticker's order was placed, and raises an
    alert once price crosses either level - the app never auto-closes a
    position, this just tells the user it's time to look."""
    target_stop = _target_stop_by_ticker(overnight_orders)

    alerts: List[Dict[str, str]] = []
    for position in positions:
        ticker = str(position.get("symbol", "")).upper()
        if not ticker:
            continue
        signal = _exit_signal_for_position(position, target_stop.get(ticker))
        if not signal:
            continue

        if signal["status"] == "TARGET_HIT":
            message = f"{ticker} hit its target of ${signal['target']:.2f} (now ${signal['last_price']:.2f}). Consider taking profit."
        else:
            message = f"{ticker} broke its stop of ${signal['stop']:.2f} (now ${signal['last_price']:.2f}). Consider cutting the loss."
        alert_type = "exit-signal"

        alerts.append(
            {
                "id": _build_alert_id(alert_type, ticker, message),
                "type": alert_type,
                "ticker": ticker,
                "message": message,
                "source": "system",
                "created_at": _now_iso(),
            }
        )
    return alerts


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


def get_alerts_snapshot(user_id: str, system_alerts: Sequence[Dict[str, str]]) -> List[Dict[str, str]]:
    dismissed = _dismissed_ids(user_id)
    read_ids = _read_ids(user_id)
    manual_alerts = [item for item in load_manual_alerts(user_id) if item.get("id") not in dismissed]
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


def mark_alert_read(user_id: str, alert_id: str) -> None:
    if not alert_id:
        raise ValueError("Alert ID is required.")
    read_file = _read_file(user_id)
    with _locked(read_file):
        read_items = _load_json(read_file)
        if any(item.get("id") == alert_id for item in read_items):
            return
        read_items.append({"id": alert_id, "read_at": _now_iso()})
        _save_json(read_file, read_items)


def mark_all_read(user_id: str, alerts: Sequence[Dict[str, str]]) -> None:
    read_file = _read_file(user_id)
    with _locked(read_file):
        existing = _read_ids(user_id)
        rows = _load_json(read_file)
        for alert in alerts:
            alert_id = alert.get("id", "")
            if not alert_id or alert_id in existing:
                continue
            rows.append({"id": alert_id, "read_at": _now_iso()})
        _save_json(read_file, rows)


def unread_count(alerts: Sequence[Dict[str, str]]) -> int:
    return sum(1 for item in alerts if not item.get("read", False))
