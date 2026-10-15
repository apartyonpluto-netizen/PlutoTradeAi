"""Calibrates confidence. It does not choose the trade.

Training replays daily bars with features taken only from the slice available
at that bar. Rows are ordered by time. The older portion trains; the newer
portion is the only one that can admit the model. There is no shuffle across
time. If the newer portion does not clear the same out-of-sample bar as the
backtest gate, live scoring ignores the model and keeps the current score.

Training is start_training / the offline script. The monitor loop does not call it.
"""

from __future__ import annotations

import json
import os
import pickle
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("PLUTO_DATA_DIR", str(BASE_DIR / "data"))).resolve()
MODEL_FILE = DATA_DIR / "research" / "ml_confidence_model.pkl"
META_FILE = DATA_DIR / "research" / "ml_confidence.json"

FEATURE_ORDER = (
    "confidence",
    "day_change_percent",
    "rsi_14",
    "relative_volume",
    "ema9_distance",
    "ema20_distance",
    "ema50_distance",
    "gap_percent",
    "direction_long",
)
SPLIT_FRACTION = 0.6
WEAK_PROBABILITY = 0.45
ALPACA_PAUSE_SECONDS = 0.4
DEFAULT_MAX_SYMBOLS = 12
DEFAULT_LOOKBACK_MONTHS = 18


