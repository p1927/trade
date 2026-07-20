"""In-process circuit breaker for fragile data vendors."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from trade_integrations.context.hub import get_hub_dir
from trade_integrations.dataflows.company_research.sources.resilience import (
    classify_error,
    remediation_for,
)

logger = logging.getLogger(__name__)

_HEALTH_REL = Path("_data") / "source_health.json"
_RATE_LIMIT_CODES = frozenset({"vendor_rate_limited", "tapetide_rate_limited"})

_lock = threading.Lock()
_cache: dict[str, "_CircuitState"] = {}
_loaded = False


class SourceStatus(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    RATE_LIMITED = "rate_limited"


@dataclass
class _CircuitState:
    failure_count: int = 0
    last_error_code: str = ""
    status: SourceStatus = SourceStatus.AVAILABLE
    open_until: float = 0.0
    circuit_logged: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "failure_count": self.failure_count,
            "last_error_code": self.last_error_code,
            "status": self.status.value,
            "open_until": self.open_until,
            "circuit_logged": self.circuit_logged,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> _CircuitState:
        status_raw = str(data.get("status") or SourceStatus.AVAILABLE.value)
        try:
            status = SourceStatus(status_raw)
        except ValueError:
            status = SourceStatus.AVAILABLE
        return cls(
            failure_count=int(data.get("failure_count") or 0),
            last_error_code=str(data.get("last_error_code") or ""),
            status=status,
            open_until=float(data.get("open_until") or 0.0),
            circuit_logged=bool(data.get("circuit_logged")),
        )


def _make_key(vendor: str, capability: str) -> str:
    return f"{vendor.strip()}:{capability.strip()}"


def _failure_threshold() -> int:
    raw = os.getenv("SOURCE_AVAIL_FAILURE_THRESHOLD", "3").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 3


def _cooldown_sec(error_code: str) -> float:
    if error_code in _RATE_LIMIT_CODES:
        raw = os.getenv("SOURCE_AVAIL_RATE_LIMIT_COOLDOWN_SEC", "3600")
    elif error_code == "not_installed":
        raw = os.getenv("SOURCE_AVAIL_NOT_INSTALLED_COOLDOWN_SEC", "86400")
    else:
        raw = os.getenv("SOURCE_AVAIL_COOLDOWN_SEC", "300")
    try:
        return max(0.0, float(raw))
    except ValueError:
        if error_code in _RATE_LIMIT_CODES:
            return 3600.0
        if error_code == "not_installed":
            return 86400.0
        return 300.0


def _open_threshold(error_code: str) -> int:
    if error_code in _RATE_LIMIT_CODES or error_code == "not_installed":
        return 1
    return _failure_threshold()


def _health_path() -> Path:
    return get_hub_dir() / _HEALTH_REL


def _ensure_loaded() -> None:
    global _loaded
    if _loaded:
        return
    path = _health_path()
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for key, value in raw.items():
                    if isinstance(value, dict):
                        _cache[key] = _CircuitState.from_dict(value)
        except (OSError, json.JSONDecodeError) as exc:
            logger.debug("Could not load source health cache: %s", exc)
    _loaded = True


def _persist() -> None:
    path = _health_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {key: state.to_dict() for key, state in _cache.items()}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _get_state(key: str) -> _CircuitState:
    _ensure_loaded()
    if key not in _cache:
        _cache[key] = _CircuitState()
    return _cache[key]


def _refresh_if_expired(state: _CircuitState, *, now: float | None = None) -> None:
    if state.status == SourceStatus.AVAILABLE:
        return
    ts = now if now is not None else time.time()
    if state.open_until <= ts:
        state.status = SourceStatus.AVAILABLE
        state.failure_count = 0
        state.circuit_logged = False


def _is_blocking(state: _CircuitState, *, now: float | None = None) -> bool:
    _refresh_if_expired(state, now=now)
    return state.status != SourceStatus.AVAILABLE


def should_attempt(vendor: str, capability: str) -> bool:
    """Return False when the vendor/capability circuit is open."""
    key = _make_key(vendor, capability)
    with _lock:
        state = _get_state(key)
        if _is_blocking(state):
            logger.debug(
                "Skipping %s — circuit %s until %.0f (%s)",
                key,
                state.status.value,
                state.open_until,
                state.last_error_code or "unknown",
            )
            return False
        return True


def record_success(vendor: str, capability: str) -> None:
    """Close the circuit after a successful fetch."""
    key = _make_key(vendor, capability)
    with _lock:
        state = _get_state(key)
        state.failure_count = 0
        state.last_error_code = ""
        state.status = SourceStatus.AVAILABLE
        state.open_until = 0.0
        state.circuit_logged = False
        _persist()


def record_failure(vendor: str, capability: str, exc: Exception | str) -> None:
    """Record a failure; may open the circuit depending on error classification."""
    key = _make_key(vendor, capability)
    code = classify_error(exc)
    now = time.time()
    with _lock:
        state = _get_state(key)
        _refresh_if_expired(state, now=now)
        state.failure_count += 1
        state.last_error_code = code

        if state.failure_count >= _open_threshold(code):
            state.status = (
                SourceStatus.RATE_LIMITED
                if code in _RATE_LIMIT_CODES
                else SourceStatus.UNAVAILABLE
            )
            state.open_until = now + _cooldown_sec(code)
            if not state.circuit_logged:
                logger.info(
                    "Source circuit open for %s: %s — %s",
                    key,
                    code,
                    remediation_for(code),
                )
                state.circuit_logged = True
        _persist()


def get_status(vendor: str, capability: str) -> SourceStatus:
    key = _make_key(vendor, capability)
    with _lock:
        state = _get_state(key)
        if _is_blocking(state):
            return state.status
        return SourceStatus.AVAILABLE


def list_all_statuses() -> dict[str, SourceStatus]:
    with _lock:
        _ensure_loaded()
        now = time.time()
        out: dict[str, SourceStatus] = {}
        for key, state in _cache.items():
            if _is_blocking(state, now=now):
                out[key] = state.status
            else:
                out[key] = SourceStatus.AVAILABLE
        return out


def clear_availability_cache() -> None:
    """Reset in-memory state and remove hub persistence (for tests)."""
    global _loaded
    with _lock:
        _cache.clear()
        _loaded = False
        path = _health_path()
        if path.is_file():
            try:
                path.unlink()
            except OSError as exc:
                logger.debug("Could not remove source health cache: %s", exc)
