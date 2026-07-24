"""Resolve observability log paths (repo-root relative, cwd-independent)."""

from __future__ import annotations

import os
from pathlib import Path

_ROOT_ENV = "TRADE_STACK_ROOT"
_EVENTS_ENV = "TRADE_OBSERVABILITY_EVENTS_PATH"
_ISSUES_ENV = "TRADE_OBSERVABILITY_ISSUES_PATH"
_DIR_ENV = "TRADE_OBSERVABILITY_DIR"


def trade_stack_root() -> Path:
    if custom := os.getenv(_ROOT_ENV, "").strip():
        return Path(custom).expanduser().resolve()
    # integrations/trade_integrations/observability/paths.py -> parents[3]
    return Path(__file__).resolve().parents[3]


def observability_dir() -> Path:
    if custom := os.getenv(_DIR_ENV, "").strip():
        path = Path(custom).expanduser()
        if not path.is_absolute():
            path = trade_stack_root() / path
        return path.resolve()
    return trade_stack_root() / "log" / "observability"


def events_path() -> Path:
    if custom := os.getenv(_EVENTS_ENV, "").strip():
        path = Path(custom).expanduser()
        if not path.is_absolute():
            path = trade_stack_root() / path
        return path.resolve()
    return observability_dir() / "events.jsonl"


def issues_path() -> Path:
    if custom := os.getenv(_ISSUES_ENV, "").strip():
        path = Path(custom).expanduser()
        if not path.is_absolute():
            path = trade_stack_root() / path
        return path.resolve()
    return observability_dir() / "issues.jsonl"