def _num(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number:
        return default
    return number


def features_as_of(frame: pd.DataFrame, index: int) -> Dict[str, float]:
    """Indicators at `index` using only rows 0..index. Later rows are ignored."""
    if index < 0 or index >= len(frame):
        return {}
    view = frame.iloc[: index + 1]
    close = pd.to_numeric(view["Close"], errors="coerce")
    if close.isna().all() or len(close) < 20:
        return {}
    last = float(close.iloc[-1])
    prev = float(close.iloc[-2]) if len(close) > 1 else last
    if last == 0:
        return {}
    ema9 = float(close.ewm(span=9, adjust=False).mean().iloc[-1])
    ema20 = float(close.ewm(span=20, adjust=False).mean().iloc[-1])
    ema50 = float(close.ewm(span=50, adjust=False).mean().iloc[-1]) if len(close) >= 50 else ema20
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean().iloc[-1]
    loss = (-delta.clip(upper=0)).rolling(14).mean().iloc[-1]
    if pd.isna(gain) or pd.isna(loss) or float(loss) == 0:
        rsi = 50.0
    else:
        rsi = 100.0 - (100.0 / (1.0 + float(gain) / float(loss)))
    volume = pd.to_numeric(view["Volume"], errors="coerce") if "Volume" in view.columns else None
    relative = 1.0
    if volume is not None and len(volume) >= 20:
        baseline = float(volume.rolling(20).mean().iloc[-1] or 0)
        if baseline > 0:
            relative = float(volume.iloc[-1]) / baseline
    gap = 0.0
    if "Open" in view.columns and len(view) > 1:
        opened = float(pd.to_numeric(view["Open"], errors="coerce").iloc[-1])
        prior_close = float(close.iloc[-2])
        if prior_close:
            gap = (opened / prior_close - 1.0) * 100.0
    return {
        "day_change_percent": (last / prev - 1.0) * 100.0 if prev else 0.0,
        "rsi_14": rsi,
        "relative_volume": relative,
        "ema9_distance": (last - ema9) / last,
        "ema20_distance": (last - ema20) / last,
        "ema50_distance": (last - ema50) / last,
        "gap_percent": gap,
    }


def vector(features: Dict[str, Any], confidence: float, direction_long: float) -> List[float]:
    values = {
        "confidence": _num(confidence),
        "day_change_percent": _num(features.get("day_change_percent")),
        "rsi_14": _num(features.get("rsi_14"), 50.0),
        "relative_volume": _num(features.get("relative_volume"), 1.0),
        "ema9_distance": _num(features.get("ema9_distance")),
        "ema20_distance": _num(features.get("ema20_distance")),
        "ema50_distance": _num(features.get("ema50_distance")),
        "gap_percent": _num(features.get("gap_percent")),
        "direction_long": _num(direction_long),
    }
    return [values[name] for name in FEATURE_ORDER]


def chronological_split(rows: Sequence[Dict[str, Any]], fraction: float = SPLIT_FRACTION) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Train on older rows, test on strictly newer rows. Input order is ignored."""
    ordered = sorted(rows, key=lambda row: str(row.get("entry_date") or ""))
    if len(ordered) < 4:
        return list(ordered), []
    dates = [str(row.get("entry_date") or "") for row in ordered]
    cutoff = dates[int((len(dates) - 1) * fraction)]
    train = [row for row in ordered if str(row.get("entry_date") or "") <= cutoff]
    test = [row for row in ordered if str(row.get("entry_date") or "") > cutoff]
    if not test:
        train, test = ordered[:-1], ordered[-1:]
    return train, test


def _load_meta() -> Dict[str, Any]:
    try:
        data = json.loads(META_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _store(model: Any, meta: Dict[str, Any]) -> None:
    MODEL_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp_model = MODEL_FILE.with_name(f"{MODEL_FILE.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:6]}")
    with tmp_model.open("wb") as handle:
        pickle.dump(model, handle)
    os.replace(tmp_model, MODEL_FILE)
    tmp_meta = META_FILE.with_name(f"{META_FILE.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:6]}")
    tmp_meta.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    os.replace(tmp_meta, META_FILE)


def _model():
    if not MODEL_FILE.exists():
        return None
    try:
        with MODEL_FILE.open("rb") as handle:
            return pickle.load(handle)
    except (OSError, pickle.UnpicklingError, EOFError):
        return None


def admitted() -> bool:
    return bool(_load_meta().get("admitted"))


def _live_armed() -> bool:
    try:
        from integrations.webull import is_live_trading_armed
    except ImportError:  # pragma: no cover
        from .integrations.webull import is_live_trading_armed  # type: ignore
    return bool(is_live_trading_armed())


def _probability(features: Dict[str, Any], confidence: float, direction_long: float) -> Optional[float]:
    if _live_armed() or not admitted():
        return None
    model = _model()
    if model is None:
        return None
    try:
        proba = model.predict_proba([vector(features, confidence, direction_long)])[0]
    except Exception:
        return None
    classes = list(getattr(model, "classes_", []))
    if 1 not in classes:
        return None
    return float(proba[classes.index(1)])


def adjust_confidence(strategy: str, raw_score: int, features: Optional[Dict[str, Any]] = None, *, direction_long: float = 1.0) -> int:
    """Blend the current score with the model. The strategy name is not chosen here."""
    del strategy  # the caller already picked the strategy
    raw = int(raw_score)
    probability = _probability(features or {}, raw, direction_long)
    if probability is None:
        return raw
    blended = round(0.5 * raw + 0.5 * (100.0 * probability))
    return max(1, min(99, blended))


def filter_reason(opportunity: Dict[str, Any]) -> Optional[str]:
    """A weak setup the model is allowed to filter. None when the model has no say."""
    if not admitted() or _live_armed():
        return None
    features = opportunity_features(opportunity)
    side = str(opportunity.get("recommendation") or "").upper()
    probability = _probability(features, _num(opportunity.get("confidence")), 0.0 if side == "PUT" else 1.0)
    if probability is None or probability >= WEAK_PROBABILITY:
        return None
    return f"confidence model marks this setup as weak ({probability:.0%} chance of a positive net return)"


def opportunity_features(opportunity: Dict[str, Any]) -> Dict[str, Any]:
    """Distances the model was trained on, taken from fields already on the setup."""
    context = opportunity.get("market_context") if isinstance(opportunity.get("market_context"), dict) else {}
    features = dict(context)
    price = _num(features.get("current_price"))
    if price:
        for source, dest in (("ema_9", "ema9_distance"), ("ema_20", "ema20_distance"), ("ema_50", "ema50_distance")):
            if features.get(dest) is None and features.get(source) is not None:
                features[dest] = (price - _num(features.get(source))) / price
    return features


def train_from_rows(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Fit on older rows and admit only if the newer rows clear the out-of-sample gate."""
    from evidence_gates import _passes, summarize

    train, test = chronological_split(list(rows))
    meta: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "admitted": False,
        "reason": "",
        "train_until": max((str(row.get("entry_date") or "") for row in train), default=""),
        "test_from": min((str(row.get("entry_date") or "") for row in test), default=""),
        "train_rows": len(train),
        "test_rows": len(test),
        "feature_order": list(FEATURE_ORDER),
    }
    if meta["train_until"] and meta["test_from"] and meta["train_until"] >= meta["test_from"]:
        meta["reason"] = "refusing to train: the test period is not strictly newer than the train period"
        _store(None, meta)
        return meta
    labels = [1 if float(row.get("net_return_percent") or 0) > 0 else 0 for row in train]
    if len(set(labels)) < 2 or len(test) < 5:
        meta["reason"] = "not enough older wins and losses, or not enough newer rows, to test the model"
        _store(None, meta)
        return meta
    try:
        from sklearn.ensemble import GradientBoostingClassifier
    except ImportError as error:
        meta["reason"] = f"scikit-learn is not installed ({error})"
        _store(None, meta)
        return meta
    model = GradientBoostingClassifier(random_state=0, n_estimators=60, max_depth=2, learning_rate=0.05)
    model.fit(
        [vector(row.get("features") or {}, _num(row.get("confidence")), _num(row.get("direction_long"), 1.0)) for row in train],
        labels,
    )
    kept_returns = []
    classes = list(model.classes_)
    for row in test:
        proba = model.predict_proba([
            vector(row.get("features") or {}, _num(row.get("confidence")), _num(row.get("direction_long"), 1.0))
        ])[0]
        probability = float(proba[classes.index(1)]) if 1 in classes else 0.0
        if probability >= WEAK_PROBABILITY:
            kept_returns.append(float(row.get("net_return_percent") or 0))
    if not kept_returns:
        verdict_passed, verdict_reason = False, "the model kept no newer setups"
        stats: Dict[str, Any] = {"n": 0}
    else:
        stats = summarize(kept_returns)
        verdict_passed, verdict_reason = _passes(stats)
    meta["oos"] = stats
    meta["admitted"] = bool(verdict_passed)
    meta["reason"] = verdict_reason or ""
    if not meta["admitted"]:
        meta["reason"] = "model not admitted: " + str(meta["reason"])
    _store(model if meta["admitted"] else None, meta)
    return meta


