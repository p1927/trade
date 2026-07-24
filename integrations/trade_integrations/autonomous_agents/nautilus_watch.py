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


class NautilusWatchLifecycleError(RuntimeError):
    """Raised when purge/start cannot reach a single desired watch process state."""


def _fetch_market_context_generation(agent_id: str) -> str | None:
    """Fetch authoritative context_generation once for handoff stamping."""
    agent_id = str(agent_id or "").strip()
    if not agent_id:
        return None
    agent: dict[str, Any] | None = None
    try:
        from trade_integrations.autonomous_agents.store import get_agent
        from trade_integrations.execution.trading_port import adapter_for_agent

        agent = get_agent(agent_id)
        if agent:
            return adapter_for_agent(agent).market_context().context_generation
    except Exception as exc:
        logger.debug("trading port market_context failed for %s: %s", agent_id, exc)
    if agent:
        try:
            from trade_integrations.execution.connector_context import load_active_connector_context

            ctx = load_active_connector_context(agent=agent)
            if ctx and ctx.execution_path != "openalgo":
                logger.warning(
                    "skip OpenAlgo fallback handoff stamp for %s (execution_path=%s)",
                    agent_id,
                    ctx.execution_path,
                )
                return None
        except Exception:
            logger.debug("connector context unavailable for handoff stamp %s", agent_id, exc_info=True)
    try:
        from nautilus_openalgo_bridge.openalgo_client import get_openalgo_client

        return get_openalgo_client().get_market_context().context_generation
    except Exception as exc:
        logger.warning("market context fetch failed for handoff stamp %s: %s", agent_id, exc)
        return None


def _stamp_handoff_market_context(agent_id: str) -> None:
    generation = _fetch_market_context_generation(agent_id)
    if not generation:
        return
    try:
        from nautilus_openalgo_bridge.handoff import stamp_handoff_context_generation

        stamp_handoff_context_generation(agent_id, generation)
    except Exception as exc:
        logger.warning("handoff context_generation stamp failed for %s: %s", agent_id, exc)


def _stamp_registry_handoff_contexts() -> None:
    for agent_id in get_registry_agent_ids():
        _stamp_handoff_market_context(agent_id)


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


def list_live_watch_pids() -> list[int]:
    """Live run_watch_node processes scoped to this trade repo."""
    return sorted(
        {
            pid
            for pid in _pgrep_watch_pids()
            if _process_in_trade_repo(pid) and _process_alive(pid)
        }
    )


def _write_service_claim(pid: int, command: str) -> None:
    claims_dir = _log_dir() / "claims"
    claims_dir.mkdir(parents=True, exist_ok=True)
    claim = claims_dir / "nautilus-watch.claim"
    started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    claim.write_text(
        f"pid={pid}\nport=\nroot={_trade_root()}\nstarted_at={started}\ncommand={command}\n",
        encoding="utf-8",
    )


def _launch_command_string(*, use_registry: bool, agent_id: str | None = None) -> str:
    if use_registry and get_registry_agent_ids():
        return "nautilus watch --registry"
    if agent_id:
        return f"nautilus watch --agent-id {agent_id}"
    agents = get_registry_agent_ids()
    if agents:
        return f"nautilus watch --agent-id {agents[0]}"
    return "nautilus watch"


def _finalize_watch_launch(*, command: str, expected_pid: int | None = None) -> int:
    """Bind pidfile, registry, and claim; fail if duplicate live nodes exist."""
    pid = expected_pid if expected_pid is not None else _read_pid()
    if pid is None or not _process_alive(pid):
        raise NautilusWatchLifecycleError("Nautilus watch failed to stay up")
    survivors = list_live_watch_pids()
    if survivors and (len(survivors) > 1 or survivors[0] != pid):
        raise NautilusWatchLifecycleError(f"duplicate Nautilus watch processes: {survivors}")
    registry = load_registry()
    registry["node_pid"] = pid
    save_registry(registry)
    _write_service_claim(pid, command)
    return pid


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
    agent_id = str(agent_id or "").strip()
    if not agent_id:
        return "IN", []
    try:
        from trade_integrations.watch_registry.scope import parse_nautilus_owner_id, symbols_for_owner

        kind, oid = parse_nautilus_owner_id(agent_id)
        syms = list(symbols_for_owner(agent_id))
        if not syms:
            return "IN", []
        if kind == "autonomous_agent" or agent_id.startswith("aa_"):
            from trade_integrations.autonomous_agents.store import get_agent
            from trade_integrations.execution.routing_context import resolve_agent_routing

            agent = get_agent(oid if kind == "autonomous_agent" else agent_id) or {}
            if agent:
                return resolve_agent_routing(agent).market, syms
        return "IN", syms
    except Exception as exc:
        logger.error("Nautilus registry bind failed for %s: %s", agent_id, exc)
        return "IN", []


