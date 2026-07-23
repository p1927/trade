"""Bridge learning into autonomous agents — prefer agent JSON lifecycle over session store."""

from __future__ import annotations

from typing import Any


def _load_lifecycle_for_agent(agent: dict[str, Any], *, session: dict[str, Any] | None = None) -> dict[str, Any]:
    from trade_integrations.autonomous_agents.lifecycle import default_lifecycle, load_lifecycle

    lifecycle_raw = agent.get("lifecycle")
    if isinstance(lifecycle_raw, dict) and str(lifecycle_raw.get("state") or "").strip():
        return load_lifecycle({"lifecycle": lifecycle_raw})
    if session is None:
        agent_id = str(agent.get("id") or "").strip()
        if agent_id:
            try:
                from trade_integrations.auto_paper.session_store import load_session

                session = load_session(autonomous_agent_id=agent_id)
            except Exception:
                session = {}
    if session:
        return load_lifecycle(session)
    return default_lifecycle()


def _resolve_exit_strategy(
    *,
    decision_entry: dict[str, Any],
    lifecycle: dict[str, Any],
    agent: dict[str, Any],
) -> str | None:
    """Best-effort strategy name after EXIT clears active_strategy."""
    from_decision = decision_entry.get("strategy")
    if from_decision:
        return str(from_decision).strip() or None
    tried = list(lifecycle.get("tried_strategies") or [])
    if tried:
        return str(tried[-1])
    thesis = agent.get("thesis") or {}
    from_thesis = thesis.get("strategy")
    return str(from_thesis).strip() if from_thesis else None


