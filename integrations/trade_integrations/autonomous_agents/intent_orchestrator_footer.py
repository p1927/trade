"""Orchestrator ARQ footer — reinforce propose + intent checklist before turn ends."""

from __future__ import annotations

from typing import Any

from trade_integrations.autonomous_agents.intent_proposal import format_instrument_labels, watch_conditions_summary
from trade_integrations.autonomous_agents.intent_schema import AgentIntent


def _intent_from_session_config(session_config: dict[str, Any] | None) -> AgentIntent | None:
    cfg = session_config or {}
    mc = cfg.get("mandate_config") if isinstance(cfg.get("mandate_config"), dict) else {}
    raw = mc.get("intent") if isinstance(mc.get("intent"), dict) else None
    if not isinstance(raw, dict):
        return None
    return AgentIntent.from_dict(raw)


def format_orchestrator_arq_footer(session_config: dict[str, Any] | None) -> str:
    """Mandatory checklist appended to orchestrator system prompt."""
    intent = _intent_from_session_config(session_config)
    engagement = str(intent.engagement if intent else "unknown")
    instruments = format_instrument_labels(intent) if intent else "—"
    symbols = ", ".join(intent.symbols) if intent and intent.symbols else "—"
    needs = ", ".join(intent.needs_clarification) if intent and intent.needs_clarification else "none"
    watch_lines = watch_conditions_summary(intent)
    watch_text = "; ".join(watch_lines) if watch_lines else "none specified"
    caps = dict(intent.capabilities or {}) if intent else {}
    if intent and not caps:
        from trade_integrations.autonomous_agents.intent_merge import derive_capabilities

        caps = derive_capabilities(intent)
    schedule_ms = (intent.schedules or {}).get("watch_ms") if intent else None
    if schedule_ms is None and intent:
        for cond in intent.watch_conditions or []:
            if cond.kind == "schedule":
                every_min = cond.params.get("every_min")
                try:
                    schedule_ms = max(1, int(every_min)) * 60_000
                except (TypeError, ValueError):
                    pass
                break
    cadence = f"every {max(1, int(schedule_ms) // 60_000)} min" if schedule_ms else "default"

    return (
        "\n## [agent_intent] ARQ checklist (complete before ending the turn)\n"
        f"- engagement: **{engagement}** · instruments: **{instruments}** · symbols: **{symbols}**\n"
        f"- watch cadence: **{cadence}** · alert conditions: **{watch_text}**\n"
        f"- needs_clarification: **{needs}**\n"
        "- Did you call `propose_autonomous_agent` when the user gave enough to propose?\n"
        "- If status would be incomplete, ask **one** clarifying question — do not guess instruments.\n"
        "- Never invent proposal IDs in prose; only the tool creates valid cards.\n"
    )