def add_agent_to_registry(agent_id: str) -> dict[str, Any]:
    """Sync watch registry owner into log/nautilus-watch.agents.json (registry-only symbols)."""
    agent_id = str(agent_id or "").strip()
    if not agent_id:
        raise ValueError("agent_id required")
    try:
        from trade_integrations.watch_registry.store import sync_nautilus_registry_from_watches

        sync_nautilus_registry_from_watches(restart_if_changed=True)
    except Exception as exc:
        logger.error("watch registry sync failed for %s: %s", agent_id, exc)
        raise ValueError(f"cannot bind agent {agent_id} to Nautilus registry — {exc}") from exc
    if not is_agent_in_registry(agent_id):
        raise ValueError(
            f"cannot bind agent {agent_id} to Nautilus registry — no active watches in registry"
        )
    return load_registry()


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


def stop_nautilus_watch_node(*, clear_agents: bool = False) -> dict[str, Any]:
    """Stop the watch process and clear node_pid; optionally clear agent registry."""
    reconcile_stale_watch_pid()
    had_pid = _read_pid() is not None
    _stop_existing()
    cleared_agents = 0
    if clear_agents:
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


def stop_nautilus_watch_completely() -> dict[str, Any]:
    """Stop the detached Nautilus watch process and clear the agent registry."""
    return stop_nautilus_watch_node(clear_agents=True)


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


_WATCH_ORPHAN_PATTERN = "nautilus_openalgo_bridge.runtime.run_watch_node"
_GRACE_KILL_WAIT_SEC = 7.5


