"""Load per-agent mandate limits for Nautilus risk gates (hub JSON, no store import)."""

from __future__ import annotations

from typing import Any

from nautilus_openalgo_bridge.hub_paths import load_agent_json


def agent_constraints(agent_id: str | None) -> dict[str, Any]:
    if not agent_id:
        return {}
    agent = load_agent_json(agent_id) or {}
    raw = agent.get("constraints")
    return dict(raw) if isinstance(raw, dict) else {}


def max_daily_loss_for_agent(agent_id: str | None, *, default: float = 2_000.0) -> float:
    constraints = agent_constraints(agent_id)
    try:
        return float(constraints.get("max_daily_loss_inr") or default)
    except (TypeError, ValueError):
        return default


def max_open_positions_for_agent(agent_id: str | None, *, default: int = 3) -> int:
    constraints = agent_constraints(agent_id)
    try:
        return max(1, int(constraints.get("max_open_positions") or default))
    except (TypeError, ValueError):
        return default


def agent_market_code(agent_id: str | None) -> str:
    if not agent_id:
        return "IN"
    agent = load_agent_json(agent_id) or {}
    mc = agent.get("mandate_config") if isinstance(agent.get("mandate_config"), dict) else {}
    market = str(agent.get("execution_market") or mc.get("execution_market") or "IN").upper()
    symbols = [str(s).upper() for s in (agent.get("symbols") or [])]
    if market == "US" or any(s in {"SPY", "QQQ", "AAPL", "MSFT", "TSLA"} for s in symbols):
        return "US"
    return "IN"
