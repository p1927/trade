"""Shared default trading connector profile inference (Trade + Vibe)."""

from __future__ import annotations

import os

OPENALGO_PAPER_PROFILE_ID = "openalgo-paper-sdk"
OPENALGO_LIVE_READONLY_PROFILE_ID = "openalgo-live-sdk-readonly"
ALPACA_PAPER_PROFILE_ID = "alpaca-paper-sdk"
DEFAULT_PROFILE_ID = "ibkr-paper-local"


def paper_mode_env_enabled() -> bool:
    """Ops deploy guard: when true, live execution paths require analyze mode."""
    paper_mode = os.getenv("OPENALGO_PAPER_MODE", "true").strip().lower()
    return paper_mode in ("1", "true", "yes")


def infer_default_profile_id() -> str:
    """Pick a sensible default when no profile has been saved yet."""
    openalgo_key = os.getenv("OPENALGO_API_KEY", "").strip()
    openalgo_host = (os.getenv("OPENALGO_HOST") or "http://127.0.0.1:5001").strip()
    if openalgo_key and openalgo_host:
        paper_mode = os.getenv("OPENALGO_PAPER_MODE", "true").strip().lower()
        if paper_mode in ("0", "false", "no", "off"):
            return OPENALGO_LIVE_READONLY_PROFILE_ID
        return OPENALGO_PAPER_PROFILE_ID

    alpaca_key = os.getenv("ALPACA_API_KEY", "").strip()
    alpaca_secret = (
        os.getenv("ALPACA_API_SECRET", "").strip()
        or os.getenv("ALPACA_SECRET_KEY", "").strip()
    )
    if alpaca_key and alpaca_secret:
        return ALPACA_PAPER_PROFILE_ID

    return DEFAULT_PROFILE_ID
