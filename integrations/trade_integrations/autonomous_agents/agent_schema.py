"""Agent JSON schema helpers — lifecycle fields on agent instances."""

from __future__ import annotations

from typing import Any


def default_agent_lifecycle() -> dict[str, Any]:
    from trade_integrations.autonomous_agents.lifecycle import default_lifecycle

    return default_lifecycle()


def lifecycle_needs_backfill(agent: dict[str, Any]) -> bool:
    if str(agent.get("status") or "") == "draft":
        return False
    if agent.get("last_decision") or agent.get("decisions") or agent.get("last_agent_turn_at"):
        return False
    lifecycle = agent.get("lifecycle")
    if not isinstance(lifecycle, dict):
        return True
    state = str(lifecycle.get("state") or "").strip()
    if state and state != "IDLE":
        return False
    if lifecycle.get("tried_strategies") or lifecycle.get("active_strategy"):
        return False
    if lifecycle.get("last_transition_at"):
        return False
    return True


def ensure_agent_lifecycle(agent: dict[str, Any], *, persist: bool = False) -> dict[str, Any]:
    """Ensure active agents have a lifecycle block."""
    if str(agent.get("status") or "") == "draft":
        return agent
    if not lifecycle_needs_backfill(agent):
        return agent
    agent = dict(agent)
    agent["lifecycle"] = default_agent_lifecycle()
    if persist:
        from trade_integrations.autonomous_agents.store import save_agent

        save_agent(agent)
    return agent
