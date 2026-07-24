"""Apply AgentIntent onto MandateConfig — single mapping authority."""

from __future__ import annotations

from trade_integrations.autonomous_agents.intent_merge import instruments_to_allowed_instruments
from trade_integrations.autonomous_agents.intent_schema import AgentIntent
from trade_integrations.autonomous_agents.mandate_config import MandateConfig


def apply_intent_to_mandate(cfg: MandateConfig, intent: AgentIntent) -> MandateConfig:
    """Sync legacy mandate fields from unified intent."""
    if intent.engagement == "observe":
        cfg.agent_mode = "observe"
        cfg.revision_policy = "user_guidance_only"
        cfg.max_open_positions = 0
    elif intent.engagement == "trade":
        cfg.agent_mode = "trade"

    allowed = instruments_to_allowed_instruments(intent.instruments)
    if allowed:
        cfg.allowed_instruments = allowed

    if intent.confidence_threshold:
        cfg.confidence_threshold = max(0, min(100, int(intent.confidence_threshold)))

    return cfg


def intent_suppresses_default_spot_alert(intent: AgentIntent) -> bool:
    """Skip silent 0.5% spot_move when user gave explicit watch conditions without move thresholds."""
    if not intent.watch_conditions:
        return False
    if not intent.clarified.get("watch_conditions") and not intent.clarified.get("schedules"):
        return False
    for cond in intent.watch_conditions:
        if cond.kind in {"price_move", "price_level", "vix", "volume", "oi", "composite"}:
            return False
    return True
