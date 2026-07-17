"""Ensure the detached Nautilus watch process is running for bridge agents (multi-agent registry)."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_LAUNCH_VERIFY_SEC = 2.0


def _trade_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _log_dir() -> Path:
    d = _trade_root() / "log"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _pidfile() -> Path:
    return _log_dir() / "nautilus-watch.pid"


def _agent_id_file() -> Path:
    return _log_dir() / "nautilus-watch.agent_id"


def _registry_file() -> Path:
    return _log_dir() / "nautilus-watch.agents.json"


def _logfile() -> Path:
    return _log_dir() / "nautilus-watch.log"


def _watch_enabled() -> bool:
    try:
        from nautilus_openalgo_bridge.config import is_watch_enabled

        return is_watch_enabled()
    except ImportError:
        raw = os.getenv("NAUTILUS_WATCH_ENABLE", "true").strip().lower()
        return raw not in {"0", "false", "no", "off"}


def _process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _read_pid() -> int | None:
    path = _pidfile()
    if not path.is_file():
        return None
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except ValueError:
        return None


def _read_bound_agent_id() -> str | None:
    """Legacy single-agent binding file (first registry agent when present)."""
    registry = load_registry()
    agents = registry.get("agents") or []
    if agents:
        return str(agents[0].get("agent_id") or "") or None
    path = _agent_id_file()
    if not path.is_file():
        return None
    value = path.read_text(encoding="utf-8").strip()
    return value or None


def load_registry() -> dict[str, Any]:
    path = _registry_file()
    if not path.is_file():
        return {"node_pid": None, "agents": [], "updated_at": None}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"node_pid": None, "agents": [], "updated_at": None}
    if not isinstance(payload, dict):
        return {"node_pid": None, "agents": [], "updated_at": None}
    agents = payload.get("agents")
    if not isinstance(agents, list):
        agents = []
    return {
        "node_pid": payload.get("node_pid"),
        "agents": [row for row in agents if isinstance(row, dict)],
        "updated_at": payload.get("updated_at"),
    }


def save_registry(payload: dict[str, Any]) -> dict[str, Any]:
    path = _registry_file()
    payload = dict(payload)
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    pid = payload.get("node_pid")
    agents = payload.get("agents") or []
    if isinstance(pid, int) and pid > 0:
        _pidfile().write_text(str(pid), encoding="utf-8")
    elif agents:
        _pidfile().unlink(missing_ok=True)
    if agents:
        first = str(agents[0].get("agent_id") or "").strip()
        if first:
            _agent_id_file().write_text(first, encoding="utf-8")
    else:
        _agent_id_file().unlink(missing_ok=True)
    return payload


def list_registry_agents() -> list[dict[str, Any]]:
    return list(load_registry().get("agents") or [])


def get_registry_agent_ids() -> list[str]:
    return [
        str(row.get("agent_id") or "").strip()
        for row in list_registry_agents()
        if str(row.get("agent_id") or "").strip()
    ]


def is_agent_in_registry(agent_id: str) -> bool:
    agent_id = str(agent_id or "").strip()
    if not agent_id:
        return False
    return agent_id in get_registry_agent_ids()


def _agent_market_and_symbols(agent_id: str) -> tuple[str, list[str]]:
    try:
        from trade_integrations.autonomous_agents.store import get_agent
        from trade_integrations.execution.routing_context import resolve_agent_routing

        agent = get_agent(agent_id) or {}
        if not agent:
            logger.error("Nautilus registry bind skipped — agent %s not found", agent_id)
            return "IN", []
        routing = resolve_agent_routing(agent)
        return routing.market, list(routing.watch_symbols)
    except Exception as exc:
        logger.error("Nautilus registry bind failed for %s: %s", agent_id, exc)
        return "IN", []


def add_agent_to_registry(agent_id: str) -> dict[str, Any]:
    agent_id = str(agent_id or "").strip()
    if not agent_id:
        raise ValueError("agent_id required")
    registry = load_registry()
    agents: list[dict[str, Any]] = list(registry.get("agents") or [])
    old_ids = sorted(get_registry_agent_ids())
    market, symbols = _agent_market_and_symbols(agent_id)
    if not symbols:
        raise ValueError(f"cannot bind agent {agent_id} to Nautilus registry — missing agent or watch symbols")
    now = datetime.now(timezone.utc).isoformat()
    replaced = False
    for row in agents:
        if str(row.get("agent_id") or "") == agent_id:
            row["market"] = market
            row["symbols"] = symbols
            row["bound_at"] = row.get("bound_at") or now
            replaced = True
            break
    if not replaced:
        agents.append(
            {
                "agent_id": agent_id,
                "market": market,
                "symbols": symbols,
                "bound_at": now,
            }
        )
    registry["agents"] = agents
    registry["node_agent_ids"] = sorted(str(row.get("agent_id") or "") for row in agents if row.get("agent_id"))
    pid = _read_pid()
    if pid is not None and _process_alive(pid):
        registry["node_pid"] = pid
    saved = save_registry(registry)
    new_ids = sorted(get_registry_agent_ids())
    if old_ids != new_ids:
        live_pid = _read_pid()
        if live_pid is not None and _process_alive(live_pid):
            logger.info(
                "Nautilus registry changed %s → %s — restarting watch node",
                old_ids,
                new_ids,
            )
            _stop_existing()
    return saved


def remove_agent_from_registry(agent_id: str) -> dict[str, Any]:
    agent_id = str(agent_id or "").strip()
    registry = load_registry()
    agents = [
        row
        for row in (registry.get("agents") or [])
        if str(row.get("agent_id") or "") != agent_id
    ]
    registry["agents"] = agents
    if not agents:
        registry["node_pid"] = None
    return save_registry(registry)


def stop_nautilus_watch_completely() -> dict[str, Any]:
    """Stop the detached Nautilus watch process and clear the agent registry."""
    reconcile_stale_watch_pid()
    had_pid = _read_pid() is not None
    _stop_existing()
    registry = load_registry()
    cleared_agents = len(registry.get("agents") or [])
    registry["agents"] = []
    registry["node_pid"] = None
    save_registry(registry)
    return {
        "stopped": True,
        "had_process": had_pid,
        "registry_agents_cleared": cleared_agents,
    }


def reconcile_stale_watch_pid() -> bool:
    """Remove pid/registry binding when the recorded process is not alive. Returns True if cleared."""
    pid = _read_pid()
    registry = load_registry()
    reg_pid = registry.get("node_pid")
    active_pid = pid or (reg_pid if isinstance(reg_pid, int) else None)
    if active_pid is None:
        return False
    if _process_alive(active_pid):
        return False
    logger.warning("nautilus watch pid %s is stale — clearing pid/registry files", active_pid)
    _pidfile().unlink(missing_ok=True)
    _agent_id_file().unlink(missing_ok=True)
    registry["node_pid"] = None
    save_registry(registry)
    return True


def get_watch_process_status(*, reconcile: bool = True) -> dict[str, str | int | bool | list | None]:
    """Return detached Nautilus watch process state for stack/runtime APIs."""
    if reconcile:
        reconcile_stale_watch_pid()
    pid = _read_pid()
    registry = load_registry()
    reg_pid = registry.get("node_pid")
    if pid is None and isinstance(reg_pid, int):
        pid = reg_pid
    bound = _read_bound_agent_id()
    alive = pid is not None and _process_alive(pid)
    agent_ids = get_registry_agent_ids()
    return {
        "enabled": _watch_enabled(),
        "pid": pid,
        "alive": alive,
        "bound_agent_id": bound,
        "registry_agent_ids": agent_ids,
        "registry_agents": list_registry_agents(),
        "log_file": str(_logfile()),
    }


def _watch_launch_script() -> Path:
    script = _trade_root() / "scripts" / "run_nautilus_watch.sh"
    if not script.is_file():
        raise FileNotFoundError(f"missing launch script: {script}")
    return script


def _stop_existing() -> None:
    pid = _read_pid()
    if pid is not None and _process_alive(pid):
        try:
            os.kill(pid, 15)
        except OSError:
            pass
    _pidfile().unlink(missing_ok=True)
    _agent_id_file().unlink(missing_ok=True)
    registry = load_registry()
    registry["node_pid"] = None
    save_registry(registry)


def _launch_watch(*, use_registry: bool = True) -> None:
    root = _trade_root()
    script = _watch_launch_script()
    cmd = [str(script)]
    if use_registry and get_registry_agent_ids():
        cmd.append("--registry")
    else:
        agents = get_registry_agent_ids()
        if agents:
            cmd.extend(["--agent-id", agents[0]])

    log_path = _logfile()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log:
        proc = subprocess.Popen(
            cmd,
            cwd=str(root),
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    registry = load_registry()
    registry["node_pid"] = proc.pid
    save_registry(registry)

    time.sleep(_LAUNCH_VERIFY_SEC)
    if not _process_alive(proc.pid):
        tail = ""
        try:
            lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
            tail = "\n".join(lines[-5:])
        except OSError:
            pass
        _pidfile().unlink(missing_ok=True)
        registry["node_pid"] = None
        save_registry(registry)
        raise RuntimeError(
            f"Nautilus watch exited immediately (pid {proc.pid}). "
            f"Check {log_path}. Recent log:\n{tail}"
        )


def ensure_nautilus_watch_for_agent(agent_id: str, *, restart_if_bound_elsewhere: bool = True) -> str | None:
    """Add agent to registry and start detached Nautilus watch if enabled. Returns warning or None."""
    agent_id = str(agent_id or "").strip()
    if not agent_id or not _watch_enabled():
        return None

    reconcile_stale_watch_pid()
    add_agent_to_registry(agent_id)

    pid = _read_pid()
    if pid is not None and _process_alive(pid):
        if is_agent_in_registry(agent_id):
            logger.info("Nautilus watch alive — agent %s in registry (pid %s)", agent_id, pid)
            return None
        if not restart_if_bound_elsewhere:
            others = [a for a in get_registry_agent_ids() if a != agent_id]
            return (
                f"Nautilus watch running but registry mismatch; agents={others}. "
                f"Run: trade start nautilus-watch --registry"
            )

    try:
        if pid is not None and _process_alive(pid) and restart_if_bound_elsewhere:
            _stop_existing()
        _launch_watch(use_registry=True)
        logger.info(
            "started Nautilus watch registry=%s (pid %s)",
            get_registry_agent_ids(),
            _read_pid(),
        )
        return None
    except Exception as exc:
        logger.warning("failed to start Nautilus watch for %s: %s", agent_id, exc, exc_info=True)
        return (
            f"Nautilus watch not started ({exc}). "
            f"Run: trade start nautilus-watch --registry"
        )


def ensure_nautilus_watch_for_running_agents() -> int:
    """Ensure registry includes all running Nautilus-watch agents; start node if needed."""
    if not _watch_enabled():
        return 0
    try:
        from trade_integrations.autonomous_agents.store import list_agents
        from trade_integrations.execution.profile import resolve_profile
    except Exception:
        return 0

    reconcile_stale_watch_pid()
    started = 0
    for agent in list_agents():
        if str(agent.get("status")) != "running":
            continue
        try:
            profile = resolve_profile(agent=agent)
        except Exception:
            continue
        if not profile.uses_nautilus_watch:
            continue
        agent_id = str(agent.get("id") or "")
        if not agent_id:
            continue
        add_agent_to_registry(agent_id)

    agent_ids = get_registry_agent_ids()
    if not agent_ids:
        return 0

    pid = _read_pid()
    if pid is not None and _process_alive(pid):
        return len(agent_ids)

    try:
        _launch_watch(use_registry=True)
        started = 1
    except Exception as exc:
        logger.warning("ensure_nautilus_watch_for_running_agents failed: %s", exc)
    return started
