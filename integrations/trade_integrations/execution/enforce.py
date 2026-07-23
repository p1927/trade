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


_BLOCKED_DIRECT_ORDER_TOOLS = frozenset(
    {
        "place_basket_order",
        "mcp_openalgo_place_basket_order",
        "execute_autonomous_basket",
        "mcp_openalgo_execute_autonomous_basket",
    }
)


def assert_direct_order_tool_allowed(
    *,
    tool_name: str,
    session_kind: str | None = None,
    autonomous_agent_id: str | None = None,
) -> None:
    """Hard-fail raw basket placement when bridge/autonomous agent is active."""
    slug = tool_name.removeprefix("mcp_openalgo_")
    if tool_name not in _BLOCKED_DIRECT_ORDER_TOOLS and slug not in _BLOCKED_DIRECT_ORDER_TOOLS:
        return
    if session_kind == "autonomous_agent" or is_bridge_autonomous_agent(autonomous_agent_id):
        raise PermissionError(
            "Direct basket order blocked for autonomous/bridge agents — "
            "use plan approval flow or bridge intent queue"
        )


def bridge_watch_required() -> bool:
    try:
        from nautilus_openalgo_bridge.config import is_watch_enabled

        return is_watch_enabled()
    except ImportError:
        import os

        raw = os.getenv("NAUTILUS_WATCH_ENABLE", "true").strip().lower()
        return raw not in {"0", "false", "no", "off"}
