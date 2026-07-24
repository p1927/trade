"""Session/agent hooks for refreshing persisted agent intent."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def refresh_intent_for_session_config(
    session_config: dict[str, Any],
    user_message: str,
    *,
    source_message_id: str = "",
    use_llm: bool | None = None,
) -> dict[str, Any]:
    """Return updated session.config with merged mandate_config.intent."""
    from trade_integrations.autonomous_agents.intent_extractor import extract_agent_intent
    from trade_integrations.autonomous_agents.intent_store import (
        load_intent_from_session_config,
        persist_intent_on_session_config,
    )

    prior = load_intent_from_session_config(session_config)
    result = extract_agent_intent(
        user_message,
        prior=prior,
        source_message_id=source_message_id,
        use_llm=use_llm,
    )
    return persist_intent_on_session_config(session_config, result.intent)


def refresh_intent_for_agent_record(
    agent: dict[str, Any],
    user_message: str,
    *,
    source_message_id: str = "",
    use_llm: bool | None = None,
) -> dict[str, Any]:
    from trade_integrations.autonomous_agents.intent_extractor import extract_agent_intent
    from trade_integrations.autonomous_agents.intent_store import (
        load_intent_from_agent,
        persist_intent_on_agent,
    )

    prior = load_intent_from_agent(agent)
    result = extract_agent_intent(
        user_message,
        prior=prior,
        source_message_id=source_message_id,
        use_llm=use_llm,
    )
    return persist_intent_on_agent(agent, result.intent)


def maybe_refresh_intent_on_user_message(
    session_config: dict[str, Any] | None,
    user_message: str,
    *,
    source_message_id: str = "",
) -> dict[str, Any] | None:
    """Refresh intent for orchestrator or autonomous agent sessions."""
    cfg = dict(session_config or {})
    kind = str(cfg.get("session_kind") or "").strip()
    if kind not in {"autonomous_orchestrator", "autonomous_agent"}:
        return None
    try:
        updated = refresh_intent_for_session_config(
            cfg,
            user_message,
            source_message_id=source_message_id,
        )
        if kind == "autonomous_agent":
            agent_id = str(cfg.get("autonomous_agent_id") or "").strip()
            if agent_id:
                from trade_integrations.autonomous_agents.store import get_agent, save_agent

                agent = get_agent(agent_id)
                if agent:
                    save_agent(refresh_intent_for_agent_record(agent, user_message, source_message_id=source_message_id))
        return updated
    except Exception:
        logger.debug("maybe_refresh_intent_on_user_message failed", exc_info=True)
        return None
