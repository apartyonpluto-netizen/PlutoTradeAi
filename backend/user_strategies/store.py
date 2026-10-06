"""Per-user strategy files, plus an anonymous aggregate of style and results.

The aggregate stores no name, description, ticker, or user id. A row is
keyed by an HMAC of the user and strategy so a later save replaces it,
and the HMAC is not reversible from the file alone.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import hmac
import json
import os
import uuid
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from auth import DATA_DIR, user_dir

from .model import (
    MAX_STRATEGIES_PER_USER,
    hold_bucket,
    infer_style,
    normalize_strategy,
    scoring_enabled,
)

AGGREGATE_FILE = DATA_DIR / "user_strategy_public_aggregate.json"


def _file(user_id: str) -> Path:
    if not user_id:
        raise ValueError("user_id is required.")
    return user_dir(user_id) / "user_strategies.json"


def _draft_file(user_id: str) -> Path:
    return user_dir(user_id) / "user_strategy_draft.json"


def _anon_id(user_id: str, strategy_id: str) -> str:
    key = os.environ.get("FLASK_SECRET_KEY", "pluto-strategy-aggregate").encode()
    return hmac.new(key, f"{user_id}:{strategy_id}".encode(), hashlib.sha256).hexdigest()[:24]


@contextlib.contextmanager
def _locked(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.touch(exist_ok=True)
    with open(lock_path, "r+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _read(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"strategies": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"strategies": []}
    if not isinstance(data, dict):
        return {"strategies": []}
    strategies = data.get("strategies")
    return {"strategies": strategies if isinstance(strategies, list) else []}


def _write(path: Path, payload: Dict[str, Any]) -> None:
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def save_draft(user_id: str, draft: Dict[str, Any], note: str, source: str) -> None:
    """Unsaved form fill. Kept in the user directory, not the session cookie,
    because an imported trade list does not fit in a cookie."""
    path = _draft_file(user_id)
    with _locked(path):
        _write(path, {"draft": draft, "note": note, "source": source})


def load_draft(user_id: str) -> Optional[Dict[str, Any]]:
    path = _draft_file(user_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) and isinstance(data.get("draft"), dict) else None


def clear_draft(user_id: str) -> None:
    path = _draft_file(user_id)
    if path.exists():
        path.unlink()


def list_strategies(user_id: str) -> List[Dict[str, Any]]:
    return list(_read(_file(user_id))["strategies"])


def get_strategy(user_id: str, strategy_id: str) -> Optional[Dict[str, Any]]:
    for strategy in list_strategies(user_id):
        if strategy.get("id") == strategy_id:
            return strategy
    return None


def _stamp_id(strategy: Dict[str, Any], strategy_id: Optional[str] = None) -> Dict[str, Any]:
    strategy = dict(strategy)
    strategy["id"] = strategy_id or uuid.uuid4().hex[:12]
    return strategy


def _reject_duplicate_name(strategies: List[Dict[str, Any]], name: str, keep_id: Optional[str]) -> None:
    for strategy in strategies:
        if strategy.get("id") != keep_id and str(strategy.get("name", "")).casefold() == name.casefold():
            raise ValueError("You already have a strategy with that name.")


def create_strategy(user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    path = _file(user_id)
    with _locked(path):
        stored = _read(path)
        if len(stored["strategies"]) >= MAX_STRATEGIES_PER_USER:
            raise ValueError(f"The library holds {MAX_STRATEGIES_PER_USER} strategies. Delete one before adding another.")
        strategy = _stamp_id(normalize_strategy(payload))
        _reject_duplicate_name(stored["strategies"], strategy["name"], None)
        stored["strategies"].append(strategy)
        _write(path, stored)
    _upsert_aggregate(user_id, strategy)
    return strategy


def update_strategy(user_id: str, strategy_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    path = _file(user_id)
    with _locked(path):
        stored = _read(path)
        index = next((i for i, item in enumerate(stored["strategies"]) if item.get("id") == strategy_id), None)
        if index is None:
            raise ValueError("Strategy not found.")
        existing = stored["strategies"][index]
        strategy = _stamp_id(normalize_strategy(payload, existing), strategy_id)
        strategy["last_backtest"] = existing.get("last_backtest")
        _reject_duplicate_name(stored["strategies"], strategy["name"], strategy_id)
        stored["strategies"][index] = strategy
        _write(path, stored)
    _upsert_aggregate(user_id, strategy)
    return strategy


def delete_strategy(user_id: str, strategy_id: str) -> None:
    path = _file(user_id)
    with _locked(path):
        stored = _read(path)
        remaining = [item for item in stored["strategies"] if item.get("id") != strategy_id]
        if len(remaining) == len(stored["strategies"]):
            raise ValueError("Strategy not found.")
        stored["strategies"] = remaining
        _write(path, stored)
    _drop_aggregate(_anon_id(user_id, strategy_id))


def save_backtest(user_id: str, strategy_id: str, result: Dict[str, Any]) -> Dict[str, Any]:
    path = _file(user_id)
    with _locked(path):
        stored = _read(path)
        index = next((i for i, item in enumerate(stored["strategies"]) if item.get("id") == strategy_id), None)
        if index is None:
            raise ValueError("Strategy not found.")
        strategy = stored["strategies"][index]
        strategy["last_backtest"] = result
        stored["strategies"][index] = strategy
        _write(path, stored)
    _upsert_aggregate(user_id, strategy)
    return strategy


def viewer_anon_ids(user_id: str) -> set:
    return {_anon_id(user_id, strategy["id"]) for strategy in list_strategies(user_id) if strategy.get("id")}


def _real_summary(strategy: Dict[str, Any]) -> Dict[str, Any]:
    """Filled in by the caller via attach_real_summary before upsert when
    known. Missing means this save did not recompute closed trades."""
    summary = strategy.get("_aggregate_real")
    if isinstance(summary, dict):
        return summary
    return {"trade_count": 0, "win_count": 0, "avg_return_percent": None}


def attach_real_summary(strategy: Dict[str, Any], returns: List[float]) -> Dict[str, Any]:
    wins = [value for value in returns if value > 0]
    strategy = dict(strategy)
    strategy["_aggregate_real"] = {
        "trade_count": len(returns),
        "win_count": len(wins),
        "avg_return_percent": round(sum(returns) / len(returns), 2) if returns else None,
    }
    return strategy


def _aggregate_row(user_id: str, strategy: Dict[str, Any]) -> Dict[str, Any]:
    backtest = strategy.get("last_backtest") or {}
    combined = backtest.get("combined") or {}
    real = _real_summary(strategy)
    trade_count = int(combined.get("trade_count") or 0)
    return {
        "anon_id": _anon_id(user_id, strategy["id"]),
        "style": infer_style(strategy),
        "direction": strategy.get("direction") or "long",
        "timeframe": strategy.get("timeframe") or "daily",
        "hold_bucket": hold_bucket(int(strategy.get("max_hold_days") or 5)),
        "instrument": strategy.get("instruments") or "equity",
        "backtest_trade_count": trade_count,
        "backtest_avg_return_percent": combined.get("avg_return_percent") if trade_count else None,
        "real_trade_count": int(real.get("trade_count") or 0),
        "real_win_count": int(real.get("win_count") or 0),
        "real_avg_return_percent": real.get("avg_return_percent"),
    }


def _read_aggregate() -> List[Dict[str, Any]]:
    if not AGGREGATE_FILE.exists():
        return []
    try:
        data = json.loads(AGGREGATE_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    rows = data.get("rows") if isinstance(data, dict) else None
    return [row for row in rows if isinstance(row, dict) and row.get("anon_id")] if isinstance(rows, list) else []


def _write_aggregate(rows: List[Dict[str, Any]]) -> None:
    _write(AGGREGATE_FILE, {"rows": rows})


def _upsert_aggregate(user_id: str, strategy: Dict[str, Any]) -> None:
    row = _aggregate_row(user_id, strategy)
    with _locked(AGGREGATE_FILE):
        rows = [item for item in _read_aggregate() if item.get("anon_id") != row["anon_id"]]
        rows.append(row)
        _write_aggregate(rows)


def _drop_aggregate(anon_id: str) -> None:
    with _locked(AGGREGATE_FILE):
        rows = [item for item in _read_aggregate() if item.get("anon_id") != anon_id]
        _write_aggregate(rows)


def aggregate_rows() -> List[Dict[str, Any]]:
    with _locked(AGGREGATE_FILE):
        return _read_aggregate()


def paper_catalog(user_id: str) -> List[Dict[str, Any]]:
    """Rules this user opted into paper scoring. Empty unless the flag is on.

    Returned for a future approval-mode integration. The autonomous scan
    does not call this function.
    """
    if not scoring_enabled():
        return []
    catalog = []
    for strategy in list_strategies(user_id):
        if not strategy.get("paper_opt_in"):
            continue
        catalog.append(
            {
                "id": strategy["id"],
                "name": strategy["name"],
                "direction": strategy["direction"],
                "timeframe": strategy["timeframe"],
                "instruments": strategy["instruments"],
                "entry_rules": strategy["entry_rules"],
                "stop_percent": strategy["stop_percent"],
                "target": strategy["target"],
                "max_hold_days": strategy["max_hold_days"],
                "participation": "paper_approval",
                "places_orders": False,
            }
        )
    return catalog


def refresh_aggregate_real(user_id: str, strategy: Dict[str, Any], returns: List[float]) -> None:
    stamped = attach_real_summary(strategy, returns)
    _upsert_aggregate(user_id, stamped)