def sync_agent_thesis_from_lifecycle(
    agent: dict[str, Any],
    *,
    session: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Merge paper-session lifecycle fields into agent thesis for scorer + prompts."""
    agent_id = str(agent.get("id") or "").strip()
    if not agent_id:
        return agent
    if session is None:
        from trade_integrations.auto_paper.session_store import load_session

        session = load_session(autonomous_agent_id=agent_id)

    from trade_integrations.autonomous_agents.lifecycle import format_lifecycle_for_prompt

    lifecycle = _load_lifecycle_for_agent(agent, session=session)
    thesis = dict(agent.get("thesis") or {})

    tried = list(lifecycle.get("tried_strategies") or [])
    if tried:
        thesis["tried_strategies"] = tried

    failures = list(lifecycle.get("failure_reasons") or [])[-5:]
    if failures:
        thesis["recent_failures"] = failures

    plan_b = lifecycle.get("plan_b_candidates") or []
    if plan_b:
        thesis["plan_b_candidates"] = plan_b

    agent["thesis"] = thesis
    return agent


def append_agent_learning(
    agent: dict[str, Any],
    *,
    event: str,
    strategy: str | None,
    rationale: str,
    symbol: str | None = None,
    pnl_inr: float | None = None,
) -> dict[str, Any]:
    from datetime import datetime, timezone

    entry = {
        "at": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "strategy": strategy,
        "symbol": symbol,
        "rationale": rationale[:500],
        "pnl_inr": pnl_inr,
    }
    learnings = list(agent.get("learnings") or [])
    learnings.append(entry)
    agent["learnings"] = learnings[-30:]
    return agent


def read_learning_snapshot(*, agent: dict[str, Any]) -> dict[str, Any]:
    """Read-only learning context for prompts — no session or agent persistence."""
    import json

    agent_id = str(agent.get("id") or "").strip()
    empty: dict[str, Any] = {
        "prompt_text": "",
        "tried_strategies": list((agent.get("thesis") or {}).get("tried_strategies") or []),
        "thesis_overlay": {},
    }
    if not agent_id:
        return empty

    from trade_integrations.autonomous_agents.lifecycle import format_lifecycle_for_prompt
    from trade_integrations.auto_paper.reflection import format_reflections_for_prompt
    from trade_integrations.auto_paper.session_store import load_session

    parts: list[str] = []
    tried = list(empty["tried_strategies"])
    overlay: dict[str, Any] = {}

    lifecycle = _load_lifecycle_for_agent(agent)
    parts.append(format_lifecycle_for_prompt(lifecycle))
    tried = list(lifecycle.get("tried_strategies") or tried)
    failures = list(lifecycle.get("failure_reasons") or [])[-5:]
    plan_b = lifecycle.get("plan_b_candidates") or []
    if tried:
        overlay["tried_strategies"] = tried
    if failures:
        overlay["recent_failures"] = failures
    if plan_b:
        overlay["plan_b_candidates"] = plan_b

    reflection_block = format_reflections_for_prompt(limit=2, agent_id=agent_id)
    if reflection_block:
        parts.append(reflection_block)

    try:
        session = load_session(autonomous_agent_id=agent_id)
    except Exception:
        session = {}
    decisions = list((session or {}).get("decisions") or [])[-5:]
    if decisions:
        parts.append(
            "## Recent session decisions\n```json\n"
            + json.dumps(decisions, indent=2, default=str)
            + "\n```\n"
        )

    learnings = agent.get("learnings") or []
    if learnings:
        parts.append(
            "## Agent trade learnings (this instance)\n```json\n"
            + json.dumps(list(learnings)[-5:], indent=2, default=str)
            + "\n```\n"
            + "Do not re-enter strategies listed in tried_strategies or failed EXIT learnings "
            "without new hub evidence or explicit user guidance.\n"
        )

    return {
        "prompt_text": "\n".join(p for p in parts if p.strip()),
        "tried_strategies": tried,
        "thesis_overlay": overlay,
    }


def format_learning_context_for_prompt(*, agent: dict[str, Any]) -> str:
    """Lifecycle, reflections, recent decisions, and per-agent learnings (read-only)."""
    return read_learning_snapshot(agent=agent)["prompt_text"]


def record_reflection_on_exit(
    *,
    agent: dict[str, Any],
    decision_entry: dict[str, Any],
    session: dict[str, Any] | None = None,
) -> None:
    """Persist markdown reflection + structured learning row after EXIT."""
    agent_id = str(agent.get("id") or "").strip()
    if not agent_id:
        return
    if session is None:
        from trade_integrations.auto_paper.session_store import load_session

        session = load_session(autonomous_agent_id=agent_id)

    from trade_integrations.auto_paper.market_feedback import _session_pnl_block
    from trade_integrations.auto_paper.reflection import save_reflection

    lifecycle = _load_lifecycle_for_agent(agent, session=session)
    strategy = _resolve_exit_strategy(
        decision_entry=decision_entry,
        lifecycle=lifecycle,
        agent=agent,
    )
    symbol = decision_entry.get("ticker") or (agent.get("symbols") or ["NIFTY"])[0]
    rationale = str(decision_entry.get("rationale") or "")

    pnl: float | None = None
    raw_pnl = decision_entry.get("pnl_inr")
    if raw_pnl is not None:
        try:
            pnl = float(raw_pnl)
        except (TypeError, ValueError):
            pnl = None
    if pnl is None:
        try:
            pnl_block = _session_pnl_block(session, focus_ticker=str(symbol))
            raw = pnl_block.get("day_pnl_inr")
            if raw is not None:
                pnl = float(raw)
        except Exception:
            pass

    summary = (
        f"Agent {agent_id} EXIT on {symbol}"
        + (f" ({strategy})" if strategy else "")
        + f": {rationale[:300]}"
    )
    save_reflection(
        agent_id=agent_id,
        summary=summary,
        decisions=list(session.get("decisions") or [])[-10:],
        pnl_inr=pnl,
    )
    append_agent_learning(
        agent,
        event="EXIT",
        strategy=strategy,
        rationale=rationale,
        symbol=str(symbol),
        pnl_inr=pnl,
    )


def _append_us_exit_learning(agent: dict[str, Any], *, decision_entry: dict[str, Any]) -> None:
    """Track tried strategies on agent thesis for US agents (no auto_paper lifecycle)."""
    strategy = decision_entry.get("strategy") or (agent.get("thesis") or {}).get("strategy")
    symbol = decision_entry.get("ticker") or (agent.get("symbols") or ["SPY"])[0]
    rationale = str(decision_entry.get("rationale") or "")

    thesis = dict(agent.get("thesis") or {})
    tried = list(thesis.get("tried_strategies") or [])
    if strategy and str(strategy) not in tried:
        tried.append(str(strategy))
        thesis["tried_strategies"] = tried
    agent["thesis"] = thesis

    append_agent_learning(
        agent,
        event="EXIT",
        strategy=str(strategy) if strategy else None,
        rationale=rationale,
        symbol=str(symbol),
    )


def apply_exit_learning(
    agent: dict[str, Any],
    *,
    decision_entry: dict[str, Any],
) -> dict[str, Any]:
    """Sync thesis + persist reflection after an EXIT decision."""
    from trade_integrations.execution.profile import resolve_profile

    profile = resolve_profile(agent=agent)
    if profile.uses_openalgo_auto_paper:
        from trade_integrations.auto_paper.session_store import load_session

        session = load_session(autonomous_agent_id=str(agent.get("id") or ""))
        sync_agent_thesis_from_lifecycle(agent, session=session)
        record_reflection_on_exit(agent=agent, decision_entry=decision_entry, session=session)
    else:
        _append_us_exit_learning(agent, decision_entry=decision_entry)
    return agent
