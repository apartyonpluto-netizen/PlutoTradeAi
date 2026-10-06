"""Ways a strategy arrives: a form, a plain-English draft, a CSV, or
read-only broker fills. Fills describe what was traded. They do not invent
the indicator rule that triggered each trade.
"""

from __future__ import annotations

import csv
import re
from collections import defaultdict, deque
from datetime import datetime
from io import StringIO
from statistics import median
from typing import Any, Deque, Dict, List, Optional

from .model import ENTRY_RULES, MAX_DESCRIPTION_LENGTH, MAX_IMPORTED_TRADES, STYLES, normalize_strategy

_RSI_BELOW = re.compile(r"rsi(?:\s*14)?\s*(?:below|under|<)\s*(\d+(?:\.\d+)?)", re.I)
_RSI_ABOVE = re.compile(r"rsi(?:\s*14)?\s*(?:above|over|>)\s*(\d+(?:\.\d+)?)", re.I)
_STOP = re.compile(r"stop(?:\s*loss)?\s*(?:of|at|=|:)?\s*(\d+(?:\.\d+)?)\s*%", re.I)
_TARGET_PCT = re.compile(r"target\s*(?:of|at|=|:)?\s*(\d+(?:\.\d+)?)\s*%", re.I)
_TARGET_R = re.compile(r"(\d+(?:\.\d+)?)\s*r\b", re.I)
_HOLD = re.compile(r"hold\s*(?:for\s*)?(\d+)\s*day", re.I)
_TICKER = re.compile(r"\b([A-Z]{1,5})\b")
_STOPWORDS = {"A", "I", "RSI", "EMA", "VWAP", "DAY", "DAYS", "AND", "THE", "FOR", "HOLD", "STOP", "WITH"}


def _date(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text[:10] if fmt == "%Y-%m-%d" else text, fmt).date().isoformat()
        except ValueError:
            continue
    return text[:10]


def _float(value: Any) -> Optional[float]:
    if value is None:
        return None
    text = str(value).strip().replace("%", "").replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def keyword_draft(description: str) -> Dict[str, Any]:
    """Fills only the fields the words actually name. Everything else stays blank."""
    text = " ".join((description or "").split())[:MAX_DESCRIPTION_LENGTH]
    rules: List[Dict[str, Any]] = []
    lowered = text.lower()

    match = _RSI_BELOW.search(text)
    if match:
        rules.append({"kind": "rsi_below", "value": float(match.group(1))})
    match = _RSI_ABOVE.search(text)
    if match:
        rules.append({"kind": "rsi_above", "value": float(match.group(1))})
    if "breakout" in lowered or "20-day high" in lowered or "20 day high" in lowered:
        rules.append({"kind": "breakout_20d"})
    if "breakdown" in lowered or "20-day low" in lowered or "20 day low" in lowered:
        rules.append({"kind": "breakdown_20d"})
    if "pullback" in lowered:
        rules.append({"kind": "price_above_ema20"})
    if "ema stack" in lowered or "uptrend" in lowered:
        rules.append({"kind": "ema_stack_up"})
    volume = re.search(r"(?:relative volume|rvol)\s*(?:above|over|>)\s*(\d+(?:\.\d+)?)", text, re.I)
    if volume:
        rules.append({"kind": "relative_volume_above", "value": float(volume.group(1))})
    if "momentum" in lowered and not volume:
        rules.append({"kind": "day_change_above", "value": 1})

    style = "custom"
    for candidate in STYLES:
        if candidate.replace("_", " ") in lowered:
            style = candidate
            break
    if "mean reversion" in lowered:
        style = "mean_reversion"

    direction = "long"
    if any(word in lowered for word in ("short", "put", "breakdown")):
        direction = "short"
    if "long and short" in lowered or "both directions" in lowered:
        direction = "both"

    timeframe = "intraday" if any(word in lowered for word in ("intraday", "5 minute", "5-minute", "1 minute", "scalp")) else "daily"
    instruments = "options" if "option" in lowered else "equity"
    if "shares" in lowered or "stock" in lowered:
        instruments = "both" if instruments == "options" else "equity"

    tickers = []
    for token in _TICKER.findall(text):
        if token in _STOPWORDS or token in tickers:
            continue
        tickers.append(token)
        if len(tickers) == 5:
            break

    stop = _STOP.search(text)
    target_pct = _TARGET_PCT.search(text)
    target_r = _TARGET_R.search(text)
    hold = _HOLD.search(text)
    draft: Dict[str, Any] = {
        "name": "Described strategy",
        "description": text,
        "source": "keyword_draft",
        "style": style,
        "direction": direction,
        "timeframe": timeframe,
        "instruments": instruments,
        "tickers": tickers,
        "entry_rules": rules[:8],
        "max_hold_days": int(hold.group(1)) if hold else 5,
        "import_note": "",
    }
    if stop:
        draft["stop_percent"] = float(stop.group(1))
    if target_pct:
        draft["target_type"] = "percent"
        draft["target_value"] = float(target_pct.group(1))
    elif target_r and stop:
        draft["target_type"] = "r_multiple"
        draft["target_value"] = float(target_r.group(1))
    recognized = [rule["kind"] for rule in rules]
    note = (
        "Draft from keywords in the description. This is not a model judgment. "
        "Review every field before saving."
    )
    if "vwap" in lowered:
        note += " VWAP was mentioned. Daily bars cannot reconstruct VWAP, so no VWAP rule was added."
    if not recognized:
        note += " No entry rule was recognized. Add one, or connect an Anthropic key for a fuller draft."
    return {"draft": draft, "note": note, "recognized": recognized, "source": "keyword_draft"}


