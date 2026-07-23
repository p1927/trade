"""Resolve execution market/backend from the selected trading connector profile.

``OPENALGO_PAPER_MODE`` affects default profile inference only (via
``default_profile.infer_default_profile_id``). Runtime paper/live authority
is OpenAlgo ``analyze_mode`` exposed through ``/api/v1/marketcontext``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

MarketCode = Literal["IN", "US"]
BackendCode = Literal["openalgo", "alpaca", "connector_sdk"]
ExecutionPathCode = Literal["openalgo", "alpaca_sdk", "connector_sdk"]

CONNECTOR_EXECUTION: dict[str, tuple[MarketCode, BackendCode, ExecutionPathCode]] = {
    "openalgo": ("IN", "openalgo", "openalgo"),
    "alpaca": ("US", "alpaca", "alpaca_sdk"),
    "dhan": ("IN", "openalgo", "openalgo"),
    "shoonya": ("IN", "openalgo", "openalgo"),
    "ibkr": ("US", "connector_sdk", "connector_sdk"),
    "robinhood": ("US", "connector_sdk", "connector_sdk"),
    "tiger": ("US", "connector_sdk", "connector_sdk"),
    "longbridge": ("US", "connector_sdk", "connector_sdk"),
    "futu": ("US", "connector_sdk", "connector_sdk"),
    "trading212": ("US", "connector_sdk", "connector_sdk"),
    "okx": ("US", "connector_sdk", "connector_sdk"),
    "binance": ("US", "connector_sdk", "connector_sdk"),
}

_DEFAULT_EXECUTION: tuple[MarketCode, BackendCode, ExecutionPathCode] = (
    "IN",
    "openalgo",
    "openalgo",
)

_KNOWN_CONNECTOR_PREFIXES: tuple[str, ...] = tuple(
    sorted(CONNECTOR_EXECUTION.keys(), key=len, reverse=True)
)


def runtime_root() -> Path:
    custom = os.getenv("VIBE_TRADING_RUNTIME_ROOT", "").strip()
    if custom:
        return Path(custom).expanduser()
    return Path.home() / ".vibe-trading"


def trading_connections_path() -> Path:
    return runtime_root() / "trading-connections.json"


def connector_from_profile_id(profile_id: str) -> str:
    pid = profile_id.strip().lower()
    if not pid:
        return ""
    for prefix in _KNOWN_CONNECTOR_PREFIXES:
        if pid.startswith(f"{prefix}-"):
            return prefix
    return pid.split("-", 1)[0]


def connector_execution_market(connector: str) -> MarketCode:
    """Map connector key to IN/US; stock simulator forces IN for OpenAlgo only."""
    key = str(connector or "").strip().lower()
    if key == "openalgo":
        try:
            from trade_integrations.stock_simulator.integration import is_simulator_active

            if is_simulator_active():
                return "IN"
        except Exception:
            pass
    market, _, _ = CONNECTOR_EXECUTION.get(key, _DEFAULT_EXECUTION)
    return market


def connector_execution_backend(connector: str) -> BackendCode:
    key = str(connector or "").strip().lower()
    if key == "alpaca":
        return "openalgo"
    _, backend, _ = CONNECTOR_EXECUTION.get(key, _DEFAULT_EXECUTION)
    return backend


def connector_execution_path(connector: str) -> ExecutionPathCode:
    key = str(connector or "").strip().lower()
    if key == "alpaca":
        return "openalgo"
    _, _, execution_path = CONNECTOR_EXECUTION.get(key, _DEFAULT_EXECUTION)
    return execution_path


def load_selected_profile_id() -> str | None:
    path = trading_connections_path()
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    selected = str(payload.get("selected_profile") or "").strip().lower()
    return selected or None


@dataclass(frozen=True)
class ConnectorExecutionContext:
    profile_id: str
    connector: str
    market: MarketCode
    backend: BackendCode
    execution_path: ExecutionPathCode
    source: Literal["selected_profile", "agent_stored", "env_default"]


def _infer_default_profile_id() -> str:
    from trade_integrations.execution.default_profile import infer_default_profile_id

    return infer_default_profile_id()


def load_active_connector_context(
    *,
    agent: dict | None = None,
) -> ConnectorExecutionContext | None:
    """Load connector context from agent record or selected runtime profile."""
    profile_id = ""
    source: Literal["selected_profile", "agent_stored", "env_default"] = "selected_profile"
    if agent:
        profile_id = str(agent.get("connector_profile_id") or "").strip().lower()
        if profile_id:
            source = "agent_stored"
    if not profile_id:
        profile_id = load_selected_profile_id() or ""
    if not profile_id:
        profile_id = _infer_default_profile_id()
        source = "env_default"
    if not profile_id:
        return None
    connector = connector_from_profile_id(profile_id)
    if not connector:
        return None
    return ConnectorExecutionContext(
        profile_id=profile_id,
        connector=connector,
        market=connector_execution_market(connector),
        backend=connector_execution_backend(connector),
        execution_path=connector_execution_path(connector),
        source=source,
    )


def symbol_allowed_for_connector_market(symbol: str, market: MarketCode) -> tuple[bool, str | None]:
    """Return whether symbol is valid for connector market."""
    sym = str(symbol or "").strip().upper()
    if not sym:
        return False, "symbol is required"
    try:
        from trade_integrations.dataflows.company_research.market import Market, detect_market
        from trade_integrations.dataflows.company_research.us_symbols import is_us_known_symbol
        from trade_integrations.dataflows.symbol_registry.openalgo_registry import is_symbol_known_for_proposal

        if market == "IN":
            detected = detect_market(sym)
            if detected == Market.US and is_us_known_symbol(sym):
                return False, f"{sym} is US-listed; cannot use the India connector."
            if is_symbol_known_for_proposal(sym):
                return True, None
            if detected == Market.IN:
                return True, None
            return False, f"{sym} is not an India symbol for the OpenAlgo connector."
        if is_us_known_symbol(sym):
            return True, None
        detected = detect_market(sym)
        if detected == Market.US:
            return True, None
        return False, f"{sym} is not a US symbol for the active US connector."
    except Exception:
        return False, f"Could not validate {sym} for connector market {market}."
