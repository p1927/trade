"""Ensure watch_spec stays aligned after REVISE/ADJUST decisions."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _norm_strategy(name: str | None) -> str:
    return str(name or "").strip().lower().replace(" ", "_").replace("-", "_")


def _level_rules(watch_spec: dict[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    for row in watch_spec.get("rules") or []:
        if not isinstance(row, dict):
            continue
        metric = str(row.get("metric") or "")
        label = str(row.get("label") or "").lower()
        try:
            threshold = float(row.get("threshold"))
        except (TypeError, ValueError):
            continue
        if metric == "level_below":
            if "target" in label or "dip" in label:
                out["target"] = threshold
            elif "stop" in label:
                out["stop"] = threshold
            else:
                out["stop"] = threshold
        elif metric == "level_above":
            out.setdefault("target", threshold)
    return out


def extract_revision_levels(
    *,
    agent: dict[str, Any],
    decision_entry: dict[str, Any] | None = None,
    stop: float | None = None,
    target: float | None = None,
    spot: float | None = None,
    strategy: str | None = None,
) -> dict[str, Any]:
    thesis = dict(agent.get("thesis") or {})
    entry = dict(decision_entry or agent.get("last_decision") or {})
    return {
        "strategy": strategy or entry.get("strategy") or thesis.get("strategy"),
        "stop": stop if stop is not None else entry.get("stop") or thesis.get("stop"),
        "target": target if target is not None else entry.get("target") or thesis.get("target"),
        "spot": spot if spot is not None else entry.get("spot") or thesis.get("spot"),
    }


def watch_spec_matches_levels(
    watch_spec: dict[str, Any] | None,
    *,
    strategy: str | None,
    stop: float | None = None,
    target: float | None = None,
) -> bool:
    spec = dict(watch_spec or {})
    if not spec.get("rules"):
        return False
    if strategy:
        spec_strategy = _norm_strategy(spec.get("strategy"))
        if spec_strategy and spec_strategy != _norm_strategy(strategy):
            return False
    levels = _level_rules(spec)
    if stop is not None:
        existing = levels.get("stop")
        if existing is None or abs(float(existing) - float(stop)) > 0.01:
            return False
    if target is not None:
        existing = levels.get("target")
        if existing is None or abs(float(existing) - float(target)) > 0.01:
            return False
    return True


def revision_needs_watch_update(
    *,
    agent: dict[str, Any],
    decision: str,
    strategy: str | None = None,
    stop: float | None = None,
    target: float | None = None,
) -> bool:
    decision_upper = str(decision).strip().upper()
    if decision_upper not in {"REVISE", "ADJUST"}:
        return False
    levels = extract_revision_levels(agent=agent, strategy=strategy, stop=stop, target=target)
    if not levels.get("strategy") and stop is None and target is None:
        return False
    return not watch_spec_matches_levels(
        agent.get("watch_spec") or (agent.get("mandate_config") or {}).get("watch_spec"),
        strategy=levels.get("strategy"),
        stop=levels.get("stop"),
        target=levels.get("target"),
    )


def maybe_sync_watch_spec_on_revision(
    agent: dict[str, Any],
    *,
    decision: str,
    strategy: str | None = None,
    stop: float | None = None,
    target: float | None = None,
    spot: float | None = None,
) -> dict[str, Any]:
    """Rebuild and persist watch_spec when REVISE/ADJUST changes strategy or levels."""
    agent_id = str(agent.get("id") or "").strip()
    if not agent_id:
        return {"status": "skipped", "reason": "missing agent id"}
    if not revision_needs_watch_update(
        agent=agent,
        decision=decision,
        strategy=strategy,
        stop=stop,
        target=target,
    ):
        return {"status": "skipped", "reason": "watch_spec already aligned"}

    existing_spec = dict(agent.get("watch_spec") or {})
    from trade_integrations.autonomous_agents.bootstrap import _coerce_watch_rules

    if (
        _coerce_watch_rules(existing_spec.get("rules"))
        and existing_spec.get("derived_from") != "strategy_watch_spec"
    ):
        return {"status": "skipped", "reason": "explicit watch rules preserved"}

    levels = extract_revision_levels(
        agent=agent,
        strategy=strategy,
        stop=stop,
        target=target,
        spot=spot,
    )
    strategy_name = str(levels.get("strategy") or "").strip()
    if not strategy_name:
        return {"status": "skipped", "reason": "no strategy to derive watch rules"}

    from trade_integrations.autonomous_agents.mandate import mandate_config_from_agent
    from trade_integrations.autonomous_agents.strategy_watch_spec import (
        build_watch_spec_for_strategy,
        format_watch_spec_summary,
    )
    from trade_integrations.execution.profile import resolve_profile

    mc = mandate_config_from_agent(agent)
    symbols = list(agent.get("symbols") or ["NIFTY"])
    watch_spec = build_watch_spec_for_strategy(
        strategy=strategy_name,
        mandate=mc,
        symbols=symbols,
        spot=float(levels["spot"]) if levels.get("spot") is not None else None,
        target=float(levels["target"]) if levels.get("target") is not None else None,
        stop=float(levels["stop"]) if levels.get("stop") is not None else None,
    )
    now = datetime.now(timezone.utc).isoformat()
    agent["watch_spec"] = watch_spec
    agent["watch_spec_updated_at"] = now
    mc_dict = dict(agent.get("mandate_config") or {})
    mc_dict["watch_spec"] = watch_spec
    agent["mandate_config"] = mc_dict

    profile = resolve_profile(agent=agent)
    handoff_synced = False
    pending_activation = False
    from trade_integrations.autonomous_agents.plan_approval import is_plan_approved

    if profile.uses_nautilus_watch and is_plan_approved(agent):
        try:
            from trade_integrations.watch_registry.store import create_watch, list_watches, update_watch

            vibe_sid = str(agent.get("vibe_session_id") or "").strip()
            existing = list_watches(owner_kind="autonomous_agent", owner_id=agent_id, active_only=True)
            if existing:
                update_watch(str(existing[0].get("watch_id")), watch_spec=watch_spec)
            elif vibe_sid:
                create_watch(
                    owner_kind="autonomous_agent",
                    owner_id=agent_id,
                    vibe_session_id=vibe_sid,
                    watch_spec=watch_spec,
                    symbols=symbols,
                    label="strategy watch",
                )
        except Exception:
            pass
        try:
            from nautilus_openalgo_bridge.handoff import sync_watch_spec_to_handoff

            if profile.uses_nautilus_handoff:
                handoff_synced = sync_watch_spec_to_handoff(agent_id, watch_spec) is not None
        except Exception:
            handoff_synced = False
    elif profile.uses_nautilus_watch:
        agent["watch_spec_pending_activation"] = True
        pending_activation = True

    return {
        "status": "ok",
        "watch_spec_updated": True,
        "watch_spec_updated_at": now,
        "watch_summary": format_watch_spec_summary(watch_spec),
        "handoff_synced": handoff_synced,
        "watch_spec_pending_activation": pending_activation,
    }
