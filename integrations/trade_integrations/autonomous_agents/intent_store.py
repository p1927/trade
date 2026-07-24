"""Load and persist AgentIntent on session config and agent records."""

from __future__ import annotations

from typing import Any

from trade_integrations.autonomous_agents.intent_schema import AgentIntent


def load_intent_from_mandate_config(mandate_config: dict[str, Any] | None) -> AgentIntent | None:
    if not isinstance(mandate_config, dict):
        return None
    raw = mandate_config.get("intent")
    if not isinstance(raw, dict):
        return None
    return AgentIntent.from_dict(raw)


def load_intent_from_session_config(session_config: dict[str, Any] | None) -> AgentIntent | None:
    cfg = dict(session_config or {})
    mc = cfg.get("mandate_config") if isinstance(cfg.get("mandate_config"), dict) else {}
    intent = load_intent_from_mandate_config(mc)
    if intent:
        return intent
    symbols = [str(s).strip().upper() for s in (cfg.get("watchlist") or []) if str(s).strip()]
    if symbols:
        from trade_integrations.autonomous_agents.intent_schema import default_agent_intent

        return default_agent_intent(symbols=symbols)
    return None


def load_intent_from_agent(agent: dict[str, Any] | None) -> AgentIntent | None:
    if not isinstance(agent, dict):
        return None
    mc = agent.get("mandate_config") if isinstance(agent.get("mandate_config"), dict) else {}
    intent = load_intent_from_mandate_config(mc)
    if intent:
        return intent
    symbols = [str(s).strip().upper() for s in (agent.get("symbols") or []) if str(s).strip()]
    if symbols:
        from trade_integrations.autonomous_agents.intent_schema import default_agent_intent

        return default_agent_intent(symbols=symbols)
    return None


def embed_intent_in_mandate_config(
    mandate_config: dict[str, Any] | None,
    intent: AgentIntent,
) -> dict[str, Any]:
    mc = dict(mandate_config or {})
    mc["intent"] = intent.to_dict()
    mc["agent_mode"] = intent.engagement
    from trade_integrations.autonomous_agents.intent_merge import instruments_to_allowed_instruments

    allowed = instruments_to_allowed_instruments(intent.instruments)
    if allowed:
        mc["allowed_instruments"] = allowed
    return mc


def persist_intent_on_session_config(
    session_config: dict[str, Any],
    intent: AgentIntent,
) -> dict[str, Any]:
    cfg = dict(session_config or {})
    mc = cfg.get("mandate_config") if isinstance(cfg.get("mandate_config"), dict) else {}
    cfg["mandate_config"] = embed_intent_in_mandate_config(mc, intent)
    if intent.symbols:
        cfg["watchlist"] = list(intent.symbols)
    return cfg


def persist_intent_on_agent(agent: dict[str, Any], intent: AgentIntent) -> dict[str, Any]:
    updated = dict(agent)
    mc = updated.get("mandate_config") if isinstance(updated.get("mandate_config"), dict) else {}
    updated["mandate_config"] = embed_intent_in_mandate_config(mc, intent)
    if intent.symbols:
        updated["symbols"] = list(intent.symbols)
    updated["intent"] = intent.to_dict()
    return updated
