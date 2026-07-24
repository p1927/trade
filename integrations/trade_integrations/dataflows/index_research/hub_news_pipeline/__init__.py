"""Modular hub news enrichment pipeline — one file per step."""

from trade_integrations.dataflows.index_research.hub_news_pipeline.pipeline_context import (
    RefPipelineContext,
    StepResult,
)
from trade_integrations.dataflows.index_research.hub_news_pipeline.pipeline_runner import (
    run_ref_pipeline,
    run_step,
)

__all__ = [
    "RefPipelineContext",
    "StepResult",
    "run_ref_pipeline",
    "run_step",
]
