"""Bridge PipelineLogger entries into Tier 0 observability."""

from __future__ import annotations

from typing import Any

from trade_integrations.dataflows.index_research.pipeline_log import PipelineLogEntry
from trade_integrations.observability.emitter import emit


def pipeline_log_to_observability(entry: PipelineLogEntry) -> None:
    level = entry.level if entry.level in {"info", "warn", "error"} else "info"
    detail: dict[str, Any] = dict(entry.detail or {})
    emit(
        "pipeline",
        entry.stage,
        level=level,  # type: ignore[arg-type]
        detail={"message": entry.message, **detail},
    )


def make_pipeline_observability_callback():
    """Return a PipelineLogger on_entry callback."""
    return pipeline_log_to_observability
