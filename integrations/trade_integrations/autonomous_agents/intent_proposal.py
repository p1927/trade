"""Proposal card helpers — clarifying prompts and watch condition labels from intent."""

from __future__ import annotations

from typing import Any

from trade_integrations.autonomous_agents.intent_schema import AgentIntent, WatchCondition

_MISSING_FIELD_PROMPTS: dict[str, str] = {
    "instruments": "Which instrument should this agent use — index watch, equity, options, or futures?",
    "allowed_instruments": "Which instrument should this agent use — index watch, equity, options, or futures?",
    "symbols": "Which symbol or index should the agent focus on?",
    "mandate": "What is the trading or watch goal (intraday, swing, observe-only, etc.)?",
    "budget_inr": "What paper budget should we use?",
    "max_daily_loss_inr": "What is the maximum daily loss limit?",
}


def intent_from_proposal(proposal: dict[str, Any]) -> AgentIntent | None:
    mc = proposal.get("mandate_config") if isinstance(proposal.get("mandate_config"), dict) else {}
    raw = mc.get("intent") if isinstance(mc.get("intent"), dict) else proposal.get("intent")
    if not isinstance(raw, dict):
        return None
    return AgentIntent.from_dict(raw)


def format_instrument_labels(intent: AgentIntent | None, *, fallback_allowed: list[str] | None = None) -> str:
    if intent and intent.instruments:
        labels = {
            "equity": "Equity",
            "options": "Options",
            "futures": "Futures",
            "index": "Index watch",
        }
        return " · ".join(labels.get(str(x).lower(), str(x).title()) for x in intent.instruments)
    if fallback_allowed:
        return " · ".join(str(x).title() for x in fallback_allowed)
    return "—"


def format_watch_condition_label(cond: WatchCondition | dict[str, Any]) -> str:
    if isinstance(cond, dict):
        parsed = WatchCondition.from_dict(cond)
        if not parsed:
            label = str(cond.get("label") or "").strip()
            return label or str(cond.get("kind") or "condition")
        cond = parsed
    if cond.label:
        return str(cond.label)
    params = dict(cond.params or {})
    sym = str(cond.symbol or "NIFTY").upper()
    if cond.kind == "schedule":
        every_min = params.get("every_min")
        return f"Poll every {every_min} min" if every_min else "Scheduled poll"
    if cond.kind == "price_level":
        parts: list[str] = []
        if params.get("above") is not None:
            parts.append(f"above {params['above']}")
        if params.get("below") is not None:
            parts.append(f"below {params['below']}")
        return f"{sym} {' & '.join(parts)}" if parts else f"{sym} price level"
    if cond.kind == "price_move":
        if params.get("points") is not None:
            return f"{sym} moves {params['points']} points"
        if params.get("pct") is not None:
            return f"{sym} moves {params['pct']}%"
        return f"{sym} price move"
    if cond.kind == "vix":
        if params.get("above") is not None:
            return f"VIX above {params['above']}"
        if params.get("below") is not None:
            return f"VIX below {params['below']}"
        return "VIX level"
    if cond.kind == "volume":
        return f"{sym} volume spike"
    if cond.kind == "oi":
        return f"{sym} OI change"
    return str(cond.kind or "condition")


def watch_conditions_summary(intent: AgentIntent | None) -> list[str]:
    if not intent or not intent.watch_conditions:
        return []
    out: list[str] = []
    for cond in intent.watch_conditions:
        if cond.kind == "schedule":
            continue
        label = format_watch_condition_label(cond)
        if label and label not in out:
            out.append(label)
    return out


def clarifying_prompt_for_missing(
    missing_fields: list[str],
    *,
    intent: AgentIntent | None = None,
) -> str:
    prompts: list[str] = []
    for field in missing_fields:
        key = str(field).strip()
        if not key:
            continue
        text = _MISSING_FIELD_PROMPTS.get(key)
        if text and text not in prompts:
            prompts.append(text)
    if not prompts and intent and intent.needs_clarification:
        for key in intent.needs_clarification:
            text = _MISSING_FIELD_PROMPTS.get(str(key).strip())
            if text and text not in prompts:
                prompts.append(text)
    if not prompts:
        return "Please clarify the missing fields above before confirming."
    if len(prompts) == 1:
        return prompts[0]
    return " ".join(f"{idx + 1}. {line}" for idx, line in enumerate(prompts))
