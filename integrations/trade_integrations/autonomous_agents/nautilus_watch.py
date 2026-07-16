"""Ensure the detached Nautilus watch process is running for India bridge agents."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


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
    path = _agent_id_file()
    if not path.is_file():
        return None
    value = path.read_text(encoding="utf-8").strip()
    return value or None


def get_watch_process_status() -> dict[str, str | int | bool | None]:
    """Return detached Nautilus watch process state for stack/runtime APIs."""
    pid = _read_pid()
    bound = _read_bound_agent_id()
    alive = pid is not None and _process_alive(pid)
    return {
        "enabled": _watch_enabled(),
        "pid": pid,
        "alive": alive,
        "bound_agent_id": bound,
        "log_file": str(_logfile()),
    }


def _pick_python() -> Path:
    root = _trade_root()
    for candidate in (root / ".venv-nautilus" / "bin" / "python", root / ".venv" / "bin" / "python"):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return Path(sys.executable)


def _watch_argv(*, agent_id: str | None, legacy_poll: bool) -> list[str]:
    py = _pick_python()
    args = [str(py), "-m", "nautilus_openalgo_bridge.runtime.run_watch_node"]
    if legacy_poll:
        args.append("--legacy-poll")
    if agent_id:
        args.extend(["--agent-id", agent_id])
    return args


def _stop_existing() -> None:
    pid = _read_pid()
    if pid is not None and _process_alive(pid):
        try:
            os.kill(pid, 15)
        except OSError:
            pass
    _pidfile().unlink(missing_ok=True)
    _agent_id_file().unlink(missing_ok=True)


def _launch_watch(*, agent_id: str | None) -> None:
    root = _trade_root()
    legacy_poll = not (root / ".venv-nautilus" / "bin" / "python").is_file()
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root / "integrations") + (
        f":{env['PYTHONPATH']}" if env.get("PYTHONPATH") else ""
    )
    env["TRADE_INTEGRATIONS_SKIP_APPLY"] = "1"
    log_path = _logfile()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log:
        proc = subprocess.Popen(
            _watch_argv(agent_id=agent_id, legacy_poll=legacy_poll),
            cwd=str(root),
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    _pidfile().write_text(str(proc.pid), encoding="utf-8")
    if agent_id:
        _agent_id_file().write_text(agent_id, encoding="utf-8")


def ensure_nautilus_watch_for_agent(agent_id: str, *, restart_if_bound_elsewhere: bool = True) -> str | None:
    """Start detached Nautilus watch for *agent_id* if enabled. Returns warning text or None."""
    agent_id = str(agent_id or "").strip()
    if not agent_id or not _watch_enabled():
        return None

    pid = _read_pid()
    bound = _read_bound_agent_id()
    if pid is not None and _process_alive(pid):
        if bound == agent_id:
            return None
        if not restart_if_bound_elsewhere:
            return f"Nautilus watch running for another agent ({bound or 'unknown'}); restart manually with trade start nautilus-watch --agent-id {agent_id}"

    try:
        if pid is not None and _process_alive(pid):
            _stop_existing()
        _launch_watch(agent_id=agent_id)
        mode = "legacy poll" if not (_trade_root() / ".venv-nautilus" / "bin" / "python").is_file() else "TradingNode"
        logger.info("started Nautilus watch (%s) for %s", mode, agent_id)
        return None
    except Exception as exc:
        logger.warning("failed to start Nautilus watch for %s: %s", agent_id, exc, exc_info=True)
        return (
            f"Nautilus watch not started ({exc}). "
            f"Run: trade start nautilus-watch --agent-id {agent_id}"
        )


def ensure_nautilus_watch_for_running_agents() -> int:
    """Start watch for the first running India bridge agent, if any. Returns count started/skipped ok."""
    if not _watch_enabled():
        return 0
    try:
        from trade_integrations.autonomous_agents.store import list_agents
        from trade_integrations.execution.profile import resolve_profile
    except Exception:
        return 0

    for agent in list_agents():
        if str(agent.get("status")) != "running":
            continue
        try:
            profile = resolve_profile(agent=agent)
        except Exception:
            continue
        if not profile.uses_nautilus_handoff:
            continue
        agent_id = str(agent.get("id") or "")
        if not agent_id:
            continue
        if ensure_nautilus_watch_for_agent(agent_id, restart_if_bound_elsewhere=False) is None:
            return 1
    return 0
