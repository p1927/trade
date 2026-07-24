"""Unified autonomous turn footer driven by persisted agent intent."""

from __future__ import annotations

from typing import Any

from trade_integrations.autonomous_agents.intent_proposal import watch_conditions_summary
from trade_integrations.autonomous_agents.intent_schema import AgentIntent


def format_agent_intent_turn_footer(agent: dict[str, Any]) -> str | None:
    """Single [agent_intent] block replacing duplicate observe/trade footers when intent exists."""
    mc = agent.get("mandate_config") if isinstance(agent.get("mandate_config"), dict) else {}
    raw_intent = mc.get("intent")
    if not isinstance(raw_intent, dict):
        return None
    intent = AgentIntent.from_dict(raw_intent)
    if not (intent.engagement or intent.instruments or intent.watch_conditions):
        return None

    observe = str(intent.engagement or "").lower() == "observe"
    from trade_integrations.autonomous_agents.intent_merge import derive_capabilities

    caps = derive_capabilities(intent)
    watch_lines = watch_conditions_summary(intent)
    watch_text = "; ".join(watch_lines) if watch_lines else "active rules from watch_spec"
    instruments = ", ".join(intent.instruments) if intent.instruments else "unspecified"

    decision_line = "## Decision: WATCH | SKIP (confidence N%)" if observe else (
        "## Decision: ENTER | HOLD | SKIP | EXIT | REVISE (confidence N% — below/above gate)"
    )
    record_line = (
        "- Call `record_autonomous_decision` with **WATCH or SKIP only** plus confidence and a short report."
        if observe
        else "- Call `record_autonomous_decision` with ENTER | REVISE | EXIT | HOLD | SKIP plus confidence, direction, and strategy when known."
    )
    widget_line = (
        "- Do **not** create trade-plan widgets or execute unless the user explicitly asks to trade."
        if observe or not caps.get("execution", True)
        else "- Use trade-plan widgets only when capabilities allow execution and confidence meets the gate."
    )

    return f"""
## [agent_intent]
- engagement: **{intent.engagement or 'trade'}** · instruments: **{instruments}**
- capabilities: widgets={caps.get('widgets', False)} payoff={caps.get('payoff', False)} execution={caps.get('execution', False)}
- watch focus: **{watch_text}**

## Output format (mandatory — trader-facing)
Respond with this structure only (no audit IDs, no implementation notes):

{decision_line}
**View:** direction · spot · VIX/regime (cite live tool or hub research)
{"**Report:** concise market summary for the user" if observe else "**Strategy considered:** name — chosen or deferred because [reason]"}
**Watch:** {watch_text} — material alerts since last turn or "none"
{"**Next action:** what would trigger a material report" if observe else "**Next action:** what would trigger ENTER or REVISE"}

## Output rules (mandatory)
- Decide autonomously — **do not ask the user questions** on scheduled turns.
{record_line}
{widget_line}
- Use hub research and live tools; cite prediction range when relevant.
- **Never** mention: handoff cycle, cached context, synthetic alert, audit pa_, verification reads.
"""
