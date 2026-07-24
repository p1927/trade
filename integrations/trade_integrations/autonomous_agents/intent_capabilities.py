"""Single capability gate for autonomous agents — widgets, payoff, execution."""

from __future__ import annotations

from typing import Any, Literal

from trade_integrations.autonomous_agents.intent_merge import derive_capabilities
from trade_integrations.autonomous_agents.intent_schema import AgentIntent
from trade_integrations.autonomous_agents.mandate_enforcer import MandateViolation

CapabilityAction = Literal[
    "widgets",
    "payoff",
    "charges",
    "execution",
    "index_outlook",
]

_WIDGET_TOOL_SUBSTRINGS = (
    "get_options_trade_widget",
    "get_stock_trade_widget",
    "get_index_trade_widget",
)

_EXECUTE_TOOL_SUBSTRINGS = (
    "execute_autonomous_basket",
    "place_order",
)


def _intent_from_session_config(session_config: dict[str, Any] | None) -> AgentIntent | None:
    cfg = session_config or {}
    mc = cfg.get("mandate_config") if isinstance(cfg.get("mandate_config"), dict) else {}
    raw = mc.get("intent") if isinstance(mc.get("intent"), dict) else None
    if not isinstance(raw, dict):
        return None
    return AgentIntent.from_dict(raw)


def resolve_capabilities(
    *,
    session_config: dict[str, Any] | None = None,
    agent: dict[str, Any] | None = None,
    intent: AgentIntent | None = None,
) -> dict[str, bool]:
    """Resolve effective capability flags from intent (derive when missing on stored intent)."""
    resolved: AgentIntent | None = intent
    if resolved is None and agent is not None:
        from trade_integrations.autonomous_agents.intent_store import load_intent_from_agent

        mc = agent.get("mandate_config") if isinstance(agent.get("mandate_config"), dict) else {}
        if isinstance(mc.get("intent"), dict):
            resolved = AgentIntent.from_dict(mc["intent"])
        else:
            resolved = load_intent_from_agent(agent)
    if resolved is None and session_config is not None:
        resolved = _intent_from_session_config(session_config)
    if resolved is None:
        cfg = session_config or {}
        if str(cfg.get("agent_mode") or "").lower() == "observe":
            return derive_capabilities(
                AgentIntent(engagement="observe", instruments=["index"], symbols=list(cfg.get("symbols") or []))
            )
        return derive_capabilities(AgentIntent())
    caps = dict(resolved.capabilities or {})
    if not caps:
        caps = derive_capabilities(resolved)
    return caps


def prefetch_widget_intent_allowed(widget_intent: str, caps: dict[str, bool]) -> bool:
    """Whether prefetch/auto-emit may use this widget intent class."""
    if not caps.get("widgets"):
        return False
    intent_key = str(widget_intent or "none").strip().lower()
    if intent_key in {"none", ""}:
        return False
    if intent_key in {"options_strategy", "execute_refresh"}:
        return bool(caps.get("payoff"))
    if intent_key == "index_outlook":
        return bool(caps.get("index_outlook"))
    if intent_key == "stock_trade":
        return bool(caps.get("charges") or caps.get("execution"))
    return False


def presentation_sections_for_capabilities(caps: dict[str, bool]) -> dict[str, bool]:
    """Frontend section visibility from capabilities."""
    return {
        "widgets": bool(caps.get("widgets")),
        "payoff": bool(caps.get("payoff")),
        "charges": bool(caps.get("charges")),
        "execution": bool(caps.get("execution")),
        "index_outlook": bool(caps.get("index_outlook")),
    }


def assert_capabilities_allow(
    action: CapabilityAction,
    *,
    session_config: dict[str, Any] | None = None,
    agent: dict[str, Any] | None = None,
    intent: AgentIntent | None = None,
) -> None:
    """Raise MandateViolation when capability gate blocks an action."""
    caps = resolve_capabilities(session_config=session_config, agent=agent, intent=intent)
    if action == "widgets" and not caps.get("widgets"):
        raise MandateViolation(
            "capabilities_widgets",
            "Agent intent does not allow trade-plan widgets.",
        )
    if action == "payoff" and not caps.get("payoff"):
        raise MandateViolation(
            "capabilities_payoff",
            "Agent intent does not allow options payoff widgets.",
        )
    if action == "charges" and not caps.get("charges"):
        raise MandateViolation(
            "capabilities_charges",
            "Agent intent does not allow charge breakdown widgets.",
        )
    if action == "execution" and not caps.get("execution"):
        raise MandateViolation(
            "capabilities_execution",
            "Agent intent does not allow autonomous execution.",
        )
    if action == "index_outlook" and not caps.get("index_outlook"):
        raise MandateViolation(
            "capabilities_index_outlook",
            "Agent intent does not allow index outlook widgets.",
        )


def is_tool_allowed_for_capabilities(tool_name: str, caps: dict[str, bool]) -> bool:
    """Filter autonomous agent tool registry by capabilities."""
    name = str(tool_name or "").strip().lower()
    if not name:
        return True
    if not caps.get("widgets"):
        if any(sub in name for sub in _WIDGET_TOOL_SUBSTRINGS):
            return False
    elif not caps.get("payoff"):
        if "options_trade_widget" in name:
            return False
    if not caps.get("execution"):
        if any(sub in name for sub in _EXECUTE_TOOL_SUBSTRINGS):
            return False
    return True


def attach_capabilities_metadata(
    payload: dict[str, Any],
    *,
    session_config: dict[str, Any] | None = None,
    agent: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Attach capability snapshot for frontend gating."""
    caps = resolve_capabilities(session_config=session_config, agent=agent)
    payload["agent_capabilities"] = presentation_sections_for_capabilities(caps)
    return payload


def summarize_intent_change(prior: AgentIntent | None, current: AgentIntent) -> dict[str, Any] | None:
    """Build user-visible summary when intent materially changes."""
    if prior is None:
        return None
    parts: list[str] = []
    if str(prior.engagement or "") != str(current.engagement or ""):
        parts.append(f"mode → **{current.engagement}**")
    if list(prior.instruments or []) != list(current.instruments or []):
        inst = ", ".join(current.instruments or []) or "unspecified"
        parts.append(f"instruments → **{inst}**")
    prior_caps = derive_capabilities(prior)
    current_caps = derive_capabilities(current)
    cap_labels = {
        "widgets": "widgets",
        "payoff": "payoff chart",
        "execution": "execution",
        "index_outlook": "index outlook",
    }
    for key, label in cap_labels.items():
        if bool(prior_caps.get(key)) != bool(current_caps.get(key)):
            state = "on" if current_caps.get(key) else "off"
            parts.append(f"{label} **{state}**")
    if not parts:
        return None
    return {
        "engagement": current.engagement,
        "instruments": list(current.instruments or []),
        "capabilities": current_caps,
        "summary": "Intent updated: " + "; ".join(parts),
    }
