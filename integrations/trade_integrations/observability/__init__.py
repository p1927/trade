"""Tier 0 observability: structured events + agent issue registry."""

from trade_integrations.observability.context import observability_context, set_observability_context
from trade_integrations.observability.emitter import emit, emit_job_rollup, is_observability_enabled
from trade_integrations.observability.rollup import JobRollup

__all__ = [
    "JobRollup",
    "emit",
    "emit_job_rollup",
    "is_observability_enabled",
    "observability_context",
    "set_observability_context",
]
