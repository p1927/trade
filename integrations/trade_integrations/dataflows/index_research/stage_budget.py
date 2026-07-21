"""Per-stage wall-clock budgets for index research pipeline runs."""

from __future__ import annotations

import os
import threading
import time
from collections import defaultdict
from typing import Any

from trade_integrations.dataflows.index_research.pipeline_cancel import PipelineCancelledError

_STAGE_STACK: dict[str, list[float]] = defaultdict(list)
_LOCK = threading.Lock()

_DEFAULT_BUDGETS_S: dict[str, float] = {
    "constituents": 1800.0,
    "forecast_lab": 120.0,
    "alpha_zoo": 60.0,
    "predict": 90.0,
    "news_impact": 120.0,
    "ohlcv_cache": 60.0,
    "momentum": 60.0,
    "spot": 30.0,
    "explain": 60.0,
}


def _env_budget(stage: str) -> float | None:
    key = f"INDEX_PREDICTION_STAGE_BUDGET_{stage.upper()}_S"
    raw = os.getenv(key, "").strip()
    if not raw:
        return _DEFAULT_BUDGETS_S.get(stage)
    try:
        return float(raw)
    except ValueError:
        return _DEFAULT_BUDGETS_S.get(stage)


def begin_stage(stage: str, *, token: str | None = None) -> str:
    """Push a monotonic start time. Returns the budget token (supports nesting)."""
    key = token or stage
    with _LOCK:
        _STAGE_STACK[key].append(time.monotonic())
    return key


def end_stage(token: str) -> None:
    with _LOCK:
        stack = _STAGE_STACK.get(token)
        if stack:
            stack.pop()
        if not stack:
            _STAGE_STACK.pop(token, None)


def check_stage_budget(stage: str, *, token: str | None = None, extra: dict[str, Any] | None = None) -> None:
    """Raise PipelineCancelledError when *stage* exceeds its configured budget."""
    budget_s = _env_budget(stage)
    if budget_s is None or budget_s <= 0:
        return
    key = token or stage
    with _LOCK:
        stack = _STAGE_STACK.get(key)
        start = stack[-1] if stack else None
    if start is None:
        return
    elapsed = time.monotonic() - start
    if elapsed <= budget_s:
        return
    detail = f" ({int(elapsed)}s > {int(budget_s)}s budget)"
    if extra:
        detail += f" {extra}"
    raise PipelineCancelledError(f"stage_budget_exceeded:{stage}{detail}")