def _model_definition(description: str, api_key: str) -> Optional[Dict[str, Any]]:
    """Calls the user's Anthropic key. Returns the tool payload, or None.
    The key is never included in an error or a return value."""
    from brains.llm_reasoning import _get_client, anthropic

    if anthropic is None or not api_key:
        return None
    tool = {
        "name": "submit_strategy_definition",
        "description": "Translate the description into the structured form. Do not add a rule the description did not state. Do not invent results.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "style": {"type": "string", "enum": list(STYLES)},
                "direction": {"type": "string", "enum": ["long", "short", "both"]},
                "timeframe": {"type": "string", "enum": ["daily", "intraday"]},
                "instruments": {"type": "string", "enum": ["equity", "options", "both"]},
                "tickers": {"type": "array", "items": {"type": "string"}},
                "max_hold_days": {"type": "integer"},
                "stop_percent": {"type": "number"},
                "target_type": {"type": "string", "enum": ["percent", "r_multiple", "none"]},
                "target_value": {"type": "number"},
                "entry_rules": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "kind": {"type": "string", "enum": list(ENTRY_RULES)},
                            "value": {"type": "number"},
                        },
                        "required": ["kind"],
                    },
                },
            },
            "required": ["name", "entry_rules"],
        },
    }
    client = _get_client(api_key)
    response = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=700,
        system=(
            "You translate a trader's description into a structured strategy form. "
            "Use only rules they described. Leave entry_rules empty if they did not name an entry. "
            "Do not claim the strategy works, and do not add performance numbers."
        ),
        tools=[tool],
        tool_choice={"type": "tool", "name": "submit_strategy_definition"},
        messages=[{"role": "user", "content": description[:MAX_DESCRIPTION_LENGTH]}],
    )
    for block in response.content:
        if getattr(block, "type", "") == "tool_use" and getattr(block, "name", "") == "submit_strategy_definition":
            payload = block.input
            return payload if isinstance(payload, dict) else None
    return None


def translate_description(description: str, api_key: str = "") -> Dict[str, Any]:
    fallback = keyword_draft(description)
    if not (api_key or "").strip():
        fallback["note"] += " No Anthropic key is saved for this account, so a model was not asked."
        fallback["anthropic_used"] = False
        return fallback
    try:
        payload = _model_definition(description, api_key)
    except Exception:
        fallback["note"] += " The model request failed. The keyword draft is shown instead."
        fallback["anthropic_used"] = False
        return fallback
    if not payload:
        fallback["note"] += " The model did not return a form. The keyword draft is shown instead."
        fallback["anthropic_used"] = False
        return fallback
    payload["description"] = description[:MAX_DESCRIPTION_LENGTH]
    payload["source"] = "plain_english"
    payload.setdefault("name", "Described strategy")
    try:
        draft = normalize_strategy(payload)
    except ValueError:
        fallback["note"] += " The model returned a form that does not fit the rule catalog. The keyword draft is shown instead."
        fallback["anthropic_used"] = False
        return fallback
    draft.pop("created_at", None)
    draft.pop("updated_at", None)
    return {
        "draft": draft,
        "note": "Draft from your Anthropic key. It is a translation of the description, not a judgment that the strategy works. Review every field before saving.",
        "recognized": [rule["kind"] for rule in draft.get("entry_rules") or []],
        "source": "plain_english",
        "anthropic_used": True,
    }


