"""Public entry point for the opt-in options plan monitor."""

from __future__ import annotations

from trade_integrations.context.hub import load_options_research_json
from trade_integrations.monitor.config import is_monitor_enabled
from trade_integrations.monitor.live_quotes import fetch_underlying_ltp
from trade_integrations.monitor.plan_staleness import StalenessReport, evaluate_plan_staleness


class MonitorService:
    """Evaluate cached options research plans against live market data."""

    @staticmethod
    def is_enabled() -> bool:
        return is_monitor_enabled()

    def evaluate_ticker(self, ticker: str) -> StalenessReport | None:
        """Load hub doc, fetch live spot, and score staleness."""
        if not is_monitor_enabled():
            return None

        doc = load_options_research_json(ticker)
        if doc is None:
            return StalenessReport(
                ticker=ticker.upper(),
                status="broken",
                as_of=None,
                live_spot=None,
                plan_spot=None,
                spot_drift_pct=None,
                age_minutes=None,
                reasons=["missing_hub_doc"],
                suggested_action="refresh",
            )

        live_spot = fetch_underlying_ltp(ticker)
        return evaluate_plan_staleness(doc, live_spot=live_spot)

    def evaluate_doc(self, doc) -> StalenessReport:
        """Score a research doc without loading from hub."""
        ticker = getattr(doc, "underlying", None) or (
            doc.get("underlying") if isinstance(doc, dict) else ""
        )
        live_spot = None
        if is_monitor_enabled() and ticker:
            live_spot = fetch_underlying_ltp(str(ticker))
        return evaluate_plan_staleness(doc, live_spot=live_spot)
