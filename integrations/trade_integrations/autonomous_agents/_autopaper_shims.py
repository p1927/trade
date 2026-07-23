"""Deprecated auto_paper import paths during migration to autonomous_agents."""

from __future__ import annotations

AUTOPAPER_SUNSET_ISO = "2026-08-23"

DEPRECATED_AUTO_PAPER_IMPORTS: tuple[tuple[str, str], ...] = (
    ("trade_integrations.autonomous_agents.mandate", "trade_integrations.autonomous_agents.mandate"),
    ("trade_integrations.auto_paper.mandate_enforcer", "trade_integrations.autonomous_agents.mandate"),
    ("trade_integrations.auto_paper.lifecycle", "trade_integrations.autonomous_agents.lifecycle"),
    ("trade_integrations.auto_paper.outcome_ledger", "trade_integrations.autonomous_agents.outcome_ledger"),
    ("trade_integrations.autonomous_agents.strategy_rank", "trade_integrations.autonomous_agents.strategy_rank"),
    ("trade_integrations.auto_paper.reconcile", "trade_integrations.autonomous_agents.reconcile"),
    ("trade_integrations.auto_paper.audit", "trade_integrations.autonomous_agents.audit"),
)
