"""Production recovery for orphaned autonomous agent bootstrap / streaming state."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_STALE_STREAMING_MAX_AGE_S = 120.0
_FINALIZE_BLOCKED_MAX_AGE_S = 300.0
_FINALIZE_RECOVERY_COOLDOWN_S = 300.0
_FINALIZE_RECOVERY_MAX_ATTEMPTS = 3


def _trade_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _vibe_sessions_dir() -> Path:
    return _trade_root() / "vibetrading" / "agent" / "sessions"


def _parse_iso_age_seconds(raw: str | None) -> float | None:
    if not raw:
        return None
    try:
        anchor = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - anchor).total_seconds()
    except (TypeError, ValueError):
        return None


def is_session_turn_in_flight(session_id: str, *, stale_after_seconds: float | None = None) -> bool:
    """True when the Vibe session store shows a running attempt.

    Any ``status=running`` attempt blocks recovery — long LLM turns may exceed
    ``stale_after_seconds``. Orphan running attempts are reclaimed by session
    recovery (``recover_stale_running_attempts``), not by clearing agent.streaming.
    """
    sid = str(session_id or "").strip()
    if not sid:
        return False
    attempts_dir = _vibe_sessions_dir() / sid / "attempts"
    if not attempts_dir.is_dir():
        return False

    now = datetime.now(timezone.utc)
    for attempt_dir in attempts_dir.iterdir():
        if not attempt_dir.is_dir():
            continue
        attempt_file = attempt_dir / "attempt.json"
        if not attempt_file.is_file():
            continue
        try:
            data = json.loads(attempt_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if str(data.get("status") or "") != "running":
            continue
        if stale_after_seconds is None:
            return True
        created_raw = str(data.get("created_at") or "")
        try:
            created = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
            age_s = (now - created).total_seconds()
        except (TypeError, ValueError):
            age_s = stale_after_seconds + 1
        if age_s <= stale_after_seconds:
            return True
    return False


def recover_stale_agent_streaming(*, max_age_s: float = _STALE_STREAMING_MAX_AGE_S) -> int:
    """Clear agent.streaming when no live session attempt exists (API crash mid-turn)."""
    from trade_integrations.autonomous_agents.bootstrap import safe_finalize_bootstrap_if_ready
    from trade_integrations.autonomous_agents.store import get_agent, list_agents, save_agent

    count = 0
    for agent in list_agents():
        if str(agent.get("status")) != "running":
            continue
        if not agent.get("streaming"):
            continue
        agent_id = str(agent.get("id") or "")
        session_id = str(agent.get("vibe_session_id") or "")
        if session_id and is_session_turn_in_flight(session_id):
            continue
        age_s = _parse_iso_age_seconds(
            str(agent.get("last_full_reasoning_at") or agent.get("updated_at") or "")
        )
        if age_s is not None and age_s < max_age_s:
            continue
        logger.warning(
            "clearing stale streaming for %s (age=%ss, session_in_flight=False)",
            agent_id,
            int(age_s or 0),
        )
        agent["streaming"] = False
        save_agent(agent)
        safe_finalize_bootstrap_if_ready(agent_id)
        count += 1
    return count


def _build_bootstrap_structure_recovery_message(*, agent_id: str, focus: str) -> str:
    return (
        "## Bootstrap finalize recovery\n"
        f"Agent `{agent_id}` recorded a decision but bootstrap is not ready to finalize.\n"
        f"1. Call `get_options_trade_plan(ticker=\"{focus}\")` or `get_options_trade_widget` once.\n"
        "2. Call `set_agent_watch_spec` with the chosen strategy and levels.\n"
        "3. Update `record_autonomous_decision` if needed — then stop.\n"
    )


def reserve_bootstrap_structure_recovery_slot(
    agent_id: str,
    *,
    cooldown_s: float = _FINALIZE_RECOVERY_COOLDOWN_S,
    max_attempts: int = _FINALIZE_RECOVERY_MAX_ATTEMPTS,
) -> bool:
    """Reserve a recovery attempt (cooldown + max attempts). Returns False if throttled."""
    from trade_integrations.autonomous_agents.store import get_agent, save_agent

    agent = get_agent(agent_id)
    if not agent:
        return False
    if str(agent.get("bootstrap_status") or "") != "running":
        return False
    if agent.get("streaming"):
        return False

    attempts = int(agent.get("bootstrap_finalize_recovery_count") or 0)
    if attempts >= max_attempts:
        return False

    cooldown_age = _parse_iso_age_seconds(str(agent.get("bootstrap_finalize_recovery_at") or ""))
    if cooldown_age is not None and cooldown_age < cooldown_s:
        return False

    now = datetime.now(timezone.utc).isoformat()
    agent["bootstrap_finalize_recovery_at"] = now
    agent["bootstrap_finalize_recovery_count"] = attempts + 1
    save_agent(agent)
    return True


def build_bootstrap_structure_recovery_message(*, agent_id: str, focus: str) -> str:
    return _build_bootstrap_structure_recovery_message(agent_id=agent_id, focus=focus)


def _schedule_bootstrap_structure_recovery(agent_id: str, *, focus: str) -> bool:
    """Enqueue a lightweight recovery turn on the API event loop."""
    try:
        import sys

        trade_root = _trade_root()
        agent_pkg = trade_root / "vibetrading" / "agent"
        if agent_pkg.is_dir() and str(agent_pkg) not in sys.path:
            sys.path.insert(0, str(agent_pkg))

        from src.api.async_bridge import schedule_coroutine
        from trade_integrations.autonomous_agents.store import get_agent, save_agent
    except Exception:
        logger.debug("bootstrap structure recovery schedule import failed", exc_info=True)
        return False

    agent = get_agent(agent_id)
    if not agent:
        return False
    session_id = str(agent.get("vibe_session_id") or "")
    if not session_id:
        return False

    if not reserve_bootstrap_structure_recovery_slot(agent_id):
        return False

    async def _enqueue() -> None:
        host = sys.modules.get("api_server") or sys.modules.get("agent.api_server")
        svc = host._get_session_service() if host else None
        if not svc:
            return
        await svc.send_message(
            session_id,
            _build_bootstrap_structure_recovery_message(agent_id=agent_id, focus=focus),
        )

    handle = schedule_coroutine(_enqueue(), label=f"bootstrap-recover-{agent_id[:12]}")
    if handle is None:
        return False

    logger.info("scheduled bootstrap structure recovery for %s", agent_id)
    return True


def recover_bootstrap_finalize_blocked(
    *,
    max_age_s: float = _FINALIZE_BLOCKED_MAX_AGE_S,
    recovery_cooldown_s: float = _FINALIZE_RECOVERY_COOLDOWN_S,
    max_attempts: int = _FINALIZE_RECOVERY_MAX_ATTEMPTS,
) -> int:
    """Recover bootstrap stuck at running with a decision but no structured plan."""
    from trade_integrations.autonomous_agents.bootstrap import (
        bootstrap_finalize_prerequisites_met,
        finalize_bootstrap_if_ready,
    )
    from trade_integrations.autonomous_agents.store import get_agent, list_agents, save_agent

    count = 0
    for agent in list_agents():
        if str(agent.get("status")) != "running":
            continue
        if str(agent.get("bootstrap_status") or "") != "running":
            continue
        if not agent.get("last_decision"):
            continue
        if agent.get("streaming"):
            continue
        if str(agent.get("pause_reason") or "") == "infra":
            continue

        agent_id = str(agent.get("id") or "")
        if not agent_id:
            continue

        if bootstrap_finalize_prerequisites_met(agent):
            if finalize_bootstrap_if_ready(agent_id):
                count += 1
            continue

        age_s = _parse_iso_age_seconds(str(agent.get("updated_at") or agent.get("created_at") or ""))
        if age_s is None or age_s < max_age_s:
            continue

        attempts = int(agent.get("bootstrap_finalize_recovery_count") or 0)
        if attempts >= max_attempts:
            logger.warning("bootstrap finalize recovery exhausted for %s", agent_id)
            latest = get_agent(agent_id) or agent
            latest["bootstrap_status"] = "failed"
            latest["bootstrap_error"] = (
                "bootstrap could not finalize structured plan and watch_spec after recovery retries"
            )
            latest["bootstrap_completed_at"] = datetime.now(timezone.utc).isoformat()
            save_agent(latest)
            count += 1
            continue

        cooldown_age = _parse_iso_age_seconds(str(agent.get("bootstrap_finalize_recovery_at") or ""))
        if cooldown_age is not None and cooldown_age < recovery_cooldown_s:
            continue

        symbols = list(agent.get("symbols") or ["NIFTY"])
        focus = str(symbols[0] if symbols else "NIFTY")
        if _schedule_bootstrap_structure_recovery(agent_id, focus=focus):
            count += 1

    return count


def run_autonomous_agent_recovery() -> dict[str, int]:
    """Run all autonomous agent recovery passes (list route + API startup)."""
    counts: dict[str, int] = {}
    try:
        counts["stale_streaming"] = recover_stale_agent_streaming()
    except Exception:
        logger.debug("stale streaming recovery failed", exc_info=True)
        counts["stale_streaming"] = 0
    try:
        counts["finalize_blocked"] = recover_bootstrap_finalize_blocked()
    except Exception:
        logger.debug("bootstrap finalize recovery failed", exc_info=True)
        counts["finalize_blocked"] = 0
    return counts
