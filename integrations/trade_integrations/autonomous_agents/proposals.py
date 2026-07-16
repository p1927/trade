"""Propose / commit consent flow for autonomous agents."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from trade_integrations.autonomous_agents.defaults import (
    DEFAULT_BUDGET_INR,
    DEFAULT_CONFIDENCE_THRESHOLD,
    DEFAULT_MAX_DAILY_LOSS_INR,
    DEFAULT_MODE,
    DEFAULT_RESEARCH_INTERVAL_MIN,
    DEFAULT_WATCH_INTERVAL_MIN,
    MAX_CONCURRENT_AGENTS,
    PROPOSAL_TTL_MS,
    REQUIRED_PROPOSAL_FIELDS,
)
from trade_integrations.autonomous_agents.market import symbol_execution_market
from trade_integrations.autonomous_agents.market_resolve import resolve_proposal_symbols
from trade_integrations.auto_paper.mandate_config import (
    MandateConfig,
    resolve_mandate_config,
)
from trade_integrations.autonomous_agents.runtime_status import build_stack_health
from trade_integrations.autonomous_agents.store import (
    delete_proposal,
    get_agent,
    list_agents,
    load_proposal,
    new_agent_id,
    new_proposal_id,
    save_agent,
    save_proposal,
)
from trade_integrations.execution.profile import resolve_profile


def _normalize_symbols(raw: Any) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, str):
        return [s.strip().upper() for s in raw.split(",") if s.strip()]
    if isinstance(raw, list):
        return [str(s).strip().upper() for s in raw if str(s).strip()]
    return []


def _missing_fields(draft: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    symbols = _normalize_symbols(draft.get("symbols"))
    if not symbols:
        missing.append("symbols")
    return missing


def _apply_defaults(kwargs: dict[str, Any]) -> dict[str, Any]:
    symbols = _normalize_symbols(kwargs.get("symbols"))
    watch_min = kwargs.get("watch_interval_min")
    research_min = kwargs.get("research_interval_min")
    try:
        watch_min = int(watch_min) if watch_min is not None else DEFAULT_WATCH_INTERVAL_MIN
    except (TypeError, ValueError):
        watch_min = DEFAULT_WATCH_INTERVAL_MIN
    try:
        research_min = int(research_min) if research_min is not None else DEFAULT_RESEARCH_INTERVAL_MIN
    except (TypeError, ValueError):
        research_min = DEFAULT_RESEARCH_INTERVAL_MIN

    name = str(kwargs.get("name") or "").strip()
    if not name and symbols:
        name = f"{symbols[0]} autonomous"

    return {
        "symbols": symbols,
        "name": name or "Autonomous agent",
        "mandate": str(kwargs.get("mandate") or "").strip()
        or f"Paper trade {symbols[0] if symbols else 'NIFTY'} autonomously; research, watch, act when confident.",
        "budget_inr": float(kwargs.get("budget_inr") or DEFAULT_BUDGET_INR),
        "max_daily_loss_inr": float(kwargs.get("max_daily_loss_inr") or DEFAULT_MAX_DAILY_LOSS_INR),
        "confidence_threshold": int(kwargs.get("confidence_threshold") or DEFAULT_CONFIDENCE_THRESHOLD),
        "watch_interval_min": max(1, watch_min),
        "research_interval_min": max(5, research_min),
        "mode": str(kwargs.get("mode") or DEFAULT_MODE),
        "vibe_session_id": kwargs.get("vibe_session_id"),
        "orchestrator_session_id": kwargs.get("orchestrator_session_id"),
        "alert_spot_move_pct": float(kwargs.get("alert_spot_move_pct") or 0.5),
    }


def _build_mandate_config(
    draft: dict[str, Any],
    *,
    mandate_text: str | None = None,
    execution_market: str | None = None,
) -> MandateConfig:
    sym_list = list(draft.get("symbols") or ["NIFTY"])
    primary = sym_list[0] if sym_list else "NIFTY"
    market = execution_market or symbol_execution_market(primary)
    return resolve_mandate_config(
        symbols=sym_list,
        mandate_text=mandate_text or str(draft.get("mandate") or ""),
        stored=draft.get("mandate_config") if isinstance(draft.get("mandate_config"), dict) else None,
        budget_inr=float(draft.get("budget_inr") or DEFAULT_BUDGET_INR),
        max_daily_loss_inr=float(draft.get("max_daily_loss_inr") or DEFAULT_MAX_DAILY_LOSS_INR),
        confidence_threshold=int(draft.get("confidence_threshold") or DEFAULT_CONFIDENCE_THRESHOLD),
        alert_spot_move_pct=float(draft.get("alert_spot_move_pct") or 0.5),
        execution_market=market,
    )


def _user_text_for_routing(kwargs: dict[str, Any], draft: dict[str, Any]) -> str:
    parts = [
        str(kwargs.get("user_text") or ""),
        str(kwargs.get("mandate") or ""),
        str(draft.get("mandate") or ""),
    ]
    return "\n".join(p for p in parts if p.strip())


def validate_proposal_routing(proposal: dict[str, Any]) -> list[str]:
    """Return blocking errors when execution market/backend disagree with symbols."""
    errors: list[str] = []
    market = str(proposal.get("execution_market") or "").upper()
    backend = str(proposal.get("execution_backend") or "").lower()
    symbols = list(proposal.get("symbols") or [])
    user_text = str(proposal.get("mandate") or "")

    if market == "IN" and backend == "alpaca":
        errors.append("India execution_market cannot use Alpaca backend.")
    if market == "US" and backend == "openalgo":
        errors.append("US execution_market cannot use OpenAlgo backend.")

    for sym in symbols:
        expected = symbol_execution_market(str(sym), user_text=user_text)
        if expected == "IN" and market == "US":
            errors.append(f"Symbol {sym} is India-listed but execution_market is US.")
        if expected == "US" and market == "IN":
            errors.append(f"Symbol {sym} is US-listed but execution_market is IN.")

    watch_spec = dict(proposal.get("watch_spec") or {})
    for row in watch_spec.get("rules") or []:
        if not isinstance(row, dict):
            continue
        sym = str(row.get("symbol") or "").upper()
        exchange = str(row.get("exchange") or "").upper()
        if not sym:
            continue
        if symbol_execution_market(sym, user_text=user_text) == "IN" and exchange == "US":
            errors.append(f"Watch rule for {sym} uses US exchange but symbol is India-listed.")

    routing_warnings = proposal.get("routing_warnings") or []
    for msg in routing_warnings:
        text = str(msg)
        if "invalid" in text.lower():
            errors.append(text)

    return errors


def propose_autonomous_agent(**kwargs: Any) -> dict[str, Any]:
    draft = _apply_defaults(kwargs)
    user_text = _user_text_for_routing(kwargs, draft)
    symbols, resolution, routing_warnings = resolve_proposal_symbols(
        list(draft.get("symbols") or []),
        user_text=user_text,
        market_hint=kwargs.get("execution_market"),
    )
    draft["symbols"] = symbols
    missing = _missing_fields(draft)
    proposal_id = str(kwargs.get("proposal_id") or new_proposal_id())

    primary_symbol = draft["symbols"][0] if draft["symbols"] else "NIFTY"
    exec_market = resolution.market
    mandate_cfg = _build_mandate_config(
        draft,
        mandate_text=str(kwargs.get("mandate") or draft.get("mandate") or ""),
        execution_market=exec_market,
    )
    profile = resolve_profile(
        agent={
            "symbols": draft["symbols"],
            "execution_market": exec_market,
            "constraints": {
                "mode": draft["mode"],
                "budget_inr": draft["budget_inr"],
                "max_daily_loss_inr": draft["max_daily_loss_inr"],
            },
            "mandate_config": mandate_cfg.to_dict(),
            "mandate": draft["mandate"],
        },
    )

    proposal: dict[str, Any] = {
        "type": "autonomous_agent.proposal",
        "proposal_id": proposal_id,
        "status": "ready" if not missing else "incomplete",
        "missing_fields": missing,
        "symbols": draft["symbols"],
        "execution_market": exec_market,
        "execution_backend": profile.backend,
        "stack_health": build_stack_health(),
        "name": draft["name"],
        "mandate": draft["mandate"],
        "constraints": {
            "mode": draft["mode"],
            "budget_inr": draft["budget_inr"],
            "max_daily_loss_inr": draft["max_daily_loss_inr"],
            "confidence_threshold": mandate_cfg.confidence_threshold,
            "market_hours_only": mandate_cfg.market_hours_only,
            "max_open_positions": mandate_cfg.max_open_positions,
        },
        "mandate_config": mandate_cfg.to_dict(),
        "watch_spec": mandate_cfg.watch_spec,
        "schedules": {
            "watch_ms": draft["watch_interval_min"] * 60_000,
            "research_ms": draft["research_interval_min"] * 60_000,
        },
        "alert_rules": mandate_cfg.alert_rules.to_dict(),
        "vibe_session_id": draft.get("vibe_session_id"),
        "orchestrator_session_id": draft.get("orchestrator_session_id"),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "expires_at_ms": int(time.time() * 1000) + PROPOSAL_TTL_MS,
        "routing_warnings": list(routing_warnings),
    }

    routing_errors = validate_proposal_routing(proposal)
    proposal["routing_errors"] = routing_errors
    if routing_errors:
        proposal["status"] = "incomplete"

    save_proposal(proposal)

    if missing:
        return {
            "status": "incomplete",
            "proposal_id": proposal_id,
            "missing_fields": missing,
            "proposal": proposal,
            "message": f"Ask user for: {', '.join(missing)}",
        }

    if routing_errors:
        return {
            "status": "incomplete",
            "proposal_id": proposal_id,
            "missing_fields": [],
            "routing_errors": routing_errors,
            "proposal": proposal,
            "message": "Proposal has routing errors — fix market/symbol mismatch before confirm.",
        }

    return {
        "status": "ready",
        "proposal_id": proposal_id,
        "missing_fields": [],
        "proposal": proposal,
        "message": "Proposal ready — user must confirm in the UI.",
    }


def _build_agent_system_note(
    *,
    agent_id: str,
    symbols: list[str],
    profile: Any,
    proposal: dict[str, Any],
    prefetch_note: str,
) -> str:
    mc = dict(proposal.get("mandate_config") or {})
    instruments = ", ".join(mc.get("allowed_instruments") or list(profile.allowed_instruments))
    constraints = dict(proposal.get("constraints") or {})
    sym_line = ", ".join(symbols)
    base = (
        f"You are autonomous agent {agent_id} for {profile.market} ({sym_line}). "
        f"Confirmed mandate (user tapped Confirm on proposal {proposal.get('proposal_id')}): "
        f"instruments={instruments}, "
        f"holding={mc.get('holding_period')}, flatten={mc.get('flatten_policy')}, "
        f"product={mc.get('product_type')}, mode={constraints.get('mode') or profile.mode}. "
        "Trust get_autonomous_agent_status for this agent_id on each turn. "
        "Do not apply rules from other agents or pre-commit orchestrator chat about other symbols. "
        f"{prefetch_note}"
    )
    if profile.is_us:
        return base + " Execution via Alpaca paper tools."
    return base + " Execution via OpenAlgo/Nautilus bridge."


def commit_autonomous_agent(
    *,
    proposal_id: str,
    consent_ack: bool,
    session_service: Any,
    orchestrator_session_id: str | None = None,
) -> dict[str, Any]:
    if not consent_ack:
        raise ValueError("consent_ack is required")

    proposal = load_proposal(proposal_id)
    if proposal is None:
        raise ValueError(f"proposal not found: {proposal_id}")

    if proposal.get("committed_agent_id"):
        existing = get_agent(str(proposal["committed_agent_id"]))
        if existing:
            return {
                "status": "ok",
                "agent": existing,
                "vibe_session_id": existing.get("vibe_session_id"),
                "already_committed": True,
            }
        raise ValueError("proposal already committed")

    expires_at = int(proposal.get("expires_at_ms") or 0)
    if expires_at and int(time.time() * 1000) > expires_at:
        raise ValueError("proposal expired")

    running = [a for a in list_agents() if str(a.get("status")) in {"running", "paused"}]
    if len(running) >= MAX_CONCURRENT_AGENTS:
        raise ValueError(f"max concurrent agents ({MAX_CONCURRENT_AGENTS}) reached")

    if session_service is None:
        raise ValueError("session runtime not enabled")

    agent_id = new_agent_id()
    symbols = list(proposal.get("symbols") or [])
    name = str(proposal.get("name") or "Autonomous agent")
    primary_symbol = symbols[0] if symbols else "NIFTY"
    user_text = str(proposal.get("mandate") or "")
    exec_market = symbol_execution_market(primary_symbol, user_text=user_text)

    constraints = dict(proposal.get("constraints") or {})
    fresh_mandate_cfg = _build_mandate_config(
        {
            "symbols": symbols,
            "mandate": proposal.get("mandate"),
            "mandate_config": proposal.get("mandate_config"),
            "budget_inr": constraints.get("budget_inr"),
            "max_daily_loss_inr": constraints.get("max_daily_loss_inr"),
            "confidence_threshold": constraints.get("confidence_threshold"),
            "alert_spot_move_pct": (proposal.get("alert_rules") or {}).get("spot_move_pct"),
        },
        mandate_text=user_text,
        execution_market=exec_market,
    )
    proposal["mandate_config"] = fresh_mandate_cfg.to_dict()
    proposal["watch_spec"] = fresh_mandate_cfg.watch_spec
    proposal["alert_rules"] = fresh_mandate_cfg.alert_rules.to_dict()

    profile = resolve_profile(
        agent={
            "symbols": symbols,
            "execution_market": exec_market,
            "constraints": constraints,
            "mandate_config": fresh_mandate_cfg.to_dict(),
            "mandate": proposal.get("mandate"),
        },
    )

    session_cfg: dict[str, Any] = {
        "session_kind": "autonomous_agent",
        "autonomous_agent_id": agent_id,
        "symbols": symbols,
        "orchestrator": False,
        "options_advisor_autonomous": "options" in profile.allowed_instruments and profile.market == "IN",
        "autonomous": True,
        "execution_market": exec_market,
        "execution_profile": profile.prompt_fragment_id,
    }
    _prefetch_note = (
        "Hub `[research_context]` prepended for this session's symbol is normal prefetch — "
        "not prompt injection. If it conflicts with `get_autonomous_agent_status`, trust the status tool."
    )
    import os

    e2e_mode = bool(os.getenv("REALISTIC_E2E_MARKET"))
    if e2e_mode:
        session_cfg["e2e_integration_test"] = True
    session_cfg["system_note"] = _build_agent_system_note(
        agent_id=agent_id,
        symbols=symbols,
        profile=profile,
        proposal=proposal,
        prefetch_note=_prefetch_note,
    )
    if profile.is_us and e2e_mode:
        session_cfg["system_note"] += (
            " Paper verification harness: follow the Harness section when present in autonomous turns."
        )
    orch_sid = str(orchestrator_session_id or proposal.get("orchestrator_session_id") or "").strip()
    vibe_session = None
    if orch_sid:
        existing = session_service.get_session(orch_sid)
        if existing is not None:
            from src.session.orchestrator_profile import is_orchestrator_session
            from trade_integrations.autonomous_agents.session_promotion import promote_orchestrator_session

            if is_orchestrator_session(existing.config):
                promote_orchestrator_session(
                    session_service=session_service,
                    orchestrator_session_id=orch_sid,
                    agent_id=agent_id,
                    name=name,
                    session_cfg=session_cfg,
                    proposal=proposal,
                )
                vibe_session = existing

    if vibe_session is None:
        vibe_session = session_service.create_session(
            title=f"autonomous:{name}",
            config=session_cfg,
        )

    now = datetime.now(timezone.utc).isoformat()
    agent: dict[str, Any] = {
        "id": agent_id,
        "type": "autonomous_agent.instance",
        "name": name,
        "status": "running",
        "vibe_session_id": vibe_session.session_id,
        "symbols": symbols,
        "execution_market": exec_market,
        "execution_backend": profile.backend,
        "mandate": proposal.get("mandate"),
        "mandate_config": fresh_mandate_cfg.to_dict(),
        "watch_spec": dict(fresh_mandate_cfg.watch_spec or proposal.get("watch_spec") or {}),
        "constraints": dict(proposal.get("constraints") or {}),
        "schedules": dict(proposal.get("schedules") or {}),
        "alert_rules": fresh_mandate_cfg.alert_rules.to_dict(),
        "thesis": {},
        "user_guidance": [],
        "last_watch_at": None,
        "last_full_reasoning_at": None,
        "last_revision_at": None,
        "streaming": False,
        "bootstrap_status": "pending",
        "proposal_id": proposal_id,
        "orchestrator_session_id": orchestrator_session_id or proposal.get("orchestrator_session_id"),
        "created_at": now,
    }
    save_agent(agent)

    from trade_integrations.autonomous_agents.store import clear_orchestrator_meta

    clear_orchestrator_meta()

    paper_session_warnings: list[str] = []
    if profile.uses_openalgo_auto_paper:
        try:
            from trade_integrations.auto_paper.mcp_actions import start_auto_paper

            constraints = dict(proposal.get("constraints") or {})
            start_auto_paper(
                ticker=primary_symbol,
                budget_inr=float(constraints.get("budget_inr") or DEFAULT_BUDGET_INR),
                watchlist=symbols,
                max_daily_loss_inr=float(constraints.get("max_daily_loss_inr") or DEFAULT_MAX_DAILY_LOSS_INR),
                mandate=str(proposal.get("mandate") or ""),
                vibe_session_id=vibe_session.session_id,
                mandate_config=fresh_mandate_cfg.to_dict(),
                autonomous_agent_id=agent_id,
                nautilus_bridge_mode=profile.uses_nautilus_handoff,
            )
        except Exception as exc:
            import logging

            logging.getLogger(__name__).warning(
                "start_auto_paper on autonomous commit failed",
                exc_info=True,
            )
            paper_session_warnings.append(f"start_auto_paper failed: {exc}")
    elif profile.is_us:
        paper_session_warnings.append(
            "US agent — OpenAlgo INR auto-paper session not started; use Alpaca paper tools."
        )

    try:
        from nautilus_openalgo_bridge.handoff import sync_watch_spec_to_handoff

        watch_spec = dict(proposal.get("watch_spec") or {})
        mc = dict(proposal.get("mandate_config") or {})
        if not watch_spec.get("rules") and mc.get("watch_spec"):
            watch_spec = dict(mc["watch_spec"])
        if watch_spec.get("rules") and profile.uses_nautilus_handoff:
            sync_watch_spec_to_handoff(agent_id, watch_spec)
    except Exception:
        import logging

        logging.getLogger(__name__).debug("initial handoff on commit skipped", exc_info=True)

    proposal["committed_agent_id"] = agent_id
    proposal["committed_at"] = now
    save_proposal(proposal)

    result: dict[str, Any] = {
        "status": "ok",
        "agent": agent,
        "vibe_session_id": vibe_session.session_id,
    }
    if paper_session_warnings:
        result["paper_session_warnings"] = paper_session_warnings
    return result


def stop_autonomous_agent(agent_id: str) -> dict[str, Any]:
    agent = get_agent(agent_id)
    if not agent:
        raise ValueError(f"agent not found: {agent_id}")
    agent["status"] = "stopped"
    agent["stopped_at"] = datetime.now(timezone.utc).isoformat()
    save_agent(agent)
    try:
        from nautilus_openalgo_bridge.handoff import clear_handoff

        clear_handoff(agent_id)
    except Exception:
        pass
    try:
        from trade_integrations.auto_paper.session_store import load_session
        from trade_integrations.auto_paper.mcp_actions import stop_auto_paper

        session = load_session(autonomous_agent_id=agent_id)
        if session.get("enabled") and str(session.get("autonomous_agent_id") or "") == agent_id:
            stop_auto_paper(unregister_scheduler=True)
    except Exception:
        pass
    return {"status": "ok", "agent": agent}


def pause_autonomous_agent(agent_id: str) -> dict[str, Any]:
    agent = get_agent(agent_id)
    if not agent:
        raise ValueError(f"agent not found: {agent_id}")
    agent["status"] = "paused"
    save_agent(agent)
    return {"status": "ok", "agent": agent}


def resume_autonomous_agent(agent_id: str) -> dict[str, Any]:
    agent = get_agent(agent_id)
    if not agent:
        raise ValueError(f"agent not found: {agent_id}")
    if str(agent.get("status")) == "stopped":
        raise ValueError("stopped agents cannot resume; create a new agent")

    agent["status"] = "running"
    save_agent(agent)
    return {"status": "ok", "agent": agent}


def delete_autonomous_agent(agent_id: str) -> dict[str, Any]:
    agent = get_agent(agent_id)
    if not agent:
        raise ValueError(f"agent not found: {agent_id}")

    from trade_integrations.autonomous_agents.store import delete_agent

    delete_agent(agent_id)
    return {"status": "ok", "deleted": agent_id}
