"""Opt-in realtime monitor for cached options research plans."""

from trade_integrations.monitor.config import is_monitor_enabled
from trade_integrations.monitor.news_watcher import (
    MaterialHeadline,
    check_material_news,
    headline_fingerprint,
)
from trade_integrations.monitor.plan_staleness import StalenessReport
from trade_integrations.monitor.service import MonitorService

__all__ = [
    "MaterialHeadline",
    "MonitorService",
    "StalenessReport",
    "check_material_news",
    "headline_fingerprint",
    "is_monitor_enabled",
]
