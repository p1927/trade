"""Merge agent intent deltas — latest explicit message wins."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from trade_integrations.autonomous_agents.intent_schema import (
    AgentIntent,
    InstrumentClass,
    IntentDelta,
    WatchCondition,
    default_agent_intent,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def derive_capabilities(intent: AgentIntent) -> dict[str, bool]:
    """Code-owned capability flags — not LLM prose."""
    engagement = str(intent.engagement or "trade").lower()
    instruments = {str(x).lower() for x in (intent.instruments or []) if str(x).strip()}

    if engagement == "observe":
        return {
            "widgets": False,
            "payoff": False,
            "charges": False,
            "execution": False,
            "index_outlook": bool(instruments & {"index"} or not instruments),
        }

    has_options = "options" in instruments
    has_futures = "futures" in instruments
    has_equity = "equity" in instruments or has_futures
    has_index = "index" in instruments

    if has_options:
        return {
            "widgets": True,
            "payoff": True,
            "charges": True,
            "execution": True,
            "index_outlook": False,
        }

    if has_equity:
        return {
            "widgets": True,
            "payoff": False,
            "charges": True,
            "execution": True,
            "index_outlook": False,
        }

    if has_index:
        return {
            "widgets": True,
            "payoff": False,
            "charges": False,
            "execution": False,
            "index_outlook": True,
        }

    return {
        "widgets": True,
        "payoff": False,
        "charges": True,
        "execution": True,
        "index_outlook": False,
    }


def merge_agent_intent(prior: AgentIntent | None, delta: IntentDelta) -> AgentIntent:
    """Apply delta; only fields listed in explicit_fields overwrite prior."""
    base = deepcopy(prior) if prior else default_agent_intent()
    explicit = {str(f).strip() for f in (delta.explicit_fields or []) if str(f).strip()}

    if "engagement" in explicit and delta.engagement is not None:
        base.engagement = delta.engagement
        base.clarified["engagement"] = True

    if "instruments" in explicit and delta.instruments is not None:
        base.instruments = list(delta.instruments)
        base.clarified["instruments"] = True

    if "symbols" in explicit and delta.symbols is not None:
        base.symbols = [str(s).strip().upper() for s in delta.symbols if str(s).strip()]
        base.clarified["symbols"] = True

    if "schedules" in explicit and delta.schedules is not None:
        merged_sched = dict(base.schedules)
        merged_sched.update({k: int(v) for k, v in delta.schedules.items()})
        base.schedules = merged_sched
        base.clarified["schedules"] = True

    if "watch_conditions" in explicit and delta.watch_conditions is not None:
        base.watch_conditions = list(delta.watch_conditions)
        base.clarified["watch_conditions"] = True

    if "confidence_threshold" in explicit and delta.confidence_threshold is not None:
        base.confidence_threshold = max(0, min(100, int(delta.confidence_threshold)))
        base.clarified["confidence_threshold"] = True

    if "constraints" in explicit and delta.constraints is not None:
        merged_constraints = dict(base.constraints)
        merged_constraints.update(delta.constraints)
        base.constraints = merged_constraints
        base.clarified["constraints"] = True

    if delta.needs_clarification:
        base.needs_clarification = list(delta.needs_clarification)
    elif "needs_clarification" not in explicit:
        base.needs_clarification = []

    if delta.source_message_id:
        base.source_message_id = delta.source_message_id
    base.updated_at = _now_iso()
    base.capabilities = derive_capabilities(base)
    return base


def instruments_to_allowed_instruments(instruments: list[InstrumentClass]) -> list[str] | None:
    """Map unified instrument classes to mandate allowed_instruments for propose."""
    if not instruments:
        return None
    mapped: list[str] = []
    for inst in instruments:
        key = str(inst).strip().lower()
        if key == "index":
            if "equity" not in mapped:
                mapped.append("equity")
        elif key in {"equity", "options", "futures"}:
            if key not in mapped:
                mapped.append(key)
    return mapped or None


def intent_to_propose_kwargs(intent: AgentIntent) -> dict[str, Any]:
    """Map merged intent to propose_autonomous_agent kwargs."""
    kwargs: dict[str, Any] = {}
    if intent.symbols:
        kwargs["symbols"] = list(intent.symbols)
    if intent.engagement == "observe":
        kwargs["agent_mode"] = "observe"
    constraints = dict(intent.constraints or {})
    if constraints.get("budget_inr") is not None:
        kwargs["budget_inr"] = constraints["budget_inr"]
    if constraints.get("max_daily_loss_inr") is not None:
        kwargs["max_daily_loss_inr"] = constraints["max_daily_loss_inr"]
    if intent.confidence_threshold:
        kwargs["confidence_threshold"] = intent.confidence_threshold
    watch_ms = intent.schedules.get("watch_ms")
    if watch_ms:
        kwargs["watch_interval_min"] = max(1, int(watch_ms) // 60_000)
    research_ms = intent.schedules.get("research_ms")
    if research_ms:
        kwargs["research_interval_min"] = max(1, int(research_ms) // 60_000)
    allowed = instruments_to_allowed_instruments(intent.instruments)
    if allowed:
        kwargs["allowed_instruments"] = allowed
    kwargs["intent"] = intent.to_dict()
    return kwargs


def watch_conditions_from_dicts(rows: list[dict[str, Any]] | None) -> list[WatchCondition]:
    out: list[WatchCondition] = []
    for row in rows or []:
        parsed = WatchCondition.from_dict(row)
        if parsed:
            out.append(parsed)
    return out
