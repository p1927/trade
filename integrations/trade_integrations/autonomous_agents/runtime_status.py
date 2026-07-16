"""Runtime observability for autonomous agent instances."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def _nautilus_watch_enabled() -> bool:
    try:
        from nautilus_openalgo_bridge.config import is_watch_enabled

        return is_watch_enabled()
    except ImportError:
        raw = os.getenv("NAUTILUS_WATCH_ENABLE", "true").strip().lower()
        return raw not in {"0", "false", "no", "off"}


def _nautilus_process_alive() -> bool:
    candidates: list[Path] = []
    try:
        from trade_integrations.context.hub import get_hub_dir

        trade_root = get_hub_dir().parent.parent
        candidates.append(trade_root / "log" / "nautilus-watch.pid")
    except Exception:
        pass
    candidates.append(Path.home() / ".vibe-trading" / "logs" / "nautilus-watch.pid")

    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            pid = int(candidate.read_text(encoding="utf-8").strip())
            os.kill(pid, 0)
            return True
        except (OSError, ValueError):
            continue
    return False


def _handoff_active(agent_id: str) -> bool:
    try:
        from nautilus_openalgo_bridge.handoff import load_handoff

        return load_handoff(agent_id) is not None
    except ImportError:
        return False


def _paper_runtime() -> dict[str, Any]:
    try:
        from trade_integrations.auto_paper.mcp_actions import get_status

        return get_status()
    except Exception as exc:
        return {"error": str(exc)}


def build_agent_runtime(agent: dict[str, Any]) -> dict[str, Any]:
    """Trader brain state — distinct from HTTP infra health."""
    agent_id = str(agent.get("id") or "")
    mc = dict(agent.get("mandate_config") or {})
    alert_rules = dict(agent.get("alert_rules") or mc.get("alert_rules") or {})

    try:
        from trade_integrations.execution.profile import resolve_profile

        profile = resolve_profile(agent=agent)
    except Exception:
        profile = None

    paper = _paper_runtime()
    session = dict(paper.get("session") or {})
    linked = str(session.get("autonomous_agent_id") or "") == agent_id

    last_decision = agent.get("last_decision") or session.get("last_decision") if linked else agent.get("last_decision")

    nautilus_on = _nautilus_watch_enabled()
    nautilus_alive = _nautilus_process_alive()
    if profile is not None and profile.uses_nautilus_handoff:
        if not nautilus_on:
            watch_path = "nautilus_disabled"
        elif nautilus_alive or nautilus_on:
            watch_path = "nautilus_bridge"
        else:
            watch_path = "nautilus_bridge_poll"
    elif linked and session.get("nautilus_bridge_mode"):
        watch_path = "nautilus_bridge"
    else:
        watch_path = "legacy_auto_paper"

    return {
        "mandate_summary": {
            "holding_period": mc.get("holding_period"),
            "flatten_policy": mc.get("flatten_policy"),
            "product_type": mc.get("product_type"),
            "revision_policy": mc.get("revision_policy"),
            "confidence_threshold": mc.get("confidence_threshold")
            or (agent.get("constraints") or {}).get("confidence_threshold"),
        },
        "alert_rules_summary": {
            "spot_move_pct": alert_rules.get("spot_move_pct"),
            "vix_above": alert_rules.get("vix_above"),
            "thesis_break": alert_rules.get("thesis_break", True),
        },
        "scheduler_health": paper.get("scheduler_health") if linked else "unknown",
        "market_open": paper.get("market_open"),
        "nautilus_watch_enabled": _nautilus_watch_enabled(),
        "nautilus_process_alive": _nautilus_process_alive(),
        "watch_path": watch_path,
        "handoff_active": _handoff_active(agent_id) if agent_id else False,
        "paper_session_linked": linked,
        "last_decision": last_decision,
        "last_revision_at": agent.get("last_revision_at"),
        "last_bridge_alert_at": agent.get("last_bridge_alert_at"),
        "open_positions": paper.get("open_positions") if linked else None,
    }


def build_stack_health() -> dict[str, Any]:
    """Infra vs trader summary for hub header."""
    paper = _paper_runtime()
    return {
        "nautilus_watch_enabled": _nautilus_watch_enabled(),
        "nautilus_process_alive": _nautilus_process_alive(),
        "scheduler_health": paper.get("scheduler_health"),
        "market_open": paper.get("market_open"),
        "paper_session_enabled": bool((paper.get("session") or {}).get("enabled")),
    }


def enrich_agent(agent: dict[str, Any]) -> dict[str, Any]:
    out = dict(agent)
    out["runtime"] = build_agent_runtime(agent)
    return out
