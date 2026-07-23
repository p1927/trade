"""Remove obsolete standalone cron jobs from Vibe scheduler store (~/.vibe-trading)."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# Pre-autonomous migration cron names (delete-only; historical job ids).
OBSOLETE_SCHEDULER_JOB_IDS = frozenset(
    {
        "auto-paper-agent-turn",
        "auto-paper-intraday",
        "auto-paper-thesis-break",
        "auto-paper-scheduler-health",
        "auto-paper-session-close-flatten",
    }
)


def agent_scheduler_job_ids(agent_id: str) -> frozenset[str]:
    aid = str(agent_id or "").strip()
    if not aid:
        return frozenset()
    return frozenset({f"{aid}-watch", f"{aid}-research", f"{aid}-quant", f"{aid}-infra-heal"})


def _delete_job_ids_via_json(
    job_ids: frozenset[str],
    *,
    store_path: Path,
    removed: dict[str, bool],
) -> dict[str, bool]:
    pending = frozenset(job_id for job_id in job_ids if not removed.get(job_id))
    if not pending or not store_path.is_file():
        return removed

    try:
        envelope = json.loads(store_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("could not read scheduler store %s: %s", store_path, exc)
        return removed

    jobs = envelope.get("jobs")
    if not isinstance(jobs, list):
        return removed

    before_ids = {str(j.get("id")) for j in jobs if isinstance(j, dict)}
    filtered = [j for j in jobs if isinstance(j, dict) and str(j.get("id")) not in pending]
    if len(filtered) == len(jobs):
        return removed

    for job_id in pending:
        if job_id in before_ids:
            removed[job_id] = True

    envelope["jobs"] = filtered
    tmp = store_path.with_name(f".{store_path.name}.{os.getpid()}.tmp")
    payload = json.dumps(envelope, ensure_ascii=False, indent=2)
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, store_path)
    return removed


def _delete_job_ids_from_store(job_ids: frozenset[str], *, store_path: Path | None = None) -> dict[str, bool]:
    removed: dict[str, bool] = {job_id: False for job_id in job_ids}
    if not job_ids:
        return removed

    path = store_path or _default_store_path()

    if store_path is None:
        try:
            from src.scheduled_research.store import ScheduledResearchJobStore

            store = ScheduledResearchJobStore()
            for job_id in job_ids:
                if store.delete(job_id):
                    removed[job_id] = True
        except Exception:
            logger.debug("ScheduledResearchJobStore delete unavailable; falling back to file edit", exc_info=True)

    pending = frozenset(job_id for job_id in job_ids if not removed.get(job_id))
    if pending:
        removed = _delete_job_ids_via_json(pending, store_path=path, removed=removed)
    return removed


def remove_agent_scheduler_jobs(agent_id: str, *, store_path: Path | None = None) -> dict[str, bool]:
    """Remove autonomous agent cron jobs ({id}-watch/research/quant/infra-heal)."""
    job_ids = agent_scheduler_job_ids(agent_id)
    removed = _delete_job_ids_from_store(job_ids, store_path=store_path)
    if any(removed.values()):
        logger.info("removed agent scheduler jobs for %s: %s", agent_id, removed)
    return removed


def _default_store_path() -> Path:
    from trade_integrations.execution.connector_context import runtime_root

    return runtime_root() / "scheduled_research" / "scheduled_research_jobs.json"


def remove_obsolete_scheduler_jobs(*, store_path: Path | None = None) -> dict[str, bool]:
    """Delete pre-migration standalone cron jobs from the Vibe scheduled-research JSON store."""
    removed = _delete_job_ids_from_store(OBSOLETE_SCHEDULER_JOB_IDS, store_path=store_path)
    if any(removed.values()):
        logger.info("removed obsolete scheduler jobs: %s", removed)
    return removed
