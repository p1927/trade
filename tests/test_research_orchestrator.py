"""Tests for research orchestrator."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from trade_integrations.dataflows.stock_research.models import StockResearchDoc
from trade_integrations.research.orchestrator import ensure_research_complete
from trade_integrations.research.registry import ResearchKind


def _minimal_stock_doc() -> StockResearchDoc:
    return StockResearchDoc(
        ticker="RELIANCE",
        as_of=datetime.now(timezone.utc),
        lookahead_days=14,
        spot=1296.0,
        prediction={
            "view": "neutral",
            "range": {"low": 1280, "high": 1310},
            "provenance": {"direction": "quant", "range": "quant"},
        },
        recommended={"max_profit": 65.0, "max_loss": 39.0},
        charges={"round_trip_charges": 1.4, "net_debit_credit": -1296},
    )


@pytest.mark.unit
class TestResearchOrchestrator:
    @patch("trade_integrations.research.orchestrator._debate_stage_status", return_value=(True, False))
    @patch("trade_integrations.research.orchestrator._save_hub_doc")
    @patch("trade_integrations.research.orchestrator._run_batch_pipeline")
    @patch("trade_integrations.research.orchestrator._is_hub_fresh", return_value=False)
    def test_stock_run_saves_and_validates(self, _fresh, run_batch, _save, _debate):
        run_batch.return_value = _minimal_stock_doc()
        result = ensure_research_complete(
            "RELIANCE",
            kind=ResearchKind.STOCK,
            refresh=True,
            require_debate=False,
        )
        assert result.status == "complete"
        assert result.doc is not None
        assert "stock_research:run" in result.stages_run
        _save.assert_called_once()

    @patch("trade_integrations.research.orchestrator._debate_stage_status", return_value=(False, True))
    @patch("trade_integrations.research.orchestrator._save_hub_doc")
    @patch("trade_integrations.research.orchestrator._run_batch_pipeline")
    @patch("trade_integrations.research.orchestrator._is_hub_fresh", return_value=False)
    def test_stock_debate_pending_partial(self, _fresh, run_batch, _save, _debate):
        run_batch.return_value = _minimal_stock_doc()
        result = ensure_research_complete("RELIANCE", kind=ResearchKind.STOCK, refresh=True)
        assert result.debate_pending is True
        assert result.status == "partial"

    @patch("trade_integrations.research.orchestrator._debate_stage_status", return_value=(True, False))
    @patch("trade_integrations.research.orchestrator._run_batch_pipeline")
    @patch("trade_integrations.research.orchestrator._load_hub_doc")
    @patch("trade_integrations.research.orchestrator._is_hub_fresh", return_value=True)
    def test_uses_cache_when_fresh(self, _fresh, load_doc, run_batch, _debate):
        load_doc.return_value = _minimal_stock_doc()
        result = ensure_research_complete(
            "RELIANCE",
            kind=ResearchKind.STOCK,
            refresh=False,
            require_debate=False,
        )
        run_batch.assert_not_called()
        assert "stock_research:cache" in result.stages_run

    def test_ineligible_ticker(self):
        result = ensure_research_complete("", kind=ResearchKind.STOCK)
        assert result.status == "incomplete"
        assert result.error
