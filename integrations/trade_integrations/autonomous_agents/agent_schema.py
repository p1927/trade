"""Agent JSON schema helpers — lifecycle fields and session backfill."""

from __future__ import annotations

from typing import Any


def default_agent_lifecycle() -> dict[str, Any]:
    from trade_integrations.autonomous_agents.lifecycle import default_lifecycle

    return default_lifecycle()


def lifecycle_needs_backfill(agent: dict[str, Any]) -> bool:
    if str(agent.get("status") or "") == "draft":
        return False
    lifecycle = agent.get("lifecycle")
    if not isinstance(lifecycle, dict):
        return True
    return not str(lifecycle.get("state") or "").strip()


def merge_lifecycle_from_session(agent: dict[str, Any]) -> dict[str, Any] | None:
    """Copy lifecycle from legacy auto_paper session JSON when agent.lifecycle is empty."""
    agent_id = str(agent.get("id") or "").strip()
    if not agent_id or not lifecycle_needs_backfill(agent):
        return None
    try:
        from trade_integrations.autonomous_agents.lifecycle import load_lifecycle
        from trade_integrations.auto_paper.session_store import load_session

        session = load_session(autonomous_agent_id=agent_id)
        if not session:
            return None
        session_lifecycle = load_lifecycle(session)
        if not isinstance(session_lifecycle, dict) or not session_lifecycle:
            return None
        merged = dict(agent)
        merged["lifecycle"] = session_lifecycle
        return merged
    except Exception:
        return None


def ensure_agent_lifecycle(agent: dict[str, Any], *, persist: bool = False) -> dict[str, Any]:
    """Ensure active agents have a lifecycle block; backfill from session once."""
    if str(agent.get("status") or "") == "draft":
        return agent
    if not lifecycle_needs_backfill(agent):
        return agent
    from_session = merge_lifecycle_from_session(agent)
    if from_session is not None:
        if persist:
            from trade_integrations.autonomous_agents.store import save_agent

            save_agent(from_session)
        return from_session
    agent = dict(agent)
    agent["lifecycle"] = default_agent_lifecycle()
    if persist:
        from trade_integrations.autonomous_agents.store import save_agent

        save_agent(agent)
    return agent
