"""Hub paths for bridge handoffs and intent queues."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nautilus_openalgo_bridge.hub_paths import get_hub_dir

from nautilus_openalgo_bridge.config import get_bridge_config
from nautilus_openalgo_bridge.models import ExecutionIntent, PositionHandoff, StopRules, WatchSpec


def handoffs_root() -> Path:
    cfg = get_bridge_config()
    root = get_hub_dir() / "_data" / cfg.handoff_dir_name
    root.mkdir(parents=True, exist_ok=True)
    return root


def intents_root() -> Path:
    cfg = get_bridge_config()
    root = get_hub_dir() / "_data" / cfg.intent_queue_dir_name
    root.mkdir(parents=True, exist_ok=True)
    return root


def handoff_path(agent_id: str) -> Path:
    return handoffs_root() / f"{agent_id}.json"


def handoff_mtime(agent_id: str) -> float | None:
    path = handoff_path(agent_id)
    if not path.is_file():
        return None
    return path.stat().st_mtime


def save_handoff(handoff: PositionHandoff) -> PositionHandoff:
    path = handoff_path(handoff.agent_id)
    path.write_text(json.dumps(handoff.to_dict(), indent=2), encoding="utf-8")
    return handoff


def stamp_handoff_context_generation(agent_id: str, context_generation: str) -> PositionHandoff | None:
    """Persist market context generation on the bridge handoff (create shell if needed)."""
    agent_id = str(agent_id or "").strip()
    generation = str(context_generation or "").strip()
    if not agent_id or not generation:
        return None
    handoff = load_handoff(agent_id) or ensure_handoff_for_agent(agent_id)
    if handoff is None:
        return None
    handoff.context_generation = generation
    return save_handoff(handoff)


def load_handoff(agent_id: str) -> PositionHandoff | None:
    path = handoff_path(agent_id)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return PositionHandoff.from_dict(payload)


def clear_handoff(agent_id: str) -> bool:
    path = handoff_path(agent_id)
    if not path.is_file():
        return False
    path.unlink()
    return True


def load_agent_watch_spec(agent_id: str) -> dict[str, Any] | None:
    from nautilus_openalgo_bridge.hub_paths import agent_json_path

    path = agent_json_path(agent_id)
    if not path.is_file():
        return None
    try:
        agent = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(agent, dict):
        return None
    spec = agent.get("watch_spec") or agent.get("watch_rules")
    if isinstance(spec, dict):
        return spec
    if isinstance(spec, list):
        return {"rules": spec}
    return None


def build_handoff_shell_from_agent(
    agent: dict[str, Any],
    *,
    watch_spec: WatchSpec | None = None,
) -> PositionHandoff:
    """Minimal handoff when watch rules are set before a basket entry."""
    from trade_integrations.autonomous_agents.mandate_config import mandate_config_from_agent

    agent_id = str(agent.get("id") or "").strip()
    symbols = list(agent.get("symbols") or ["NIFTY"])
    underlying = str(symbols[0] if symbols else "NIFTY").upper()
    mc = mandate_config_from_agent(agent)
    spec = watch_spec or WatchSpec.from_dict(agent.get("watch_spec") or mc.watch_spec)
    constraints = dict(agent.get("constraints") or {})
    return PositionHandoff(
        agent_id=agent_id,
        widget_id=None,
        underlying=underlying,
        legs=[],
        entry_spot=0.0,
        watch_spec=spec,
        stop_rules=StopRules(
            max_loss_inr=float(constraints.get("max_daily_loss_inr") or 2_000) * 0.75,
            flatten_at_close=mc.needs_session_close_flatten(),
        ),
        vibe_session_id=agent.get("vibe_session_id"),
    )


def build_handoff_shell_from_hub_agent(agent_id: str) -> PositionHandoff | None:
    """Build handoff from hub agent JSON only (Nautilus venv safe)."""
    from nautilus_openalgo_bridge.hub_paths import load_agent_json

    agent = load_agent_json(agent_id)
    if not agent:
        return None
    agent = dict(agent)
    agent.setdefault("id", agent_id)
    from trade_integrations.autonomous_agents.mandate_config import mandate_config_from_agent

    mc = mandate_config_from_agent(agent)
    raw_spec = load_agent_watch_spec(agent_id) or agent.get("watch_spec") or {}
    spec = WatchSpec.from_dict(raw_spec if isinstance(raw_spec, dict) else {"rules": raw_spec})
    constraints = dict(agent.get("constraints") or {})
    symbols = list(agent.get("symbols") or ["NIFTY"])
    underlying = str(symbols[0] if symbols else "NIFTY").upper()
    return PositionHandoff(
        agent_id=agent_id,
        widget_id=None,
        underlying=underlying,
        legs=[],
        entry_spot=0.0,
        watch_spec=spec,
        stop_rules=StopRules(
            max_loss_inr=float(constraints.get("max_daily_loss_inr") or 2_000) * 0.75,
            flatten_at_close=mc.needs_session_close_flatten(),
        ),
        vibe_session_id=agent.get("vibe_session_id"),
    )


def ensure_handoff_for_agent(agent_id: str) -> PositionHandoff | None:
    """Ensure handoff file exists with watch_spec before Nautilus node starts."""
    agent_id = str(agent_id or "").strip()
    if not agent_id:
        return None
    existing = load_handoff(agent_id)
    if existing and existing.watch_spec.rules:
        return existing
    raw = load_agent_watch_spec(agent_id)
    if raw:
        return sync_watch_spec_to_handoff(agent_id, raw)
    shell = build_handoff_shell_from_hub_agent(agent_id)
    if shell is None:
        return None
    return save_handoff(shell)


def sync_watch_spec_to_handoff(agent_id: str, watch_spec: dict[str, Any]) -> PositionHandoff | None:
    """Persist watch rules on the bridge handoff file (create shell if needed)."""
    spec = WatchSpec.from_dict(watch_spec)
    existing = load_handoff(agent_id)
    if existing:
        existing.watch_spec = spec
        return save_handoff(existing)

    try:
        from trade_integrations.autonomous_agents.store import get_agent

        agent = get_agent(agent_id)
        if agent:
            return save_handoff(build_handoff_shell_from_agent(agent, watch_spec=spec))
    except ImportError:
        pass

    shell = build_handoff_shell_from_hub_agent(agent_id)
    if shell is None:
        return None
    shell.watch_spec = spec
    return save_handoff(shell)


def update_agent_thesis_from_handoff(handoff: PositionHandoff) -> None:
    """Merge handoff state into autonomous agent instance JSON."""
    from trade_integrations.autonomous_agents.store import get_agent, save_agent

    agent = get_agent(handoff.agent_id)
    if not agent:
        return

    thesis = dict(agent.get("thesis") or {})
    thesis["underlying"] = handoff.underlying
    thesis["entry_spot"] = handoff.entry_spot
    if handoff.widget_id:
        thesis["active_widget_id"] = handoff.widget_id
    if handoff.legs:
        thesis["open_legs"] = [leg.to_dict() for leg in handoff.legs]
    thesis["handoff_at"] = handoff.created_at
    agent["thesis"] = thesis

    if handoff.watch_spec.rules:
        watch_dict = handoff.watch_spec.to_dict()
        agent["watch_spec"] = watch_dict
        mc = dict(agent.get("mandate_config") or {})
        mc["watch_spec"] = watch_dict
        agent["mandate_config"] = mc

    save_agent(agent)


def clear_agent_position_state(agent_id: str) -> None:
    """Clear bridge handoff and mark thesis closed on EXIT."""
    from datetime import datetime, timezone

    from trade_integrations.autonomous_agents.store import get_agent, save_agent

    clear_handoff(agent_id)
    agent = get_agent(agent_id)
    if not agent:
        return
    thesis = dict(agent.get("thesis") or {})
    thesis["position_closed_at"] = datetime.now(timezone.utc).isoformat()
    thesis.pop("active_widget_id", None)
    thesis.pop("open_legs", None)
    agent["thesis"] = thesis
    save_agent(agent)


def enqueue_intent(intent: ExecutionIntent) -> Path:
    """Backward-compatible alias for submit_intent — queue intent for async execution."""
    from nautilus_openalgo_bridge.intent_queue import submit_intent

    return submit_intent(intent)
