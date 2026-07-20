"""Hub news pipeline schedule and ingest configuration.

Defaults come from ``.env``; runtime overrides persist in
``reports/hub/_data/news_pipeline/config.json`` (editable from Hub UI).
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from trade_integrations.context.hub import get_hub_dir

_CONFIG_REL = Path("_data") / "news_pipeline" / "config.json"

_FULL_SOURCES_DEFAULT = "all"
_LIGHT_SOURCES_DEFAULT = "rss,watcher"

_JOB_ID_FULL = "nifty-hub-news-ingest-full"
_JOB_ID_LIGHT = "nifty-hub-news-ingest-light"
_JOB_ID_ENTITY = "nifty-hub-news-entity"
_JOB_ID_ENTITY_CONTINUOUS = "nifty-hub-news-entity-drain-continuous"
_JOB_ID_ENTITY_MAINTENANCE = "nifty-hub-news-entity-maintenance"


def _config_path() -> Path:
    path = get_hub_dir() / _CONFIG_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "1" if default else "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)).strip())
    except ValueError:
        return default


@dataclass
class NewsPipelineConfig:
    """Merged hub news ingest + distillation schedule."""

    ticker: str = "NIFTY"
    full_ingest_cron: str = "0 7 * * *"
    light_ingest_cron: str = "0 */4 * * *"
    light_ingest_enabled: bool = True
    entity_drain_cron: str = "35 18 * * *"
    entity_drain_continuous_cron: str = "*/15 * * * *"
    entity_drain_continuous_enabled: bool = True
    entity_maintenance_cron: str = "0 3 * * 0"
    entity_backpressure_threshold: int = 400
    full_ingest_sources: str = _FULL_SOURCES_DEFAULT
    light_ingest_sources: str = _LIGHT_SOURCES_DEFAULT
    full_lookback_days: int = 3
    light_lookback_days: int = 1
    entity_batch_size: int = 200
    cluster_threshold: float = 0.80
    relevance_gate_enabled: bool = True
    relevance_min_confidence: float = 0.60
    relevance_rule_first: bool = True
    discard_retention_days: int = 30

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> NewsPipelineConfig:
        base = cls()
        if not isinstance(data, dict):
            return base
        for key in base.to_dict():
            if key in data and data[key] is not None:
                setattr(base, key, data[key])
        base.ticker = str(base.ticker or "NIFTY").strip().upper()
        base.full_ingest_sources = str(base.full_ingest_sources or _FULL_SOURCES_DEFAULT)
        base.light_ingest_sources = str(base.light_ingest_sources or _LIGHT_SOURCES_DEFAULT)
        try:
            base.cluster_threshold = float(base.cluster_threshold)
        except (TypeError, ValueError):
            base.cluster_threshold = 0.80
        try:
            base.relevance_min_confidence = float(base.relevance_min_confidence)
        except (TypeError, ValueError):
            base.relevance_min_confidence = 0.60
        try:
            base.discard_retention_days = int(base.discard_retention_days)
        except (TypeError, ValueError):
            base.discard_retention_days = 30
        try:
            base.entity_backpressure_threshold = int(base.entity_backpressure_threshold)
        except (TypeError, ValueError):
            base.entity_backpressure_threshold = 400
        return base


def env_defaults() -> NewsPipelineConfig:
    return NewsPipelineConfig(
        ticker=os.getenv("HUB_NEWS_DEFAULT_TICKER", "NIFTY").strip().upper() or "NIFTY",
        full_ingest_cron=os.getenv("HUB_NEWS_FULL_INGEST_CRON", "0 7 * * *").strip(),
        light_ingest_cron=os.getenv(
            "HUB_NEWS_LIGHT_INGEST_CRON",
            os.getenv("HUB_NEWS_INGEST_CRON", "0 */4 * * *"),
        ).strip(),
        light_ingest_enabled=_env_bool("HUB_NEWS_LIGHT_INGEST_ENABLED", True),
        entity_drain_cron=os.getenv("HUB_NEWS_ENTITY_CRON", "35 18 * * *").strip(),
        entity_drain_continuous_cron=os.getenv("HUB_NEWS_ENTITY_CONTINUOUS_CRON", "*/15 * * * *").strip(),
        entity_drain_continuous_enabled=_env_bool("HUB_NEWS_ENTITY_CONTINUOUS_ENABLED", True),
        entity_maintenance_cron=os.getenv("HUB_NEWS_ENTITY_MAINTENANCE_CRON", "0 3 * * 0").strip(),
        entity_backpressure_threshold=_env_int("HUB_NEWS_BACKPRESSURE_THRESHOLD", 400),
        full_ingest_sources=os.getenv("HUB_NEWS_FULL_SOURCES", _FULL_SOURCES_DEFAULT).strip(),
        light_ingest_sources=os.getenv("HUB_NEWS_LIGHT_SOURCES", _LIGHT_SOURCES_DEFAULT).strip(),
        full_lookback_days=_env_int("HUB_NEWS_FULL_LOOKBACK_DAYS", _env_int("HUB_NEWS_INGEST_LOOKBACK_DAYS", 3)),
        light_lookback_days=_env_int("HUB_NEWS_LIGHT_LOOKBACK_DAYS", 1),
        entity_batch_size=_env_int("HUB_NEWS_ENTITY_BATCH_SIZE", 200),
        cluster_threshold=float(os.getenv("HUB_NEWS_EMBED_CLUSTER_THRESHOLD", "0.80")),
        relevance_gate_enabled=_env_bool("HUB_NEWS_RELEVANCE_GATE", True),
        relevance_min_confidence=float(os.getenv("HUB_NEWS_RELEVANCE_MIN_CONFIDENCE", "0.60")),
        relevance_rule_first=_env_bool("HUB_NEWS_RELEVANCE_RULE_FIRST", True),
        discard_retention_days=_env_int("HUB_NEWS_DISCARD_RETENTION_DAYS", 30),
    )


def load_news_pipeline_config() -> NewsPipelineConfig:
    """Env defaults merged with persisted hub override file."""
    merged = env_defaults()
    path = _config_path()
    if not path.is_file():
        return merged
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        override = NewsPipelineConfig.from_dict(raw if isinstance(raw, dict) else {})
        for key, value in override.to_dict().items():
            if key in raw:
                setattr(merged, key, value)
    except (json.JSONDecodeError, OSError):
        pass
    return merged


def save_news_pipeline_config(config: NewsPipelineConfig) -> NewsPipelineConfig:
    path = _config_path()
    payload = config.to_dict()
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return config


def update_news_pipeline_config(patch: dict[str, Any]) -> NewsPipelineConfig:
    current = load_news_pipeline_config()
    data = current.to_dict()
    allowed = set(data.keys())
    for key, value in patch.items():
        if key in allowed and value is not None:
            data[key] = value
    updated = NewsPipelineConfig.from_dict(data)
    save_news_pipeline_config(updated)
    return updated


def config_for_api() -> dict[str, Any]:
    """Config blob for Hub UI including env source hints."""
    cfg = load_news_pipeline_config()
    return {
        **cfg.to_dict(),
        "job_ids": {
            "full_ingest": _JOB_ID_FULL,
            "light_ingest": _JOB_ID_LIGHT,
            "entity_drain": _JOB_ID_ENTITY,
            "entity_drain_continuous": _JOB_ID_ENTITY_CONTINUOUS,
            "entity_maintenance": _JOB_ID_ENTITY_MAINTENANCE,
        },
        "ingest_modes": {
            "full": {
                "label": "Full ingest (daily)",
                "sources": cfg.full_ingest_sources,
                "lookback_days": cfg.full_lookback_days,
                "cron": cfg.full_ingest_cron,
            },
            "light": {
                "label": "Light ingest (periodic)",
                "sources": cfg.light_ingest_sources,
                "lookback_days": cfg.light_lookback_days,
                "cron": cfg.light_ingest_cron,
                "enabled": cfg.light_ingest_enabled,
            },
        },
        "config_path": str(_config_path()),
    }


def sync_scheduled_jobs_from_config() -> dict[str, Any]:
    """Apply cron schedules to vibe scheduled-research job store."""
    cfg = load_news_pipeline_config()
    try:
        from src.scheduled_research.models import validate_schedule
        from src.scheduled_research.store import ScheduledResearchJobStore
        from src.scheduled_research.index_jobs import (
            JOB_TYPE_HUB_NEWS_ENTITY,
            JOB_TYPE_HUB_NEWS_INGEST,
        )
        from src.scheduled_research.models import ScheduledResearchJob, JobStatus
        import time
    except ImportError:
        return {"synced": False, "reason": "scheduler not available outside vibe agent"}

    validate_schedule(cfg.full_ingest_cron)
    validate_schedule(cfg.light_ingest_cron)
    validate_schedule(cfg.entity_drain_cron)
    validate_schedule(cfg.entity_maintenance_cron)
    if cfg.entity_drain_continuous_enabled:
        validate_schedule(cfg.entity_drain_continuous_cron)

    store = ScheduledResearchJobStore()
    jobs = store.load()
    now_ms = int(time.time() * 1000)

    def upsert(job_id: str, *, schedule: str, prompt: str, config: dict[str, Any]) -> None:
        existing = jobs.get(job_id)
        if existing:
            existing.schedule = schedule
            existing.config = config
            store.upsert(existing)
            return
        store.upsert(
            ScheduledResearchJob(
                id=job_id,
                prompt=prompt,
                schedule=schedule,
                next_run_at=now_ms,
                status=JobStatus.PENDING,
                created_at=now_ms,
                config=config,
            )
        )

    upsert(
        _JOB_ID_FULL,
        schedule=cfg.full_ingest_cron,
        prompt="Full hub news ingest (all sources)",
        config={
            "job_type": JOB_TYPE_HUB_NEWS_INGEST,
            "mode": "full",
            "ticker": cfg.ticker,
            "sources": cfg.full_ingest_sources,
            "lookback_days": cfg.full_lookback_days,
        },
    )

    if cfg.light_ingest_enabled:
        upsert(
            _JOB_ID_LIGHT,
            schedule=cfg.light_ingest_cron,
            prompt="Light hub news ingest (RSS + watcher)",
            config={
                "job_type": JOB_TYPE_HUB_NEWS_INGEST,
                "mode": "light",
                "ticker": cfg.ticker,
                "sources": cfg.light_ingest_sources,
                "lookback_days": cfg.light_lookback_days,
            },
        )
    elif _JOB_ID_LIGHT in jobs:
        store.delete(_JOB_ID_LIGHT)

    upsert(
        _JOB_ID_ENTITY,
        schedule=cfg.entity_drain_cron,
        prompt="Drain staging into distilled hub events",
        config={
            "job_type": JOB_TYPE_HUB_NEWS_ENTITY,
            "mode": "drain",
            "ticker": cfg.ticker,
            "batch_size": cfg.entity_batch_size,
        },
    )

    if cfg.entity_drain_continuous_enabled:
        upsert(
            _JOB_ID_ENTITY_CONTINUOUS,
            schedule=cfg.entity_drain_continuous_cron,
            prompt="Continuous staging drain (adaptive batch)",
            config={
                "job_type": JOB_TYPE_HUB_NEWS_ENTITY,
                "mode": "drain",
                "ticker": cfg.ticker,
                "batch_size": "adaptive",
                "adaptive_batch": True,
                "run_wiki_rescan": True,
            },
        )
    elif _JOB_ID_ENTITY_CONTINUOUS in jobs:
        store.delete(_JOB_ID_ENTITY_CONTINUOUS)

    upsert(
        _JOB_ID_ENTITY_MAINTENANCE,
        schedule=cfg.entity_maintenance_cron,
        prompt="Heavy hub news maintenance (repair, backfill, compact)",
        config={
            "job_type": JOB_TYPE_HUB_NEWS_ENTITY,
            "mode": "maintenance",
            "ticker": cfg.ticker,
            "batch_size": cfg.entity_batch_size,
            "lookback_days": 365,
        },
    )

    # Retire legacy single ingest job if present
    if "nifty-hub-news-ingest" in jobs:
        store.delete("nifty-hub-news-ingest")

    return {
        "synced": True,
        "full_cron": cfg.full_ingest_cron,
        "light_cron": cfg.light_ingest_cron if cfg.light_ingest_enabled else None,
        "entity_cron": cfg.entity_drain_cron,
        "entity_continuous_cron": cfg.entity_drain_continuous_cron
        if cfg.entity_drain_continuous_enabled
        else None,
        "entity_maintenance_cron": cfg.entity_maintenance_cron,
        "entity_backpressure_threshold": cfg.entity_backpressure_threshold,
    }
