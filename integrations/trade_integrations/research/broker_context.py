"""Resolve broker preset for charge calculation."""

from __future__ import annotations

import os

from trade_integrations.dataflows.broker_charges.calculate import normalize_broker_id


def resolve_broker_preset(*, openalgo_session_broker: str | None = None) -> str:
    """OpenAlgo session broker → env → presets default (indmoney)."""
    if openalgo_session_broker:
        return normalize_broker_id(openalgo_session_broker)
    env_broker = os.getenv("TRADINGAGENTS_OPTIONS_BROKER_PRESET")
    if env_broker:
        return normalize_broker_id(env_broker)
    return normalize_broker_id(None)
