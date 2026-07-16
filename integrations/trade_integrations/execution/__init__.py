"""Unified execution routing for IN/US, paper/live, equity/options."""

from trade_integrations.execution.profile import ExecutionProfile, resolve_profile
from trade_integrations.execution.prompt_fragments import prompt_fragment_for

__all__ = ["ExecutionProfile", "resolve_profile", "prompt_fragment_for"]
