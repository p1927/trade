"""Fail-closed guards for India autonomous agents on the Nautilus bridge path."""

from __future__ import annotations

from typing import Any


def resolve_agent_profile(agent_id: str | None) -> Any | None:
    if not agent_id:
        return None
    from trade_integrations.autonomous_agents.store import get_agent
    from trade_integrations.execution.profile import resolve_profile

    agent = get_agent(agent_id.strip())
    if not agent:
        return None
    return resolve_profile(agent=agent)


def is_bridge_autonomous_agent(agent_id: str | None) -> bool:
    profile = resolve_agent_profile(agent_id)
    return profile is not None and profile.uses_nautilus_handoff


def bridge_watch_required() -> bool:
    try:
        from nautilus_openalgo_bridge.config import is_watch_enabled

        return is_watch_enabled()
    except ImportError:
        import os

        raw = os.getenv("NAUTILUS_WATCH_ENABLE", "true").strip().lower()
        return raw not in {"0", "false", "no", "off"}
