"""Unified hub calibration and maintenance orchestrator (Phase 11)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


def run_morning_hub_calibration(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Morning pipeline: reconcile ledgers → export fills → retrain → manifest."""
    from trade_integrations.env import load_trade_env

    load_trade_env()
    cfg = config or {}
    dry_run = bool(cfg.get("dry_run"))
    summary: dict[str, Any] = {
        "phase": "morning",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": dry_run,
        "steps": {},
    }

    if dry_run:
        summary["status"] = "dry_run"
        summary["steps"] = {
            "options_reconcile": "skipped",
            "index_calibration": "skipped",
            "fills_export": "skipped",
            "manifest": "skipped",
        }
        return summary

    try:
        from trade_integrations.dataflows.options_research.prediction_ledger import (
            reconcile_options_predictions,
        )

        reconciled = reconcile_options_predictions()
        summary["steps"]["options_reconcile"] = {"reconciled_rows": reconciled}
    except Exception as exc:
        logger.exception("options reconcile failed")
        summary["steps"]["options_reconcile"] = {"status": "error", "error": str(exc)}

    try:
        from trade_integrations.dataflows.index_research.calibration_runner import run_calibration

        index_summary = run_calibration(
            horizon_days=cfg.get("horizon_days"),
            force_retrain=bool(cfg.get("force_retrain")),
            skip_retrain=bool(cfg.get("skip_retrain")),
            backfill_history=not bool(cfg.get("skip_backfill")),
        )
        summary["steps"]["index_calibration"] = index_summary
    except Exception as exc:
        logger.exception("index calibration failed")
        summary["steps"]["index_calibration"] = {"status": "error", "error": str(exc)}

    try:
        from trade_integrations.hub_storage.openalgo_fills_export import export_openalgo_fills

        summary["steps"]["fills_export"] = export_openalgo_fills()
    except Exception as exc:
        logger.exception("fills export failed")
        summary["steps"]["fills_export"] = {"status": "error", "error": str(exc)}

    try:
        from trade_integrations.auto_paper.outcome_ledger import (
            compute_execution_calibration_metrics,
            compute_paper_calibration_metrics,
        )

        summary["steps"]["strategy_calibration"] = {
            "paper": compute_paper_calibration_metrics(),
            "execution": compute_execution_calibration_metrics(),
        }
    except Exception as exc:
        logger.exception("strategy calibration metrics failed")
        summary["steps"]["strategy_calibration"] = {"status": "error", "error": str(exc)}

    try:
        from trade_integrations.hub_analytics.manifest import write_hub_manifest

        summary["steps"]["manifest"] = write_hub_manifest(sync_executions=True)
    except Exception as exc:
        logger.exception("manifest write failed")
        summary["steps"]["manifest"] = {"status": "error", "error": str(exc)}

    summary["status"] = "ok"
    summary["finished_at"] = datetime.now(timezone.utc).isoformat()
    return summary


def run_evening_hub_maintenance(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Evening pipeline: archive company + options/stock research → refresh manifest."""
    from trade_integrations.env import load_trade_env

    load_trade_env()
    cfg = config or {}
    dry_run = bool(cfg.get("dry_run"))
    as_of_date = cfg.get("as_of_date") or datetime.now(timezone.utc).date().isoformat()
    summary: dict[str, Any] = {
        "phase": "evening",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": dry_run,
        "as_of_date": as_of_date,
        "steps": {},
    }

    if dry_run:
        summary["status"] = "dry_run"
        return summary

    try:
        from trade_integrations.context.hub import archive_company_research_snapshots

        summary["steps"]["company_archive"] = archive_company_research_snapshots(as_of_date=as_of_date)
    except Exception as exc:
        logger.exception("company archive failed")
        summary["steps"]["company_archive"] = {"status": "error", "error": str(exc)}

    try:
        from trade_integrations.context.hub import archive_options_stock_snapshots

        summary["steps"]["options_stock_archive"] = archive_options_stock_snapshots(as_of_date=as_of_date)
    except Exception as exc:
        logger.exception("options/stock archive failed")
        summary["steps"]["options_stock_archive"] = {"status": "error", "error": str(exc)}

    summary["steps"]["market_intelligence"] = _run_market_intelligence_step(as_of_date)
    summary["steps"]["timescale_export"] = _run_timescale_export_step()
    summary["steps"]["news_impact_reconcile"] = _run_news_impact_reconcile_step()
    summary["steps"]["capture_rollup"] = _run_capture_rollup_step(as_of_date)

    try:
        from trade_integrations.hub_analytics.manifest import write_hub_manifest

        summary["steps"]["manifest"] = write_hub_manifest(sync_executions=True)
    except Exception as exc:
        logger.exception("manifest write failed")
        summary["steps"]["manifest"] = {"status": "error", "error": str(exc)}

    summary["status"] = "ok"
    summary["finished_at"] = datetime.now(timezone.utc).isoformat()
    return summary


def _run_timescale_export_step() -> dict[str, Any]:
    try:
        from trade_integrations.hub_storage.timescale_ticks import export_and_prune_hot_ticks

        return export_and_prune_hot_ticks()
    except Exception as exc:
        logger.exception("timescale export failed")
        return {"status": "error", "error": str(exc)}


def _run_market_intelligence_step(as_of_date: str) -> dict[str, Any]:
    try:
        from trade_integrations.hub_storage.market_intelligence_archive import archive_market_intelligence

        return archive_market_intelligence(as_of_date=as_of_date)
    except Exception as exc:
        logger.exception("market intelligence archive failed")
        return {"status": "error", "error": str(exc)}


def _run_news_impact_reconcile_step() -> dict[str, Any]:
    try:
        from trade_integrations.dataflows.index_research.news_impact_engine import reconcile_matured_impacts
        from trade_integrations.dataflows.index_research.news_shock_calibration import update_shock_calibration
        from trade_integrations.dataflows.index_research.news_event_features import evaluate_news_model_gates

        reconcile = reconcile_matured_impacts(ticker="NIFTY")
        shock = update_shock_calibration(ticker="NIFTY")
        gates = evaluate_news_model_gates(ticker="NIFTY")
        return {"reconcile": reconcile, "shock_calibration": shock, "model_gates": gates}
    except Exception as exc:
        logger.exception("news impact reconcile failed")
        return {"status": "error", "error": str(exc)}


def _run_capture_rollup_step(as_of_date: str) -> dict[str, Any]:
    try:
        from trade_integrations.hub_capture.rollup import run_capture_rollup

        return run_capture_rollup(as_of_date=as_of_date)
    except Exception as exc:
        logger.exception("capture rollup failed")
        return {"status": "error", "error": str(exc)}