def _header_map(fieldnames: List[str]) -> Dict[str, str]:
    aliases = {
        "ticker": {"ticker", "symbol", "sym"},
        "side": {"side", "direction", "action", "buy_sell"},
        "entry_date": {"entry_date", "entry", "opened", "open_date", "date"},
        "exit_date": {"exit_date", "exit", "closed", "close_date"},
        "entry_price": {"entry_price", "price", "avg_entry", "average_entry_price"},
        "exit_price": {"exit_price", "avg_exit", "average_exit_price"},
        "pnl_percent": {"pnl_percent", "pnl", "return", "return_percent", "pnl_pct"},
        "quantity": {"quantity", "qty", "shares"},
    }
    found: Dict[str, str] = {}
    for field in fieldnames:
        key = re.sub(r"[^a-z0-9]+", "_", (field or "").strip().lower()).strip("_")
        for canonical, names in aliases.items():
            if key in names and canonical not in found:
                found[canonical] = field
    return found


def _side(value: str) -> str:
    text = (value or "").strip().lower()
    if text in {"sell", "short", "put", "s"}:
        return "short"
    return "long"


def parse_trade_csv(text: str) -> Dict[str, Any]:
    if len(text or "") > 500_000:
        raise ValueError("That CSV is too large. Split it under 500KB.")
    reader = csv.DictReader(StringIO(text or ""))
    if not reader.fieldnames:
        raise ValueError("The CSV needs a header row.")
    columns = _header_map(list(reader.fieldnames))
    if "ticker" not in columns:
        raise ValueError("The CSV needs a ticker or symbol column.")
    trades: List[Dict[str, Any]] = []
    for row in reader:
        if len(trades) >= MAX_IMPORTED_TRADES:
            break
        ticker = str(row.get(columns["ticker"]) or "").strip().upper()
        if not ticker:
            continue
        entry = _float(row.get(columns["entry_price"])) if "entry_price" in columns else None
        exit_price = _float(row.get(columns["exit_price"])) if "exit_price" in columns else None
        pnl = _float(row.get(columns["pnl_percent"])) if "pnl_percent" in columns else None
        side = _side(str(row.get(columns["side"]) or "")) if "side" in columns else "long"
        if pnl is None and entry and exit_price:
            pnl = (exit_price - entry) / entry * 100 if side == "long" else (entry - exit_price) / entry * 100
        closed = pnl is not None or bool(exit_price)
        trades.append(
            {
                "ticker": ticker,
                "side": side,
                "entry_date": _date(str(row.get(columns["entry_date"]) or "")) if "entry_date" in columns else "",
                "exit_date": _date(str(row.get(columns["exit_date"]) or "")) if "exit_date" in columns else "",
                "entry_price": entry,
                "exit_price": exit_price,
                "pnl_percent": None if pnl is None else round(pnl, 4),
                "closed": closed,
                "source": "csv",
            }
        )
    if not trades:
        raise ValueError("No trades were found in that CSV.")
    return {"trades": trades, "draft": _draft_from_trades(trades, source="csv"), "note": _import_note(trades, "CSV")}


def _import_note(trades: List[Dict[str, Any]], origin: str) -> str:
    closed = [trade for trade in trades if trade.get("closed") and trade.get("pnl_percent") is not None]
    return (
        f"{origin} import: {len(trades)} fill row(s), {len(closed)} with a percent return. "
        "This is your imported record, not the app's reconciled closed trades. "
        "Fills do not show the indicator rule, so entry rules were left blank for you to add."
    )


def _draft_from_trades(trades: List[Dict[str, Any]], *, source: str) -> Dict[str, Any]:
    closed = [trade for trade in trades if trade.get("closed")]
    holds: List[int] = []
    for trade in closed:
        try:
            start = datetime.strptime(trade["entry_date"], "%Y-%m-%d")
            end = datetime.strptime(trade["exit_date"], "%Y-%m-%d")
        except (TypeError, ValueError):
            continue
        days = (end - start).days
        if days >= 0:
            holds.append(days)
    longs = sum(1 for trade in trades if trade.get("side") == "long")
    shorts = sum(1 for trade in trades if trade.get("side") == "short")
    counts: Dict[str, int] = defaultdict(int)
    for trade in trades:
        if trade.get("ticker"):
            counts[trade["ticker"]] += 1
    tickers = [ticker for ticker, _count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:5]]
    hold_days = int(median(holds)) if holds else 5
    hold_days = min(20, max(1, hold_days))
    return {
        "name": "Imported strategy",
        "description": "",
        "source": "trade_import",
        "style": "custom",
        "direction": "short" if shorts > longs else "long",
        "timeframe": "intraday" if holds and median(holds) <= 1 else "daily",
        "instruments": "equity",
        "tickers": tickers,
        "entry_rules": [],
        "max_hold_days": hold_days,
        "imported_trades": trades,
        "import_note": _import_note(trades, "CSV" if source == "csv" else "Broker fill"),
    }


