"""Remove auto-paper cron jobs from Vibe scheduler store (~/.vibe-trading)."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

AUTO_PAPER_AGENT_JOB_ID = "auto-paper-agent-turn"
AUTO_PAPER_INTRADAY_JOB_ID = "auto-paper-intraday"
AUTO_PAPER_THESIS_BREAK_JOB_ID = "auto-paper-thesis-break"
AUTO_PAPER_SCHEDULER_JOB_IDS = frozenset(
    {
        AUTO_PAPER_AGENT_JOB_ID,
        AUTO_PAPER_INTRADAY_JOB_ID,
        AUTO_PAPER_THESIS_BREAK_JOB_ID,
    }
)


def _default_store_path() -> Path:
    root = Path(os.getenv("VIBE_TRADING_RUNTIME_ROOT", Path.home() / ".vibe-trading"))
    return root / "scheduled_research" / "scheduled_research_jobs.json"


def remove_auto_paper_scheduler_jobs(*, store_path: Path | None = None) -> dict[str, bool]:
    """Delete paper-trading jobs from the Vibe scheduled-research JSON store."""
    try:
        from src.scheduled_research.auto_paper_jobs import unregister_auto_paper_scheduler_jobs

        return unregister_auto_paper_scheduler_jobs()
    except ImportError:
        pass
    except Exception:
        logger.debug("vibetrading unregister failed; falling back to file edit", exc_info=True)

    path = store_path or _default_store_path()
    if not path.is_file():
        return {job_id: False for job_id in AUTO_PAPER_SCHEDULER_JOB_IDS}

    try:
        envelope = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("could not read scheduler store %s: %s", path, exc)
        return {job_id: False for job_id in AUTO_PAPER_SCHEDULER_JOB_IDS}

    jobs = envelope.get("jobs")
    if not isinstance(jobs, list):
        return {job_id: False for job_id in AUTO_PAPER_SCHEDULER_JOB_IDS}

    before_ids = {str(j.get("id")) for j in jobs if isinstance(j, dict)}
    filtered = [
        j for j in jobs if isinstance(j, dict) and str(j.get("id")) not in AUTO_PAPER_SCHEDULER_JOB_IDS
    ]
    removed = {job_id: job_id in before_ids for job_id in AUTO_PAPER_SCHEDULER_JOB_IDS}

    if len(filtered) == len(jobs):
        return {job_id: False for job_id in AUTO_PAPER_SCHEDULER_JOB_IDS}

    envelope["jobs"] = filtered
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    payload = json.dumps(envelope, ensure_ascii=False, indent=2)
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)
    logger.info("removed auto paper scheduler jobs from %s: %s", path, removed)
    return removed
