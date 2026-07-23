"""Agent-native decision recording (replaces autonomous_agents session decisions)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from trade_integrations.autonomous_agents.audit import write_agent_audit
from trade_integrations.autonomous_agents.lifecycle import apply_lifecycle_decision, load_agent_lifecycle
from trade_integrations.autonomous_agents.mandate import mandate_config_from_agent, validate_decision
from trade_integrations.autonomous_agents.outcome_ledger import append_outcome


def _audit_payload(record: dict[str, Any]) -> dict[str, Any]:
    return {"agent_audit": record, "audit_id": record.get("audit_id")}


def _pseudo_session(agent: dict[str, Any], *, ticker: str | None = None) -> dict[str, Any]:
    symbols = list(agent.get("symbols") or ["NIFTY"])
    focus = (ticker or symbols[0] or "NIFTY").strip().upper()
    mc = mandate_config_from_agent(agent)
    return {
        "mandate_config": mc.to_dict(),
        "primary_ticker": focus,
        "watchlist": symbols,
        "user_guidance": list(agent.get("user_guidance") or []),
    }


def record_agent_decision(
    agent: dict[str, Any],
    *,
    decision: str,
    rationale: str,
    ticker: str | None = None,
    actions_taken: list[str] | None = None,
    confidence: int | None = None,
    direction: str | None = None,
    strategy: str | None = None,
    append_outcome: bool = True,
) -> dict[str, Any]:
    """Validate, apply lifecycle, audit, and persist decision on agent JSON."""
    raw_decision = str(decision).strip().upper()
    session = _pseudo_session(agent, ticker=ticker)
    mc = mandate_config_from_agent(agent)
    validated, warnings = validate_decision(raw_decision, session, mandate=mc)
    entry: dict[str, Any] = {
        "at": datetime.now(timezone.utc).isoformat(),
        "decision": validated,
        "original_decision": raw_decision if validated != raw_decision else None,
        "mandate_warnings": warnings or None,
        "rationale": rationale.strip(),
        "ticker": (ticker or session.get("primary_ticker") or "").strip().upper() or None,
        "actions_taken": actions_taken or [],
    }
    if confidence is not None:
        try:
            entry["confidence"] = max(0, min(100, int(confidence)))
        except (TypeError, ValueError):
            pass
    if direction:
        entry["direction"] = direction.strip()
    if strategy:
        entry["strategy"] = strategy.strip()

    agent = dict(agent)
    decisions = list(agent.get("decisions") or [])
    decisions.append(entry)
    agent["decisions"] = decisions[-100:]
    agent["last_agent_turn_at"] = entry["at"]
    agent["last_decision"] = entry

    agent = apply_lifecycle_decision(
        agent,
        decision=validated,
        rationale=entry["rationale"],
        ticker=entry.get("ticker"),
    )

    if entry["decision"] == "EXIT" and append_outcome:
        lifecycle = load_agent_lifecycle(agent)
        exit_strategy = strategy or entry.get("strategy")
        if not exit_strategy:
            tried = list(lifecycle.get("tried_strategies") or [])
            exit_strategy = tried[-1] if tried else lifecycle.get("active_strategy")
        append_outcome(
            symbol=str(entry.get("ticker") or session.get("primary_ticker") or "NIFTY"),
            strategy=exit_strategy,
            action="EXIT",
            intent_source="vibe_decision",
            agent_id=str(agent.get("id") or ""),
            mandate_snapshot=mc.to_dict(),
        )

    audit = write_agent_audit("decision_recorded", detail=entry)
    return {"status": "recorded", "decision": entry, "audit": _audit_payload(audit)}
