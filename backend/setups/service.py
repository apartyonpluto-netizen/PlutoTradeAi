"""Fetches bars, runs the engine and evaluates opportunities for symbols,
with a short cache. Read-only market data - no broker calls."""

from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any, Dict, List, Optional, Sequence

from . import model
from .bars import Bars
from .engine import scan_bars
from .opportunities import evaluate_symbol, liquidity_from_daily

logger = logging.getLogger(__name__)

DEFAULT_TIMEFRAMES = ("1d", "5m")
PERIOD_FOR = {"1d": "1y", "1h": "1mo", "30m": "5d", "15m": "5d", "5m": "2d", "1m": "1d"}
CACHE_SECONDS = {"1d": 300, "1h": 120}
INTRADAY_CACHE_SECONDS = 60
CHART_BARS = 160

_cache: Dict[tuple, Dict[str, Any]] = {}
_lock = threading.Lock()


def _alpaca():
    try:
        from integrations import alpaca_data
    except ImportError:  # pragma: no cover
        from ..integrations import alpaca_data  # type: ignore
    return alpaca_data


def fetch_bars(symbols: Sequence[str], timeframe: str) -> Dict[str, Bars]:
    """Bars per symbol, cached briefly. Symbols with no data are absent."""
    symbols = sorted({s.strip().upper() for s in symbols if s and s.strip()})
    ttl = CACHE_SECONDS.get(timeframe, INTRADAY_CACHE_SECONDS)
    now = time.time()
    out: Dict[str, Bars] = {}
    missing = []
    with _lock:
        for symbol in symbols:
            hit = _cache.get((symbol, timeframe))
            if hit and hit["expires"] > now:
                if hit["bars"] is not None:
                    out[symbol] = hit["bars"]
            else:
                missing.append(symbol)
    if missing:
        frames = _alpaca().get_bars(missing, period=PERIOD_FOR.get(timeframe, "1y"), interval=timeframe)
        with _lock:
            for symbol in missing:
                frame = frames.get(symbol)
                bars = Bars.from_frame(symbol, timeframe, frame) if frame is not None and len(frame) else None
                _cache[(symbol, timeframe)] = {"bars": bars, "expires": now + ttl}
                if bars is not None:
                    out[symbol] = bars
    return out


def _short_error(error: Exception) -> str:
    text = re.sub(r"<[^>]+>", " ", str(error))
    return re.sub(r"\s+", " ", text).strip()[:140]


def chart_for(bars: Bars, detection: Dict[str, Any]) -> Dict[str, Any]:
    """The bars behind a detection plus its own points/lines/levels, so the
    chart draws exactly what the rules used."""
    last = len(bars) - 1
    start = max(0, min(detection["start_index"] - 15, last - CHART_BARS + 1))
    return {
        "timeframe": bars.timeframe,
        "offset": start,
        "bars": [[bars.t[i], round(float(bars.o[i]), 4), round(float(bars.h[i]), 4), round(float(bars.l[i]), 4),
                  round(float(bars.c[i]), 4), float(bars.v[i])] for i in range(start, last + 1)],
        "points": detection["points"],
        "lines": detection["lines"],
        "levels": detection["levels"],
    }


def analyze(symbols: Sequence[str], timeframes: Sequence[str] = DEFAULT_TIMEFRAMES) -> List[Dict[str, Any]]:
    symbols = [s.strip().upper() for s in symbols if s and s.strip()]
    bars_by_tf: Dict[str, Dict[str, Bars]] = {}
    errors: Dict[str, List[str]] = {s: [] for s in symbols}
    for timeframe in timeframes:
        try:
            bars_by_tf[timeframe] = fetch_bars(symbols, timeframe)
        except Exception as error:  # noqa: BLE001 - one timeframe failing must not hide the others
            logger.warning("setup bars %s failed: %s", timeframe, error)
            bars_by_tf[timeframe] = {}
            for symbol in symbols:
                errors[symbol].append(f"{timeframe} bars unavailable: {_short_error(error)}")
    results = []
    for symbol in symbols:
        scans, bars_for = [], {}
        for timeframe in timeframes:
            bars = bars_by_tf.get(timeframe, {}).get(symbol)
            if bars is None or len(bars) < 20:
                if not any(timeframe in e for e in errors[symbol]):
                    errors[symbol].append(f"not enough {timeframe} bars")
                continue
            bars_for[timeframe] = bars
            scans.append(scan_bars(bars))
        result = evaluate_symbol(scans, liquidity=liquidity_from_daily(bars_for.get("1d"))) if scans else {
            "symbol": symbol, "opportunities": [], "summary": "no data", "trade_candidate": None, "recently_ended": [], "errors": []}
        for opportunity in result["opportunities"]:
            bars = bars_for.get(opportunity["timeframe"])
            opportunity["chart"] = chart_for(bars, opportunity["primary_detection"]) if bars is not None else None
        result["data_errors"] = errors[symbol]
        result["as_of"] = {tf: b.t[-1] for tf, b in bars_for.items()}
        results.append(result)
    return results


