"""Opt-in realtime monitor for cached options research plans."""

from trade_integrations.monitor.config import is_monitor_enabled
from trade_integrations.monitor.execution_ledger import (
    fetch_position_book,
    get_ledger_entry,
    has_open_position_for_underlying,
    list_open_by_underlying,
    list_open_entries,
    load_ledger,
    match_positions_for_entry,
    record_execution,
    record_execution_from_widget,
)
from trade_integrations.monitor.news_watcher import (
    MaterialHeadline,
    check_material_news,
    headline_fingerprint,
)
from trade_integrations.monitor.plan_staleness import StalenessReport
from trade_integrations.monitor.service import MonitorService
from trade_integrations.monitor.thesis_break import ThesisBreakReport, evaluate_thesis_break

__all__ = [
    "MaterialHeadline",
    "MonitorService",
    "StalenessReport",
    "ThesisBreakReport",
    "check_material_news",
    "evaluate_thesis_break",
    "fetch_position_book",
    "get_ledger_entry",
    "has_open_position_for_underlying",
    "headline_fingerprint",
    "is_monitor_enabled",
    "list_open_by_underlying",
    "list_open_entries",
    "load_ledger",
    "match_positions_for_entry",
    "record_execution",
    "record_execution_from_widget",
]
