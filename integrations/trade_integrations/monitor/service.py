"""Public entry point for the opt-in options plan monitor."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from trade_integrations.context.hub import load_options_research_json, save_options_research
from trade_integrations.dataflows.options_research.aggregator import run_options_research
from trade_integrations.monitor.config import is_monitor_enabled
from trade_integrations.monitor.execution_ledger import (
    fetch_position_book,
    get_ledger_entry,
    match_positions_for_entry,
)
from trade_integrations.monitor.live_quotes import fetch_underlying_ltp
from trade_integrations.monitor.news_watcher import check_material_news
from trade_integrations.monitor.plan_staleness import StalenessReport, evaluate_plan_staleness
from trade_integrations.monitor.thesis_break import ThesisBreakReport, evaluate_thesis_break


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

    def evaluate_position_thesis(self, widget_id: str) -> ThesisBreakReport | None:
        """Evaluate thesis break for an executed trade-plan widget."""
        if not is_monitor_enabled():
            return None

        ledger_entry = get_ledger_entry(widget_id)
        if ledger_entry is None:
            return None

        underlying = str(ledger_entry.get("underlying") or "").strip().upper()
        if not underlying:
            return None

        doc = load_options_research_json(underlying)
        live_spot = fetch_underlying_ltp(underlying)
        position_pnl = self._position_pnl_for_entry(ledger_entry)
        return evaluate_thesis_break(
            doc,
            ledger_entry,
            live_spot=live_spot,
            position_pnl=position_pnl,
        )

    @staticmethod
    def _position_pnl_for_entry(ledger_entry: dict) -> float | None:
        position_book = fetch_position_book()
        if position_book is None:
            return None
        _, position_pnl = match_positions_for_entry(ledger_entry, position_book)
        return position_pnl

    def check_news_and_maybe_refresh(self, ticker: str) -> bool:
        """Refresh hub research when material news or staleness warrants it."""
        if not is_monitor_enabled():
            return False

        symbol = ticker.strip().upper()
        if not symbol:
            return False

        since = self._news_since(symbol)
        if check_material_news(symbol, since):
            self._refresh(symbol)
            return True

        report = self.evaluate_ticker(symbol)
        if report is None:
            return False
        if report.suggested_action in {"refresh", "re_recommend"}:
            self._refresh(symbol)
            return True
        return False

    @staticmethod
    def _news_since(ticker: str) -> datetime:
        doc = load_options_research_json(ticker)
        if doc is None:
            return datetime.now(timezone.utc) - timedelta(days=1)

        as_of = getattr(doc, "as_of", None)
        if as_of is None and isinstance(doc, dict):
            as_of = doc.get("as_of")
        if isinstance(as_of, datetime):
            if as_of.tzinfo is None:
                return as_of.replace(tzinfo=timezone.utc)
            return as_of
        if isinstance(as_of, str):
            text = as_of.strip()
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            try:
                parsed = datetime.fromisoformat(text)
            except ValueError:
                parsed = None
            if parsed is not None:
                if parsed.tzinfo is None:
                    return parsed.replace(tzinfo=timezone.utc)
                return parsed
        return datetime.now(timezone.utc) - timedelta(days=1)

    @staticmethod
    def _refresh(ticker: str) -> None:
        doc = run_options_research(ticker)
        save_options_research(doc)
