"""Home-screen facts computed from local records. No broker calls.

The daily loss-cap meter is not computed here. It uses the broker day P/L
in daily_loss.evaluate, the same figure the scan halt uses, and is injected
when a page renders. today_pnl below is closed trades after fees only."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from autonomy.autonomous_controller import get_autonomy_status
from autonomy.closed_trades import list_closed_trades
from autonomy.overnight_orders import list_overnight_orders
from autonomy.research_log import list_research_decisions
from go_live_gate import evaluate as evaluate_go_live
from integrations.webull import is_live_trading_armed
from kill_switch import current_halt
from paper_costs import performance_net
from settings_store import get_settings

_NY = ZoneInfo("America/New_York")


def _number(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _today_key() -> str:
    return datetime.now(_NY).date().isoformat()


def _on_today(timestamp: Any) -> bool:
    if not isinstance(timestamp, str) or not timestamp:
        return False
    try:
        moment = datetime.fromisoformat(timestamp)
    except ValueError:
        return timestamp.startswith(_today_key())
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=_NY)
    return moment.astimezone(_NY).date().isoformat() == _today_key()


def trading_mode(status: Dict[str, Any]) -> str:
    if is_live_trading_armed():
        return "live"
    current = str(status.get("current_mode") or "OFF").upper()
    if current in {"PAPER", "APPROVAL", "AUTONOMOUS"}:
        return "paper"
    return "off"


def build_home_status(user_id: str) -> Dict[str, Any]:
    status = get_autonomy_status(user_id)
    mode = trading_mode(status)
    halt = current_halt(user_id)
    emergency = bool(status.get("emergency_stop_enabled"))
    trades = list_closed_trades(user_id)
    today_nets = []
    for trade in trades:
        if not _on_today(trade.get("exit_timestamp")):
            continue
        if trade.get("pnl_status") not in (None, "", "complete"):
            continue
        net = performance_net(trade)
        if net is not None:
            today_nets.append(net)
    today_pnl = round(sum(today_nets), 2)
    return {
        "mode": mode,
        "mode_label": {"live": "Live", "paper": "Paper", "off": "Off"}[mode],
        "halted": emergency or bool(halt.get("active")),
        "halt_reason": "Emergency stop is on." if emergency else str(halt.get("reason") or ""),
        "today_pnl": today_pnl,
        "daily_loss_limit_percent": float(status.get("daily_loss_limit_percent") or 0),
    }


def build_savings_goal(user_id: str) -> Dict[str, Any]:
    settings = get_settings(user_id)
    name = str(settings.get("savings_goal_name") or "").strip()
    amount = _number(settings.get("savings_goal_amount"))
    if amount is not None and amount <= 0:
        amount = None
    realized = 0.0
    for trade in list_closed_trades(user_id):
        if str(trade.get("environment") or "") != "live":
            continue
        if trade.get("pnl_status") not in (None, "", "complete"):
            continue
        net = _number(trade.get("net_realized_pnl"))
        if net is not None:
            realized += net
    realized = round(realized, 2)
    banked = round(max(0.0, realized), 2)
    progress = round(min(100.0, banked / amount * 100.0), 1) if amount else None
    visible = bool(settings.get("savings_goal_visible", True))
    return {
        "name": name,
        "amount": amount,
        "unset": amount is None,
        "visible": visible,
        "realized_live_profit": realized,
        "banked": banked,
        "progress_percent": progress,
    }


def _plain(text: str) -> str:
    cleaned = " ".join(str(text or "").replace("_", " ").split())
    if not cleaned:
        return ""
    return cleaned[0].upper() + cleaned[1:]


def _on_record_today(row: Dict[str, Any]) -> bool:
    if str(row.get("trading_day") or "") == _today_key():
        return True
    return _on_today(row.get("logged_at") or row.get("exit_timestamp"))


def build_daily_recap(user_id: str, limit: int = 6) -> Dict[str, Any]:
    """Today's taken and skipped names, in plain language, plus goal movement.

    Goal movement is omitted when the owner has hidden the goal. Paper profit
    does not move the meter; only today's realized live profit does.
    """
    goal = build_savings_goal(user_id)
    taken: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    for row in reversed(list_research_decisions(user_id)):
        if not _on_record_today(row):
            continue
        decision = str(row.get("decision") or "")
        ticker = str(row.get("ticker") or "—")
        if decision == "skipped" and len(skipped) < limit:
            why = _plain(str(row.get("reason_skipped") or "")) or _plain(str(row.get("skip_category") or "")) or "Skipped"
            skipped.append({"ticker": ticker, "why": why[:180]})
        elif decision == "placed" and len(taken) < limit:
            why = _plain(str(row.get("strategy") or "")) or "Order placed"
            taken.append({"ticker": ticker, "why": why[:180], "net": None})
    if not taken:
        for trade in list_closed_trades(user_id):
            if not _on_today(trade.get("exit_timestamp")):
                continue
            net = performance_net(trade) if trade.get("pnl_status") in (None, "", "complete") else None
            strategy = str(trade.get("strategy") or "Closed trade")
            why = f"Closed by {trade.get('close_reason') or trade.get('exit_type') or 'recorded exit'}."
            taken.append({"ticker": str(trade.get("ticker") or "—"), "why": f"{strategy}. {why}"[:180], "net": net})
            if len(taken) >= limit:
                break
    goal_move = None
    if goal["visible"] and not goal["unset"]:
        today_live = 0.0
        for trade in list_closed_trades(user_id):
            if str(trade.get("environment") or "") != "live":
                continue
            if not _on_today(trade.get("exit_timestamp")):
                continue
            if trade.get("pnl_status") not in (None, "", "complete"):
                continue
            net = _number(trade.get("net_realized_pnl"))
            if net is not None:
                today_live += net
        goal_move = {
            "name": goal["name"] or "Goal",
            "banked": goal["banked"],
            "amount": goal["amount"],
            "progress_percent": goal["progress_percent"],
            "today": round(today_live, 2),
        }
    return {"taken": taken, "skipped": skipped, "goal": goal_move}


def resolve_chart_markers(user_id: str, ticker: str, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Optional[float]]:
    """Entry, exit, stop, and target for a chart.

    Explicit overrides win. Anything left blank is filled from this account's
    own records (closed trade, then a working order). No market-data call.
    """
    levels: Dict[str, Optional[float]] = {"entry": None, "exit": None, "stop": None, "target": None}
    for key in levels:
        if not overrides or key not in overrides or overrides.get(key) in (None, ""):
            continue
        value = _number(overrides.get(key))
        levels[key] = value if value is not None and value > 0 else None
    symbol = str(ticker or "").strip().upper()
    if not symbol:
        return levels

    def _fill_from(source: Dict[str, Any], mapping: Dict[str, str]) -> None:
        for level, field in mapping.items():
            if levels[level] is None:
                value = _number(source.get(field))
                if value is not None and value > 0:
                    levels[level] = value

    for trade in list_closed_trades(user_id):
        if str(trade.get("ticker") or "").upper() != symbol:
            continue
        _fill_from(trade, {"entry": "average_entry_price", "exit": "average_exit_price"})
        break
    if any(levels[key] is None for key in ("entry", "stop", "target")):
        for order in list_overnight_orders(user_id):
            if str(order.get("ticker") or "").upper() != symbol:
                continue
            _fill_from(order, {"entry": "limit_price", "stop": "stop_price", "target": "target_price"})
            break
    return levels


def build_trade_cards(user_id: str, limit: int = 6) -> List[Dict[str, Any]]:
    research = {
        str(row.get("entry_client_order_id")): row
        for row in list_research_decisions(user_id)
        if row.get("entry_client_order_id")
    }
    cards = []
    for trade in list_closed_trades(user_id)[:limit]:
        cards.append(_card(trade, research.get(str(trade.get("entry_client_order_id") or trade.get("trade_id") or ""))))
    return cards


def _card(trade: Dict[str, Any], research: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    entry = _number(trade.get("average_entry_price"))
    exit_price = _number(trade.get("average_exit_price"))
    net = performance_net(trade) if trade.get("pnl_status") in (None, "", "complete") else None
    strategy = str((research or {}).get("strategy") or trade.get("strategy") or "Closed trade")
    score = (research or {}).get("raw_confidence")
    if score is None:
        score = trade.get("confidence")
    thesis = ""
    snapshot = (research or {}).get("signal_snapshot") if research else None
    if isinstance(snapshot, dict):
        thesis = str(snapshot.get("why_this_strategy_fits") or snapshot.get("trade_thesis") or snapshot.get("reason") or "")
    plan = (research or {}).get("trade_plan") if research else None
    numbers = plan.get("numbers") if isinstance(plan, dict) else {}
    if not isinstance(numbers, dict):
        numbers = {}
    why = thesis.strip() or f"Taken as {strategy}. Closed by {trade.get('close_reason') or trade.get('exit_type') or 'recorded exit'}."
    chart = _chart(entry, exit_price)
    return {
        "ticker": trade.get("ticker") or "—",
        "strategy": strategy,
        "why": why[:280],
        "score": score,
        "entry": entry,
        "exit": exit_price,
        "stop": _number(numbers.get("stop_price")),
        "target": _number(numbers.get("target_price")),
        "net": net,
        "close_reason": trade.get("close_reason") or trade.get("exit_type") or "",
        "chart": chart,
    }


def _chart(entry: Optional[float], exit_price: Optional[float]) -> Optional[Dict[str, float]]:
    if entry is None or exit_price is None:
        return None
    low = min(entry, exit_price)
    high = max(entry, exit_price)
    span = high - low or max(abs(entry) * 0.01, 0.01)

    def y(price: float) -> float:
        return round(8 + (1 - (price - low) / span) * 48, 1)

    return {"x1": 12, "y1": y(entry), "x2": 148, "y2": y(exit_price), "up": exit_price >= entry}
