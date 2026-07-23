"""Start and heal required infra for autonomous agents after commit."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from trade_integrations.autonomous_agents.defaults import DEFAULT_BUDGET_INR, DEFAULT_MAX_DAILY_LOSS_INR
from trade_integrations.autonomous_agents.store import get_agent, list_agents, save_agent
from trade_integrations.execution.profile import ExecutionProfile

logger = logging.getLogger(__name__)

_INFRA_HEAL_MIN_INTERVAL_S = 30


def start_required_infra(
    *,
    agent: dict[str, Any],
    profile: ExecutionProfile,
    proposal: dict[str, Any],
    primary_symbol: str,
    symbols: list[str],
    vibe_session_id: str,
    fresh_mandate_cfg: Any,
) -> tuple[list[str], list[str]]:
    """Start paper session, Nautilus watch, and handoff. Returns (blocking_errors, warnings)."""
    blocking: list[str] = []
    warnings: list[str] = []
    constraints = dict(proposal.get("constraints") or {})

    if profile.uses_openalgo_auto_paper and not profile.uses_nautilus_handoff:
        try:
            from trade_integrations.auto_paper.mcp_actions import start_auto_paper

            start_auto_paper(
                ticker=primary_symbol,
                budget_inr=float(constraints.get("budget_inr") or DEFAULT_BUDGET_INR),
                watchlist=symbols,
                max_daily_loss_inr=float(
                    constraints.get("max_daily_loss_inr") or DEFAULT_MAX_DAILY_LOSS_INR
                ),
                mandate=str(proposal.get("mandate") or ""),
                vibe_session_id=vibe_session_id,
                mandate_config=fresh_mandate_cfg.to_dict(),
                autonomous_agent_id=str(agent.get("id") or ""),
                nautilus_bridge_mode=profile.uses_nautilus_handoff,
            )
        except Exception as exc:
            logger.warning("start_auto_paper failed for %s", agent.get("id"), exc_info=True)
            msg = f"start_auto_paper failed: {exc}"
            if profile.market == "IN":
                blocking.append(msg)
            else:
                warnings.append(msg)

    if profile.uses_nautilus_watch:
        try:
            from trade_integrations.autonomous_agents.nautilus_watch import ensure_nautilus_watch_for_agent

            watch_warning = ensure_nautilus_watch_for_agent(str(agent.get("id") or ""))
            if watch_warning:
                msg = str(watch_warning)
                if profile.market == "IN":
                    blocking.append(msg)
                else:
                    warnings.append(msg)
        except Exception as exc:
            logger.warning("ensure_nautilus_watch failed for %s", agent.get("id"), exc_info=True)
            msg = (
                f"Nautilus watch not started ({exc}). "
                "Run: trade start nautilus-watch --registry"
            )
            if profile.market == "IN":
                blocking.append(msg)
            else:
                warnings.append(msg)

    # Handoff/watch_spec sync deferred until plan approval (R7-04) — see plan_approval.approve_agent_plan.

    return blocking, warnings


def infra_requirements_met(agent: dict[str, Any], profile: ExecutionProfile) -> bool:
    pending = list(agent.get("infra_pending") or [])
    return not pending and str(agent.get("pause_reason") or "") != "infra"


def _infra_heal_throttled(agent: dict[str, Any]) -> bool:
    last = str(agent.get("infra_last_attempt_at") or "")
    if not last:
        return False
    try:
        dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - dt).total_seconds()
        return age < _INFRA_HEAL_MIN_INTERVAL_S
    except ValueError:
        return False


def attempt_infra_heal(agent_id: str) -> dict[str, Any] | None:
    """Retry infra startup for an infra-paused agent. Returns updated agent or None."""
    agent = get_agent(agent_id)
    if not agent:
        return None
    if str(agent.get("pause_reason") or "") != "infra":
        return agent
    if _infra_heal_throttled(agent):
        return agent

    from trade_integrations.auto_paper.mandate_config import mandate_config_from_agent
    from trade_integrations.execution.profile import resolve_profile

    profile = resolve_profile(agent=agent)
    mc = mandate_config_from_agent(agent)
    symbols = list(agent.get("symbols") or [])
    primary = symbols[0] if symbols else "NIFTY"
    proposal = {
        "mandate": agent.get("mandate"),
        "constraints": agent.get("constraints"),
        "watch_spec": agent.get("watch_spec"),
        "mandate_config": agent.get("mandate_config"),
    }

    agent["infra_last_attempt_at"] = datetime.now(timezone.utc).isoformat()
    save_agent(agent)

    blocking, warnings = start_required_infra(
        agent=agent,
        profile=profile,
        proposal=proposal,
        primary_symbol=primary,
        symbols=symbols,
        vibe_session_id=str(agent.get("vibe_session_id") or ""),
        fresh_mandate_cfg=mc,
    )

    if blocking:
        agent["infra_pending"] = blocking
        agent["status"] = "paused"
        agent["pause_reason"] = "infra"
        save_agent(agent)
        return agent

    agent["status"] = "running"
    agent["pause_reason"] = None
    agent["infra_pending"] = []
    save_agent(agent)

    if warnings:
        logger.info("infra heal warnings for %s: %s", agent_id, warnings)
    return agent


def maybe_heal_infra_paused_agents() -> int:
    """Best-effort heal for infra-paused agents (hub poll fast path). Returns count healed."""
    healed = 0
    for agent in list_agents():
        if str(agent.get("pause_reason") or "") != "infra":
            continue
        agent_id = str(agent.get("id") or "")
        if not agent_id or _infra_heal_throttled(agent):
            continue
        before = str(agent.get("status") or "")
        updated = attempt_infra_heal(agent_id)
        if updated and str(updated.get("status") or "") == "running" and before == "paused":
            healed += 1
    return healed
