"""Chart payload for the scanner and trade-detail candlesticks.

Candles come from the existing cached Alpaca history helper. This module
never calls Alpaca itself. Entry, exit, stop, and target marks are read
from records this app already stored (closed trades, overnight orders,
paper trades). Missing prices are omitted — nothing is invented.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

_TICKER = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
_MAX_MARKS = 12
_SKIPPED_ORDER_STATUSES = {"skipped", "failed", "preview", "rejected"}
_MARK_KINDS = ("entry", "exit", "stop", "target")


def normalize_ticker(ticker: str) -> str:
    symbol = str(ticker or "").strip().upper()
    if not _TICKER.fullmatch(symbol):
        raise ValueError("Ticker is required and must be a stock symbol.")
    return symbol


def _price(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number <= 0:  # NaN or non-positive
        return None
    return round(number, 4)


def _day(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    return None


def _add(
    marks: List[Dict[str, Any]],
    seen: set,
    kind: str,
    price: Any,
    when: Any = None,
    source: str = "",
) -> None:
    if kind not in _MARK_KINDS:
        return
    number = _price(price)
    if number is None:
        return
    key = (kind, number)
    if key in seen:
        return
    seen.add(key)
    marks.append(
        {
            "kind": kind,
            "price": number,
            "time": _day(when),
            "label": kind.capitalize(),
            "source": source,
        }
    )


def _plan_numbers(record: Dict[str, Any]) -> Dict[str, Any]:
    plan = record.get("trade_plan")
    if not isinstance(plan, dict):
        return {}
    numbers = plan.get("numbers")
    return numbers if isinstance(numbers, dict) else {}


def collect_trade_marks(
    ticker: str,
    closed_trades: Sequence[Dict[str, Any]] | None = None,
    overnight_orders: Sequence[Dict[str, Any]] | None = None,
    paper_trades: Sequence[Dict[str, Any]] | None = None,
) -> List[Dict[str, Any]]:
    """Price levels to draw on this ticker. Local records only."""
    symbol = normalize_ticker(ticker)
    marks: List[Dict[str, Any]] = []
    seen: set = set()

    def same(record: Dict[str, Any]) -> bool:
        return str(record.get("ticker") or record.get("symbol") or "").strip().upper() == symbol

    for trade in closed_trades or []:
        if not isinstance(trade, dict) or not same(trade):
            continue
        when_exit = trade.get("exit_timestamp") or trade.get("closed_at")
        when_entry = trade.get("entry_timestamp") or trade.get("logged_at") or trade.get("opened_at")
        _add(marks, seen, "entry", trade.get("average_entry_price") or trade.get("entry_price"), when_entry, "closed_trade")
        _add(marks, seen, "exit", trade.get("average_exit_price") or trade.get("exit_price"), when_exit, "closed_trade")
        numbers = _plan_numbers(trade)
        _add(marks, seen, "stop", trade.get("stop") or trade.get("stop_price") or numbers.get("stop_price"), when_entry, "closed_trade")
        _add(marks, seen, "target", trade.get("target") or trade.get("target_price") or numbers.get("target_price"), when_entry, "closed_trade")

    for trade in paper_trades or []:
        if not isinstance(trade, dict) or not same(trade):
            continue
        _add(marks, seen, "entry", trade.get("entry_price"), trade.get("opened_at"), "paper_trade")
        _add(marks, seen, "exit", trade.get("exit_price"), trade.get("closed_at"), "paper_trade")

    for order in overnight_orders or []:
        if not isinstance(order, dict) or not same(order):
            continue
        status = str(order.get("status") or "").strip().lower()
        if status in _SKIPPED_ORDER_STATUSES:
            continue
        side = str(order.get("side") or "").strip().upper()
        when = order.get("logged_at") or order.get("filled_at")
        numbers = _plan_numbers(order)
        if side == "SELL":
            _add(
                marks,
                seen,
                "exit",
                order.get("average_exit_price") or order.get("limit_price"),
                when,
                "overnight_order",
            )
        else:
            _add(
                marks,
                seen,
                "entry",
                order.get("average_entry_price") or order.get("entry_price") or order.get("limit_price"),
                when,
                "overnight_order",
            )
        _add(marks, seen, "stop", order.get("stop") or order.get("stop_price") or numbers.get("stop_price"), when, "overnight_order")
        _add(
            marks,
            seen,
            "target",
            order.get("target") or order.get("target_price") or numbers.get("target_price"),
            when,
            "overnight_order",
        )

    # Newest dated marks first so a long history keeps recent trades.
    ordered = sorted(marks, key=lambda mark: str(mark.get("time") or ""), reverse=True)
    return ordered[:_MAX_MARKS]


def build_chart_payload(
    ticker: str,
    interval: str,
    load_history: Callable[[str, str], Dict[str, Any]],
    closed_trades: Iterable[Dict[str, Any]] | None = None,
    overnight_orders: Iterable[Dict[str, Any]] | None = None,
    paper_trades: Iterable[Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    """Candles from ``load_history`` plus local trade marks.

    ``load_history`` is the cached chart-history helper. Calling it does
    not bypass that cache, and this function does not take a refresh flag.
    """
    symbol = normalize_ticker(ticker)
    chart_interval = (interval or "1d").strip() or "1d"
    history = load_history(symbol, chart_interval) or {}
    candles = history.get("candles") if isinstance(history.get("candles"), list) else []
    marks = collect_trade_marks(
        symbol,
        closed_trades=list(closed_trades or []),
        overnight_orders=list(overnight_orders or []),
        paper_trades=list(paper_trades or []),
    )
    return {
        "ticker": symbol,
        "interval": str(history.get("interval") or chart_interval),
        "candles": candles,
        "error": str(history.get("error") or ""),
        "marks": marks,
        "mark_count": len(marks),
        "generated_at": history.get("generated_at") or "",
    }
