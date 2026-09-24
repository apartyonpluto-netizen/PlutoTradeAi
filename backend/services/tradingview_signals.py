from __future__ import annotations

import hashlib
import json
import math
import os
import queue
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from backend.services.market_data_service import normalize_ticker, validate_ticker
from backend.services.storage import CrossProcessRLock, StorageCorruptionError, atomic_write_json, data_file, finite_number, read_json_strict


ALLOWED_DIRECTIONS = {"LONG", "SHORT", "EXIT", "WATCH"}
ALLOWED_STATUSES = {
    "RECEIVED",
    "VALIDATED",
    "REJECTED",
    "ANALYZING",
    "AWAITING_APPROVAL",
    "APPROVED",
    "DECLINED",
    "PAPER_EXECUTED",
    "EXPIRED",
    "FAILED",
}
# States from which a human may still decline. APPROVED is excluded on purpose: once an
# approval has been claimed, execution may already be running, so declining would race it.
DECLINABLE_STATUSES = {"RECEIVED", "VALIDATED", "ANALYZING", "AWAITING_APPROVAL"}
STALE_FRESHNESS = {"STALE", "UNAVAILABLE"}
TIMEFRAME_PATTERN = re.compile(r"^[A-Za-z0-9]{1,12}$")
STRATEGY_PATTERN = re.compile(r"^[A-Za-z0-9_.\-]{2,80}$")
SIGNAL_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:+\-]{1,120}$")
MAX_INDICATOR_JSON_BYTES = 8192
MAX_RECOVERY_ATTEMPTS = 3


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def _sha256_text(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _parse_iso_time(value: Any) -> Optional[datetime]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (ValueError, OverflowError, OSError):
            return None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        try:
            return datetime.fromtimestamp(float(text), tz=timezone.utc)
        except (ValueError, OverflowError, OSError):
            return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _json_safe(value: Any) -> Any:
    """Recursively replace non-finite floats so analysis output can always be persisted."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if value is None or isinstance(value, (str, int, bool)):
        return value
    return str(value)


class TradingViewSignalService:
    def __init__(
        self,
        analyzer: Callable[[Dict[str, Any]], Dict[str, Any]],
        data_path: Optional[str] = None,
        approvals_path: Optional[str] = None,
        webhook_secret: Optional[str] = None,
        max_age_seconds: Optional[int] = None,
        approval_max_age_seconds: Optional[int] = None,
        analysis_timeout_seconds: Optional[int] = None,
    ) -> None:
        self._analyzer = analyzer
        self._data_path = Path(data_path) if data_path else data_file("PLUTO_SIGNALS_FILE", "signals.json")
        self._approvals_path = Path(approvals_path) if approvals_path else data_file("PLUTO_SIGNAL_APPROVALS_FILE", "signal_approvals.json")
        self._webhook_secret = (webhook_secret if webhook_secret is not None else os.getenv("TRADINGVIEW_WEBHOOK_SECRET", "")).strip()
        self._max_age_seconds = max(30, self._int_setting(max_age_seconds, "TRADINGVIEW_WEBHOOK_MAX_AGE_SECONDS", 300))
        self._approval_max_age_seconds = max(0, self._int_setting(approval_max_age_seconds, "PLUTO_SIGNAL_APPROVAL_MAX_AGE_SECONDS", 3600))
        self._analysis_timeout_seconds = max(30, self._int_setting(analysis_timeout_seconds, "PLUTO_SIGNAL_ANALYSIS_TIMEOUT_SECONDS", 180))
        # One lock (thread + process) guards the signals file and the approvals audit file.
        self._lock = CrossProcessRLock(self._data_path.with_name(f"{self._data_path.name}.lock"))
        self._queue: "queue.Queue[str]" = queue.Queue()
        self._stop_event = threading.Event()
        self._worker: Optional[threading.Thread] = None

    @staticmethod
    def _int_setting(explicit: Optional[int], env_name: str, default: int) -> int:
        if explicit is not None:
            return int(explicit)
        try:
            return int(float(os.getenv(env_name, str(default))))
        except (TypeError, ValueError):
            return default

    # ------------------------------------------------------------------ worker

    def start_worker(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._stop_event.clear()
        self._worker = threading.Thread(target=self._worker_loop, name="tradingview-signal-worker", daemon=True)
        self._worker.start()

    def stop_worker(self) -> None:
        self._stop_event.set()
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=2.0)

    def _worker_loop(self) -> None:
        last_recovery = time.monotonic()
        while not self._stop_event.is_set():
            if time.monotonic() - last_recovery > 30:
                last_recovery = time.monotonic()
                try:
                    self.recover_pending()
                except Exception:  # noqa: BLE001 - the worker must survive a bad pass
                    pass
            try:
                signal_id = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self.process_signal(signal_id)
            except Exception:  # noqa: BLE001 - one poisoned signal must not kill the worker
                pass
            finally:
                self._queue.task_done()

    def recover_pending(self, *, process_inline: bool = False) -> List[str]:
        """Re-queue signals that were accepted but never finished analysis.

        Covers a restart (or crash) between webhook acknowledgement and analysis:
        VALIDATED/RECEIVED signals are queued again and ANALYZING signals whose claim is
        older than the analysis timeout are released back to VALIDATED (bounded retries)."""
        recovered: List[str] = []
        with self._lock:
            db = self._load_signals()
            changed = False
            now = utc_now()
            for row in db["signals"]:
                status = row.get("status")
                if status == "ANALYZING":
                    started = _parse_iso_time(row.get("analysis_started_at"))
                    if started is not None and (now - started).total_seconds() < self._analysis_timeout_seconds:
                        continue
                    attempts = int(row.get("analysis_attempts") or 0)
                    if attempts >= MAX_RECOVERY_ATTEMPTS:
                        row["status"] = "FAILED"
                        row["status_reason"] = "analysis was interrupted repeatedly and gave up"
                        changed = True
                        continue
                    row["status"] = "VALIDATED"
                    row["status_reason"] = "analysis interrupted; re-queued after restart"
                    changed = True
                    recovered.append(str(row.get("signal_id")))
                elif status in {"RECEIVED", "VALIDATED"}:
                    recovered.append(str(row.get("signal_id")))
            if changed:
                self._save_signals(db)
        for signal_id in recovered:
            if process_inline:
                self.process_signal(signal_id)
            else:
                self._queue.put(signal_id)
        return recovered

    # ----------------------------------------------------------------- storage

    def _load_signals(self) -> Dict[str, Any]:
        base = read_json_strict(self._data_path, lambda: {"signals": []})
        if not isinstance(base, dict):
            raise StorageCorruptionError(f"{self._data_path} does not contain a JSON object")
        if not isinstance(base.get("signals"), list):
            base["signals"] = []
        return base

    def _save_signals(self, payload: Dict[str, Any]) -> None:
        atomic_write_json(self._data_path, payload)

    def _load_approvals(self) -> Dict[str, Any]:
        base = read_json_strict(self._approvals_path, lambda: {"events": []})
        if not isinstance(base, dict):
            base = {"events": []}
        if not isinstance(base.get("events"), list):
            base["events"] = []
        return base

    def _save_approvals(self, payload: Dict[str, Any]) -> None:
        atomic_write_json(self._approvals_path, payload)

    def _append_event_locked(self, event: Dict[str, Any]) -> None:
        db = self._load_approvals()
        events = db["events"]
        events.append(dict(event))
        db["events"] = events[-5000:]
        self._save_approvals(db)

    # -------------------------------------------------------------- validation

    def _extract_secret(self, payload: Dict[str, Any]) -> Tuple[str, str]:
        header_secret = ""
        body_secret = ""
        raw_header = payload.get("_header_secret")
        if raw_header is not None:
            header_secret = str(raw_header).strip()
        for key in ("secret", "token", "webhook_secret"):
            if payload.get(key) is not None:
                body_secret = str(payload.get(key)).strip()
                break
        return header_secret, body_secret

    def _sanitized_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        cleaned = dict(payload)
        cleaned.pop("_header_secret", None)
        for key in ("secret", "token", "webhook_secret"):
            if key in cleaned:
                cleaned[key] = "REDACTED"
        return cleaned

    def _payload_hash(self, payload: Dict[str, Any]) -> str:
        stable = json.dumps(self._sanitized_payload(payload), sort_keys=True, separators=(",", ":"))
        return _sha256_text(stable)

    def _next_signal_id(self, payload: Dict[str, Any], bar_time: datetime, payload_hash: str) -> str:
        raw = payload.get("signal_id")
        if raw is not None and str(raw).strip():
            return str(raw).strip()
        symbol = normalize_ticker(payload.get("symbol") or "") or "UNKNOWN"
        timeframe = str(payload.get("timeframe") or "NA").upper()
        return f"{symbol}-{timeframe}-{bar_time.strftime('%Y%m%dT%H%M%SZ')}-{payload_hash[:12]}"

    def _validate_strategy(self, strategy: Any) -> Tuple[bool, Optional[str]]:
        text = strategy.strip() if isinstance(strategy, str) else ""
        if not text:
            return False, "strategy is required"
        if not STRATEGY_PATTERN.match(text):
            return False, "strategy format is invalid"
        return True, None

    def _validate_timeframe(self, timeframe: Any) -> Tuple[bool, Optional[str]]:
        text = str(timeframe).strip() if isinstance(timeframe, (str, int)) and not isinstance(timeframe, bool) else ""
        if not text:
            return False, "timeframe is required"
        if not TIMEFRAME_PATTERN.match(text):
            return False, "timeframe format is invalid"
        return True, None

    def _validate_direction(self, direction: Any) -> Tuple[bool, Optional[str]]:
        text = direction.strip().upper() if isinstance(direction, str) else ""
        if text not in ALLOWED_DIRECTIONS:
            return False, f"direction must be one of: {', '.join(sorted(ALLOWED_DIRECTIONS))}"
        return True, None

    def _validate_timestamp(self, payload: Dict[str, Any]) -> Tuple[bool, Optional[str], Optional[datetime]]:
        bar_time_raw = payload.get("bar_time") or payload.get("timestamp")
        parsed = _parse_iso_time(bar_time_raw)
        if parsed is None:
            return False, "bar_time is required and must be a valid timestamp", None
        age = abs((utc_now() - parsed).total_seconds())
        if age > self._max_age_seconds:
            return False, f"signal timestamp is outside tolerance of {self._max_age_seconds}s", parsed
        return True, None, parsed

    def _validate_levels(self, payload: Dict[str, Any], direction: str) -> Tuple[Optional[str], Dict[str, Optional[float]]]:
        """price/stop_loss/take_profit must be finite and positive; for entries the stop and
        target must sit on the correct side of price."""
        values: Dict[str, Optional[float]] = {}
        for field in ("price", "stop_loss", "take_profit"):
            raw = payload.get(field)
            if raw is None or (isinstance(raw, str) and not raw.strip()):
                values[field] = None
                continue
            number = finite_number(raw)
            if number is None or number <= 0:
                return f"{field} must be a finite number greater than zero", values
            values[field] = number
        if direction in {"LONG", "SHORT"}:
            price, stop, target = values["price"], values["stop_loss"], values["take_profit"]
            if price is None:
                return "price is required for LONG and SHORT signals", values
            if direction == "LONG":
                if stop is not None and stop >= price:
                    return "stop_loss must be below price for a LONG signal", values
                if target is not None and target <= price:
                    return "take_profit must be above price for a LONG signal", values
            else:
                if stop is not None and stop <= price:
                    return "stop_loss must be above price for a SHORT signal", values
                if target is not None and target >= price:
                    return "take_profit must be below price for a SHORT signal", values
        return None, values

    def _authenticate_payload(self, payload: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        if not self._webhook_secret:
            return False, "webhook secret is not configured"
        header_secret, body_secret = self._extract_secret(payload)
        provided = header_secret or body_secret
        if not provided:
            return False, "missing webhook secret"
        if not secrets.compare_digest(provided.encode("utf-8"), self._webhook_secret.encode("utf-8")):
            return False, "invalid webhook secret"
        return True, None

    # ------------------------------------------------------------------ intake

    def normalize_and_store(self, payload: Dict[str, Any]) -> Tuple[bool, int, Dict[str, Any]]:
        if not isinstance(payload, dict):
            return False, 400, {"success": False, "error": "payload must be a JSON object"}

        auth_ok, auth_error = self._authenticate_payload(payload)
        if not auth_ok:
            code = 503 if auth_error == "webhook secret is not configured" else 401
            return False, code, {"success": False, "error": auth_error}

        raw_symbol = payload.get("symbol")
        if not isinstance(raw_symbol, str):
            return False, 400, {"success": False, "error": "symbol must be a string"}
        symbol = normalize_ticker(raw_symbol)
        is_valid_symbol, symbol_error = validate_ticker(symbol)
        if not is_valid_symbol:
            return False, 400, {"success": False, "error": symbol_error}

        strategy_ok, strategy_error = self._validate_strategy(payload.get("strategy"))
        if not strategy_ok:
            return False, 400, {"success": False, "error": strategy_error}

        direction_ok, direction_error = self._validate_direction(payload.get("direction"))
        if not direction_ok:
            return False, 400, {"success": False, "error": direction_error}
        direction = str(payload.get("direction")).strip().upper()

        timeframe_ok, timeframe_error = self._validate_timeframe(payload.get("timeframe"))
        if not timeframe_ok:
            return False, 400, {"success": False, "error": timeframe_error}

        timestamp_ok, timestamp_error, parsed_time = self._validate_timestamp(payload)
        if not timestamp_ok or parsed_time is None:
            return False, 400, {"success": False, "error": timestamp_error}

        level_error, levels = self._validate_levels(payload, direction)
        if level_error:
            return False, 400, {"success": False, "error": level_error}

        hint = payload.get("confidence_hint")
        confidence_hint: Optional[float] = None
        if hint is not None and not (isinstance(hint, str) and not hint.strip()):
            confidence_hint = finite_number(hint)
            if confidence_hint is None or not 0 <= confidence_hint <= 100:
                return False, 400, {"success": False, "error": "confidence_hint must be a finite number between 0 and 100"}

        indicators = payload.get("indicator_values")
        indicators = indicators if isinstance(indicators, dict) else {}
        try:
            if len(json.dumps(indicators, allow_nan=False)) > MAX_INDICATOR_JSON_BYTES:
                return False, 400, {"success": False, "error": "indicator_values is too large"}
            clean_payload = self._sanitized_payload(payload)
            json.dumps(clean_payload, allow_nan=False)
        except (TypeError, ValueError):
            return False, 400, {"success": False, "error": "payload contains non-finite or non-serialisable values"}

        raw_signal_id = payload.get("signal_id")
        if raw_signal_id is not None and (not isinstance(raw_signal_id, str) or not SIGNAL_ID_PATTERN.match(raw_signal_id.strip())):
            return False, 400, {"success": False, "error": "signal_id may only contain letters, digits and _ . : + - (max 120)"}

        payload_hash = self._payload_hash(clean_payload)
        bar_time_iso = parsed_time.isoformat()
        signal_id = self._next_signal_id(payload, parsed_time, payload_hash)

        with self._lock:
            db = self._load_signals()
            signals = db["signals"]

            if any(str(row.get("signal_id")) == signal_id for row in signals):
                return False, 409, {"success": False, "error": "duplicate signal_id", "signal_id": signal_id}
            if any(str(row.get("raw_payload_hash")) == payload_hash for row in signals):
                return False, 409, {"success": False, "error": "duplicate payload", "signal_id": signal_id}

            signals.append(
                {
                    "signal_id": signal_id,
                    "source": "TRADINGVIEW",
                    "symbol": symbol,
                    "exchange": str(payload.get("exchange") or "UNKNOWN").upper()[:32],
                    "asset_type": str(payload.get("asset_type") or "STOCK").upper()[:32],
                    "strategy": str(payload.get("strategy")).strip(),
                    "direction": direction,
                    "timeframe": str(payload.get("timeframe")).strip().upper(),
                    "price": levels["price"],
                    "stop_loss": levels["stop_loss"],
                    "take_profit": levels["take_profit"],
                    "confidence_hint": confidence_hint,
                    "indicator_values": indicators,
                    "bar_time": bar_time_iso,
                    "received_at": utc_now_iso(),
                    "raw_payload_hash": payload_hash,
                    "status": "VALIDATED",
                    "status_reason": None,
                    "analysis": None,
                    "approval": None,
                    "raw_payload": clean_payload,
                }
            )
            self._save_signals(db)

        self._queue.put(signal_id)
        return True, 202, {
            "success": True,
            "data": {
                "signal_id": signal_id,
                "status": "VALIDATED",
                "queued": True,
            },
            "error": None,
            "timestamp": utc_now_iso(),
        }

    # ---------------------------------------------------------------- analysis

    def process_signal(self, signal_id: str) -> None:
        with self._lock:
            db = self._load_signals()
            target = next((row for row in db["signals"] if str(row.get("signal_id")) == str(signal_id)), None)
            if not target or target.get("status") not in {"RECEIVED", "VALIDATED"}:
                return
            target["status"] = "ANALYZING"
            target["analysis_started_at"] = utc_now_iso()
            target["analysis_attempts"] = int(target.get("analysis_attempts") or 0) + 1
            self._save_signals(db)
            snapshot = dict(target)

        try:
            analysis = self._analyzer(snapshot)
            if not isinstance(analysis, dict):
                raise ValueError("analyzer returned a non-object result")
            analysis = _json_safe(analysis)
            next_status = "AWAITING_APPROVAL"
            reason = None
            if analysis.get("warnings") and analysis.get("data_freshness") in STALE_FRESHNESS:
                reason = "analysis generated with stale or unavailable backend data"
        except Exception as exc:  # noqa: BLE001 - analysis failure is recorded, never raised
            analysis = {"error": str(exc), "warnings": ["analysis failed"], "data_freshness": "UNAVAILABLE"}
            next_status = "FAILED"
            reason = str(exc)

        with self._lock:
            db = self._load_signals()
            target = next((row for row in db["signals"] if str(row.get("signal_id")) == str(signal_id)), None)
            # Only the ANALYZING claim may be completed: a decline (or any later decision) that
            # landed while the analyzer was running must never be overwritten by its result.
            if not target or target.get("status") != "ANALYZING":
                return
            target["analysis"] = analysis
            target["status"] = next_status
            target["status_reason"] = reason
            self._save_signals(db)

    # --------------------------------------------------------------- decisions

    def begin_approval(
        self,
        signal_id: str,
        approved_by: str,
        note: Optional[str] = None,
        *,
        acknowledge_stale: bool = False,
    ) -> Tuple[str, Optional[Dict[str, Any]], str]:
        """Atomically claim an approval. Returns (outcome, signal, message) where outcome is one of
        OK, RESUME, ALREADY_EXECUTED, NOT_FOUND, NOT_READY, CONFLICT, EXPIRED, STALE_DATA."""
        with self._lock:
            db = self._load_signals()
            row = next((item for item in db["signals"] if str(item.get("signal_id")) == str(signal_id)), None)
            if not row:
                return "NOT_FOUND", None, "Signal not found"
            status = row.get("status")
            if status == "PAPER_EXECUTED":
                return "ALREADY_EXECUTED", dict(row), "Signal was already executed"
            if status == "APPROVED":
                return "RESUME", dict(row), "Approval already recorded; re-driving idempotent execution"
            if status in {"RECEIVED", "VALIDATED", "ANALYZING"}:
                return "NOT_READY", dict(row), f"Signal analysis is not finished (status {status})"
            if status != "AWAITING_APPROVAL":
                return "CONFLICT", dict(row), f"Signal cannot be approved from status {status}"

            if self._approval_max_age_seconds:
                received = _parse_iso_time(row.get("received_at"))
                if received is not None and (utc_now() - received).total_seconds() > self._approval_max_age_seconds:
                    row["status"] = "EXPIRED"
                    row["status_reason"] = f"not approved within {self._approval_max_age_seconds}s of receipt"
                    self._save_signals(db)
                    self._append_event_locked({"signal_id": signal_id, "decision": "EXPIRED", "approved_by": approved_by, "timestamp": utc_now_iso()})
                    return "EXPIRED", dict(row), row["status_reason"]

            freshness = str((row.get("analysis") or {}).get("data_freshness") or "UNAVAILABLE").upper()
            if freshness in STALE_FRESHNESS and not acknowledge_stale:
                return "STALE_DATA", dict(row), f"Analysis used {freshness} market data; resubmit with acknowledge_stale=true to approve anyway"

            row["status"] = "APPROVED"
            row["status_reason"] = None
            row["approval"] = {
                "decision": "APPROVE",
                "approved_by": approved_by,
                "note": note,
                "acknowledged_stale_data": bool(freshness in STALE_FRESHNESS),
                "timestamp": utc_now_iso(),
            }
            self._save_signals(db)
            self._append_event_locked({"signal_id": signal_id, "decision": "APPROVED", "approved_by": approved_by, "note": note, "timestamp": utc_now_iso()})
            return "OK", dict(row), "Approval recorded"

    def finish_execution(
        self,
        signal_id: str,
        *,
        executed: bool,
        message: str,
        position: Optional[Dict[str, Any]] = None,
        retryable: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Record the outcome of an approved signal. Only an APPROVED signal can transition."""
        with self._lock:
            db = self._load_signals()
            row = next((item for item in db["signals"] if str(item.get("signal_id")) == str(signal_id)), None)
            if not row or row.get("status") != "APPROVED":
                return dict(row) if row else None
            approver = (row.get("approval") or {}).get("approved_by")
            if executed:
                row["status"] = "PAPER_EXECUTED"
                row["status_reason"] = None
                row["paper_position"] = position
                row["executed_at"] = utc_now_iso()
            else:
                row["status"] = "AWAITING_APPROVAL" if retryable else "FAILED"
                row["status_reason"] = message
                row["approval"] = None if retryable else row.get("approval")
                self._append_event_locked({"signal_id": signal_id, "decision": "APPROVE_FAILED", "approved_by": approver, "message": message, "retryable": retryable, "timestamp": utc_now_iso()})
            self._save_signals(db)
            return dict(row)

    def decline(self, signal_id: str, declined_by: str, reason: str) -> Tuple[str, Optional[Dict[str, Any]], str]:
        """Atomically decline. Outcomes: OK, ALREADY_DECLINED, NOT_FOUND, CONFLICT."""
        with self._lock:
            db = self._load_signals()
            row = next((item for item in db["signals"] if str(item.get("signal_id")) == str(signal_id)), None)
            if not row:
                return "NOT_FOUND", None, "Signal not found"
            status = row.get("status")
            if status == "DECLINED":
                return "ALREADY_DECLINED", dict(row), "Signal was already declined"
            if status not in DECLINABLE_STATUSES:
                return "CONFLICT", dict(row), f"Signal cannot be declined from status {status}"
            row["status"] = "DECLINED"
            row["approval"] = {"decision": "DECLINE", "declined_by": declined_by, "reason": reason, "timestamp": utc_now_iso()}
            self._save_signals(db)
            self._append_event_locked({"signal_id": signal_id, "decision": "DECLINED", "declined_by": declined_by, "reason": reason, "timestamp": utc_now_iso()})
            return "OK", dict(row), "Signal declined"

    def revert_interrupted_approval(self, signal_id: str, message: str) -> bool:
        """Startup recovery: an APPROVED signal with no execution goes back to the human queue."""
        with self._lock:
            db = self._load_signals()
            row = next((item for item in db["signals"] if str(item.get("signal_id")) == str(signal_id)), None)
            if not row or row.get("status") != "APPROVED":
                return False
            row["status"] = "AWAITING_APPROVAL"
            row["status_reason"] = message
            row["approval"] = None
            self._save_signals(db)
            self._append_event_locked({"signal_id": signal_id, "decision": "APPROVAL_REVERTED", "message": message, "timestamp": utc_now_iso()})
            return True

    # ------------------------------------------------------------------- reads

    def list_signals(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self._lock:
            rows = list(self._load_signals()["signals"])
        rows.sort(key=lambda row: row.get("received_at") or "", reverse=True)
        return rows[: max(1, min(int(limit), 500))]

    def get_signal(self, signal_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            for row in self._load_signals()["signals"]:
                if str(row.get("signal_id")) == str(signal_id):
                    return dict(row)
        return None

    def update_signal(self, signal_id: str, mutate: Callable[[Dict[str, Any]], Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        with self._lock:
            db = self._load_signals()
            rows = db["signals"]
            for idx, row in enumerate(rows):
                if str(row.get("signal_id")) != str(signal_id):
                    continue
                updated = mutate(dict(row))
                rows[idx] = updated
                self._save_signals(db)
                return dict(updated)
        return None

    def record_approval_event(self, event: Dict[str, Any]) -> None:
        with self._lock:
            self._append_event_locked(event)
