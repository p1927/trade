"""Persist autonomous agent evaluation runs during simulator replay."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trade_integrations.context.hub import get_hub_dir
from trade_integrations.stock_simulator.integration import is_simulator_active

_RUNS_DIR = Path("_data") / "sim_runs"
_active_run_id: str | None = None


def _runs_root() -> Path:
    root = get_hub_dir() / _RUNS_DIR
    root.mkdir(parents=True, exist_ok=True)
    return root


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def start_run(
    *,
    agent_id: str,
    replay_date: str | None = None,
    starting_capital: float | None = None,
) -> dict[str, Any]:
    global _active_run_id
    from trade_integrations.stock_simulator.replay import get_replay_service

    svc = get_replay_service()
    run_id = f"sim_{uuid.uuid4().hex[:12]}"
    payload: dict[str, Any] = {
        "run_id": run_id,
        "agent_id": agent_id,
        "replay_date": replay_date or svc.config.replay_date,
        "started_at": _now_iso(),
        "starting_capital": starting_capital,
        "decisions": [],
        "fills": [],
        "status": "running",
        "sim_status": svc.status(),
    }
    path = _runs_root() / f"{run_id}.json"
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    _active_run_id = run_id
    return payload


def active_run_id() -> str | None:
    return _active_run_id


def load_run(run_id: str) -> dict[str, Any] | None:
    path = _runs_root() / f"{run_id}.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _save_run(run: dict[str, Any]) -> None:
    run_id = str(run.get("run_id") or "")
    if not run_id:
        return
    path = _runs_root() / f"{run_id}.json"
    path.write_text(json.dumps(run, indent=2, default=str), encoding="utf-8")


def record_decision(
    *,
    agent_id: str,
    decision: dict[str, Any],
    run_id: str | None = None,
) -> None:
    if not is_simulator_active():
        return
    rid = run_id or _active_run_id
    if not rid:
        start_run(agent_id=agent_id)
        rid = _active_run_id
    if not rid:
        return
    run = load_run(rid)
    if run is None:
        return
    entry = dict(decision)
    entry["recorded_at"] = _now_iso()
    decisions = list(run.get("decisions") or [])
    decisions.append(entry)
    run["decisions"] = decisions[-500:]
    _save_run(run)


def record_fill(*, run_id: str | None, fill: dict[str, Any]) -> None:
    if not is_simulator_active():
        return
    rid = run_id or _active_run_id
    if not rid:
        return
    run = load_run(rid)
    if run is None:
        return
    fills = list(run.get("fills") or [])
    fills.append({**fill, "recorded_at": _now_iso()})
    run["fills"] = fills[-1000:]
    _save_run(run)


def finalize_run(
    *,
    run_id: str | None = None,
    session_pnl: float | None = None,
    final_positions: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    global _active_run_id
    rid = run_id or _active_run_id
    if not rid:
        return None
    run = load_run(rid)
    if run is None:
        return None
    run["status"] = "completed"
    run["completed_at"] = _now_iso()
    if session_pnl is not None:
        run["session_pnl"] = session_pnl
    if final_positions is not None:
        run["final_positions"] = final_positions
    from trade_integrations.stock_simulator.replay import get_replay_service

    run["sim_status"] = get_replay_service().status()
    _save_run(run)
    if _active_run_id == rid:
        _active_run_id = None
    return run