def coverage() -> Dict[str, Any]:
    from . import evidence

    rows = []
    for spec, _ in model.registered():
        statuses = {tf: evidence.lookup(spec.id, spec.version, tf)["status"] for tf in spec.timeframes}
        rows.append({**spec.to_dict(), "family_label": model.FAMILIES[spec.family], "validation": statuses})
    rows.sort(key=lambda r: (r["family"], r["name"]))
    return {"detectors": rows, "not_supported": model.pending_definitions(), "families": model.FAMILIES,
            "evidence": evidence.latest_snapshot_summary()}


# --- historical validation runs ---------------------------------------------------

VALIDATION_MAX_AGE_SECONDS = 7 * 24 * 3600


def _validation_paths():
    from . import evidence

    root = evidence.DATA_DIR / "research"
    root.mkdir(parents=True, exist_ok=True)
    return root / "setup_validation_status.json", root / "setup_validation.lock"


def validation_status() -> Dict[str, Any]:
    import json

    from . import evidence

    status_file, _ = _validation_paths()
    try:
        status = json.loads(status_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        status = {"state": "never_run"}
    status["evidence"] = evidence.latest_snapshot_summary()
    return status


def _write_status(**fields: Any) -> None:
    import json
    import os

    status_file, _ = _validation_paths()
    tmp = status_file.with_name(status_file.name + ".tmp")
    tmp.write_text(json.dumps(fields), encoding="utf-8")
    os.replace(tmp, status_file)


def _fetch_history(symbols: List[str], timeframe: str) -> Dict[str, Bars]:
    frames = _alpaca().get_bars(symbols, period="2y", interval=timeframe)
    return {symbol: Bars.from_frame(symbol, timeframe, frame) for symbol, frame in frames.items() if frame is not None and len(frame) > 250}


def start_validation(symbols: List[str], *, reason: str) -> Dict[str, Any]:
    """Runs the walk-forward validation in a background thread. Only one run
    at a time across all workers (non-blocking flock). Read-only market data."""
    import fcntl
    from datetime import datetime, timezone

    from . import validation

    _, lock_path = _validation_paths()
    handle = open(lock_path, "a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return {"started": False, "reason": "a validation run is already in progress"}
    started_at = datetime.now(timezone.utc).isoformat()
    _write_status(state="running", started_at=started_at, reason=reason, progress="fetching bars", symbols=len(symbols))

    def work() -> None:
        try:
            snapshot = validation.run(
                symbols, fetch=_fetch_history,
                progress=lambda text: _write_status(state="running", started_at=started_at, reason=reason, progress=text, symbols=len(symbols)),
            )
            _write_status(state="done", started_at=started_at, finished_at=datetime.now(timezone.utc).isoformat(), reason=reason,
                          trades=snapshot["trades"], universe_size=snapshot["universe_size"])
        except Exception as error:  # noqa: BLE001 - recorded for the admin page
            logger.exception("setup validation run failed")
            _write_status(state="failed", started_at=started_at, finished_at=datetime.now(timezone.utc).isoformat(),
                          reason=reason, error=_short_error(error))
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()

    threading.Thread(target=work, name="setup-validation", daemon=True).start()
    return {"started": True, "started_at": started_at}


def validation_is_stale() -> bool:
    from datetime import datetime, timezone

    from . import evidence

    summary = evidence.latest_snapshot_summary()
    if not summary or not summary.get("generated_at"):
        return True
    try:
        generated = datetime.fromisoformat(str(summary["generated_at"]))
    except ValueError:
        return True
    return (datetime.now(timezone.utc) - generated).total_seconds() > VALIDATION_MAX_AGE_SECONDS