def pair_broker_fills(orders: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Pairs filled equity orders into round trips. Read-only. Cancels and
    unfilled orders are ignored. Option orders are counted and not paired."""
    lots: Dict[str, Deque[Dict[str, Any]]] = defaultdict(deque)
    trades: List[Dict[str, Any]] = []
    skipped_options = 0

    def when(order: Dict[str, Any]) -> str:
        return str(order.get("filled_time_at") or order.get("place_time_at") or "")

    filled = []
    for order in orders:
        if str(order.get("status") or "").upper() != "FILLED":
            continue
        instrument = str(order.get("instrument_type") or "EQUITY").upper()
        if instrument not in {"EQUITY", "STOCK"}:
            skipped_options += 1
            continue
        price = _float(order.get("filled_price"))
        quantity = _float(order.get("filled_quantity"))
        if not price or not quantity or quantity <= 0:
            continue
        filled.append(order)
    filled.sort(key=when)

    for order in filled:
        symbol = str(order.get("symbol") or "").upper()
        side = str(order.get("side") or "").upper()
        quantity = float(order.get("filled_quantity"))
        price = float(order.get("filled_price"))
        opened = _date(when(order)[:10])
        book = lots[symbol]
        opening_side = "long" if side == "BUY" else "short"
        closing = book and book[0]["side"] != opening_side
        if not closing:
            book.append({"side": opening_side, "qty": quantity, "price": price, "date": opened})
            continue
        remaining = quantity
        while remaining > 1e-9 and book and book[0]["side"] != opening_side:
            lot = book[0]
            matched = min(remaining, lot["qty"])
            entry = lot["price"]
            pnl = (price - entry) / entry * 100 if lot["side"] == "long" else (entry - price) / entry * 100
            trades.append(
                {
                    "ticker": symbol,
                    "side": lot["side"],
                    "entry_date": lot["date"],
                    "exit_date": opened,
                    "entry_price": round(entry, 4),
                    "exit_price": round(price, 4),
                    "pnl_percent": round(pnl, 4),
                    "closed": True,
                    "source": "broker_fills",
                }
            )
            lot["qty"] -= matched
            remaining -= matched
            if lot["qty"] <= 1e-9:
                book.popleft()
        if remaining > 1e-9:
            book.append({"side": opening_side, "qty": remaining, "price": price, "date": opened})

    for symbol, book in lots.items():
        for lot in book:
            trades.append(
                {
                    "ticker": symbol,
                    "side": lot["side"],
                    "entry_date": lot["date"],
                    "exit_date": "",
                    "entry_price": round(lot["price"], 4),
                    "exit_price": None,
                    "pnl_percent": None,
                    "closed": False,
                    "source": "broker_fills",
                }
            )
    trades = trades[:MAX_IMPORTED_TRADES]
    if not trades:
        raise ValueError("No filled equity orders were found to pair.")
    note = _import_note(trades, "Broker fill")
    if skipped_options:
        note += f" {skipped_options} non-equity order(s) were not paired."
    note += " Fees are not applied. Open leftovers have no return."
    draft = _draft_from_trades(trades, source="broker")
    draft["import_note"] = note
    return {"trades": trades, "draft": draft, "note": note}


def read_broker_orders(user_id: str, days_back: int = 90) -> List[Dict[str, Any]]:
    """Read-only account list and order history. Does not place or change orders."""
    from integrations import webull as webull_api
    from webull_credentials import get_webull_credentials

    creds = get_webull_credentials(user_id)
    if not creds.get("app_key") or not creds.get("app_secret"):
        raise ValueError("Connect Webull in Account Hub before importing fill history.")
    days_back = max(1, min(int(days_back), 120))
    accounts = webull_api.get_paper_accounts(creds["app_key"], creds["app_secret"])
    account = webull_api.find_individual_cash_account(accounts)
    account_id = (account or {}).get("account_id")
    if not account_id:
        raise ValueError("No Webull paper account was found to read fills from.")
    return webull_api.get_order_history(creds["app_key"], creds["app_secret"], account_id, days_back=days_back)