def replay_frame(frame: pd.DataFrame, ticker: str, *, hold_days: int = 5, brain=None) -> List[Dict[str, Any]]:
    """Graded setups from one daily frame. Features come from bars up to the signal bar."""
    if brain is None:
        try:
            from brains.strategy_brain import build_strategy_intelligence
        except ImportError:  # pragma: no cover
            from .brains.strategy_brain import build_strategy_intelligence  # type: ignore
        brain = build_strategy_intelligence
    rows: List[Dict[str, Any]] = []
    if frame is None or len(frame) < 90:
        return rows
    last = len(frame) - hold_days - 1
    for index in range(80, last):
        sliced = frame.iloc[: index + 1]
        try:
            result = brain(ticker, daily=sliced, intraday=pd.DataFrame(), backtest_mode=True)
        except Exception:
            continue
        if not isinstance(result, dict) or result.get("insufficient_data"):
            continue
        if str(result.get("recommendation") or "") not in {"CALL", "PUT"}:
            continue
        entry = float(frame["Open"].iloc[index + 1])
        exit_price = float(frame["Close"].iloc[index + 1 + hold_days])
        if entry <= 0:
            continue
        sign = -1.0 if result.get("recommendation") == "PUT" else 1.0
        gross = sign * (exit_price - entry) / entry * 100.0
        from evidence_gates import net_return_percent
        features = features_as_of(frame, index)
        features["confidence"] = int(result.get("strategy_confidence") or 0)
        date = frame.index[index]
        entry_date = date.date().isoformat() if hasattr(date, "date") else str(date)[:10]
        rows.append({
            "ticker": ticker,
            "entry_date": entry_date,
            "strategy": result.get("best_strategy"),
            "confidence": int(result.get("strategy_confidence") or 0),
            "direction_long": 1.0 if sign > 0 else 0.0,
            "features": features,
            "net_return_percent": round(net_return_percent(gross), 4),
        })
    return rows


def start_training(symbols: Sequence[str], lookback_months: int = DEFAULT_LOOKBACK_MONTHS) -> Dict[str, Any]:
    """Background-friendly entry. One daily request per symbol, then a pause. Not for the 10s loop."""
    import threading
    import time

    clean = [symbol.strip().upper() for symbol in symbols if str(symbol).strip()][: max(1, int(os.environ.get("PLUTO_ML_MAX_SYMBOLS", str(DEFAULT_MAX_SYMBOLS))))]
    if not clean:
        raise ValueError("At least one symbol is required.")

    def _run() -> None:
        try:
            from backtest_engine import _fetch_daily_history, _normalize_ohlcv
        except ImportError:  # pragma: no cover
            from .backtest_engine import _fetch_daily_history, _normalize_ohlcv  # type: ignore
        rows: List[Dict[str, Any]] = []
        for symbol in clean:
            try:
                raw = _fetch_daily_history(symbol, lookback_months)
                frame = _normalize_ohlcv(raw, symbol)
                rows.extend(replay_frame(frame, symbol))
            except Exception:
                pass
            time.sleep(ALPACA_PAUSE_SECONDS)
        train_from_rows(rows)

    threading.Thread(target=_run, name="ml-confidence-train", daemon=True).start()
    return {
        "status": "started",
        "symbols": clean,
        "lookback_months": lookback_months,
        "message": "Training started. It does not place trades.",
    }