def _process_cmdline(pid: int) -> str:
    try:
        out = subprocess.run(
            ["ps", "-p", str(pid), "-o", "args="],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        return (out.stdout or "").strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _process_in_trade_repo(pid: int) -> bool:
    root = str(_trade_root())
    args = _process_cmdline(pid)
    if args and root in args:
        return True
    if args and any(token in args for token in ("cli._legacy", "cli.main", "app.py", "/vite")):
        return True
    try:
        out = subprocess.run(
            ["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        for line in (out.stdout or "").splitlines():
            if line.startswith("n") and line[1:].startswith(root):
                return True
    except (OSError, subprocess.SubprocessError):
        pass
    return False


def _pgrep_watch_pids() -> list[int]:
    try:
        out = subprocess.run(
            ["pgrep", "-f", _WATCH_ORPHAN_PATTERN],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    pids: list[int] = []
    for line in (out.stdout or "").splitlines():
        line = line.strip()
        if line.isdigit():
            pids.append(int(line))
    return pids


def _kill_pid_graceful(pid: int) -> None:
    if not _process_alive(pid):
        return
    try:
        os.kill(pid, 15)
    except OSError:
        return
    deadline = time.monotonic() + _GRACE_KILL_WAIT_SEC
    while time.monotonic() < deadline:
        if not _process_alive(pid):
            return
        time.sleep(0.5)
    try:
        os.kill(pid, 9)
    except OSError:
        pass


def purge_nautilus_watch_processes() -> dict[str, Any]:
    """Stop every Nautilus watch node for this repo (pidfile, registry pid, pgrep orphans)."""
    reconcile_stale_watch_pid()
    targets: set[int] = set()
    pid = _read_pid()
    if pid is not None:
        targets.add(pid)
    registry = load_registry()
    reg_pid = registry.get("node_pid")
    if isinstance(reg_pid, int):
        targets.add(reg_pid)
    for orphan in _pgrep_watch_pids():
        if _process_in_trade_repo(orphan):
            targets.add(orphan)

    killed: list[int] = []
    for target in sorted(targets):
        if _process_alive(target):
            logger.info("purging Nautilus watch process pid %s", target)
            _kill_pid_graceful(target)
            killed.append(target)

    _pidfile().unlink(missing_ok=True)
    _agent_id_file().unlink(missing_ok=True)
    claim = _log_dir() / "claims" / "nautilus-watch.claim"
    claim.unlink(missing_ok=True)
    registry = load_registry()
    registry["node_pid"] = None
    save_registry(registry)

    time.sleep(0.5)
    for orphan in _pgrep_watch_pids():
        if _process_in_trade_repo(orphan) and _process_alive(orphan):
            logger.info("purging surviving Nautilus watch orphan pid %s", orphan)
            _kill_pid_graceful(orphan)
            killed.append(orphan)

    survivors = list_live_watch_pids()
    if survivors:
        logger.error("Nautilus purge incomplete — survivors: %s", survivors)
    return {"purged": not survivors, "killed_pids": killed, "survivors": survivors}


def _stop_existing() -> None:
    purge_nautilus_watch_processes()


def _launch_watch(*, use_registry: bool = True, agent_id: str | None = None) -> int:
    if use_registry and not get_registry_agent_ids():
        logger.info("skip Nautilus launch — no agents in registry")
        return 0
    root = _trade_root()
    script = _watch_launch_script()
    cmd = [str(script)]
    if use_registry and get_registry_agent_ids():
        cmd.append("--registry")
    elif agent_id:
        cmd.extend(["--agent-id", agent_id])
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
    time.sleep(_LAUNCH_VERIFY_SEC)
    if not _process_alive(proc.pid):
        tail = ""
        try:
            lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
            tail = "\n".join(lines[-5:])
        except OSError:
            pass
        registry = load_registry()
        registry["node_pid"] = None
        save_registry(registry)
        raise RuntimeError(
            f"Nautilus watch exited immediately (pid {proc.pid}). "
            f"Check {log_path}. Recent log:\n{tail}"
        )
    command = _launch_command_string(use_registry=use_registry, agent_id=agent_id)
    pid = _finalize_watch_launch(command=command, expected_pid=proc.pid)
    _stamp_registry_handoff_contexts()
    return pid


def run_stack_nautilus_start(*, agent_id: str | None = None, skip_adopt: bool = False) -> dict[str, Any]:
    """Single stack/heal entry for start/adopt under the cross-process lifecycle lock."""
    if not _watch_enabled():
        return {"status": "skipped", "reason": "disabled"}

    from trade_integrations.watch_registry.sync_lock import watch_registry_mutation_lock
    from trade_integrations.watch_registry.store import _sync_nautilus_registry_from_watches_locked

    agent_id = str(agent_id or "").strip() or None

    with watch_registry_mutation_lock():
        reconcile_stale_watch_pid()
        existing = _read_pid()
        bound = _read_bound_agent_id()

        if (
            existing is not None
            and _process_alive(existing)
            and agent_id
            and bound
            and bound != agent_id
        ):
            purge = purge_nautilus_watch_processes()
            if purge.get("survivors"):
                return {"status": "error", "reason": "purge_incomplete", **purge}
            existing = None

        if not skip_adopt and existing is not None and _process_alive(existing):
            if get_registry_agent_ids():
                command = "nautilus watch --registry"
                _finalize_watch_launch(command=command, expected_pid=existing)
                return {"status": "ok", "pid": existing, "adopted": True}
            if agent_id and not bound:
                _agent_id_file().write_text(agent_id, encoding="utf-8")
                command = f"nautilus watch --agent-id {agent_id}"
                _finalize_watch_launch(command=command, expected_pid=existing)
                return {"status": "ok", "pid": existing, "adopted": True}
            command = "nautilus watch"
            _finalize_watch_launch(command=command, expected_pid=existing)
            return {"status": "ok", "pid": existing, "adopted": True}

        if skip_adopt and existing is not None and _process_alive(existing):
            purge = purge_nautilus_watch_processes()
            if purge.get("survivors"):
                return {"status": "error", "reason": "purge_incomplete", **purge}

        _sync_nautilus_registry_from_watches_locked(restart_if_changed=False)
        registry_agents = get_registry_agent_ids()
        if not registry_agents and not agent_id:
            return {"status": "skipped", "reason": "no_agents"}

        try:
            pid = _launch_watch(
                use_registry=bool(registry_agents),
                agent_id=agent_id if not registry_agents else None,
            )
        except Exception as exc:
            logger.exception("stack Nautilus start failed")
            return {"status": "error", "reason": "launch_failed", "error": str(exc)}

        if not pid:
            return {"status": "skipped", "reason": "no_agents"}
        return {"status": "ok", "pid": pid, "adopted": False}


def ensure_nautilus_watch_for_agent(agent_id: str, *, restart_if_bound_elsewhere: bool = True) -> str | None:
    """Add agent to registry and start detached Nautilus watch if enabled. Returns warning or None."""
    agent_id = str(agent_id or "").strip()
    if not agent_id or not _watch_enabled():
        return None

    try:
        from trade_integrations.autonomous_agents.store import get_agent

        agent = get_agent(agent_id)
        if agent and str(agent.get("status") or "") == "draft":
            return None
    except Exception:
        pass

    reconcile_stale_watch_pid()
    try:
        from trade_integrations.watch_registry.store import sync_nautilus_registry_from_watches

        sync_nautilus_registry_from_watches(restart_if_changed=False)
    except Exception:
        logger.warning("watch registry sync failed for %s", agent_id, exc_info=True)

    if not is_agent_in_registry(agent_id):
        try:
            from trade_integrations.autonomous_agents.plan_approval import is_plan_approved
            from trade_integrations.autonomous_agents.store import get_agent

            agent_row = get_agent(agent_id)
            if agent_row and not is_plan_approved(agent_row):
                return None
        except Exception:
            pass
        return f"Nautilus watch not started — no active watches for {agent_id}"

    _stamp_handoff_market_context(agent_id)

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

    reconcile_stale_watch_pid()
    try:
        from trade_integrations.watch_registry.store import sync_nautilus_registry_from_watches

        sync_nautilus_registry_from_watches(restart_if_changed=True)
    except Exception:
        logger.debug("ensure_nautilus_watch_for_running_agents registry sync failed", exc_info=True)

    agent_ids = get_registry_agent_ids()
    if not agent_ids:
        return 0

    pid = _read_pid()
    if pid is not None and _process_alive(pid):
        return len(agent_ids)

    started = 0
    try:
        _launch_watch(use_registry=True)
        started = 1
    except Exception as exc:
        logger.warning("ensure_nautilus_watch_for_running_agents failed: %s", exc)
    return started
