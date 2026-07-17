"""Index prediction ledger reconciliation and model retrain orchestration."""

from __future__ import annotations

from typing import Any

from trade_integrations.context.hub import load_index_research_json, save_index_research
from trade_integrations.dataflows.index_research.calibrator import retrain, should_retrain
from trade_integrations.dataflows.index_research.predictor import load_stored_model_artifact
from trade_integrations.dataflows.index_research.prediction_ledger import (
    compute_accuracy_metrics,
    reconcile_predictions,
)


def run_calibration(
    *,
    horizon_days: int | None = None,
    skip_retrain: bool = False,
    force_retrain: bool = False,
    backfill_history: bool = True,
) -> dict[str, Any]:
    """Reconcile ledger, score accuracy, retrain on drift, update hub artifact."""
    backfill_summary: dict[str, Any] | None = None
    if backfill_history:
        from trade_integrations.dataflows.index_research.factor_backfill import backfill_if_needed

        backfill_summary = backfill_if_needed()

    news_pipeline: dict[str, Any] = {}
    try:
        from trade_integrations.dataflows.index_research.news_event_features import (
            backfill_news_event_features,
            evaluate_news_model_gates,
        )
        from trade_integrations.dataflows.index_research.news_impact_engine import (
            reconcile_matured_impacts,
        )
        from trade_integrations.dataflows.index_research.news_shock_calibration import (
            update_shock_calibration,
        )

        news_pipeline["backfill"] = backfill_news_event_features(ticker="NIFTY")
        news_pipeline["reconcile"] = reconcile_matured_impacts(ticker="NIFTY")
        news_pipeline["shock_calibration"] = update_shock_calibration(ticker="NIFTY")
        news_pipeline["model_gates"] = evaluate_news_model_gates(ticker="NIFTY")
    except Exception as exc:
        news_pipeline = {"status": "error", "error": str(exc)}

    reconciled = reconcile_predictions()
    accuracy = compute_accuracy_metrics()
    retrained = False
    artifact = None

    stored = load_stored_model_artifact()
    needs_initial = stored is None or not stored.coefficients
    if not skip_retrain and (
        force_retrain
        or needs_initial
        or should_retrain(accuracy.get("mae_14d_pct"))
    ):
        artifact = retrain(horizon_days=horizon_days)
        retrained = artifact is not None

    doc = load_index_research_json("NIFTY")
    cascade_summary: dict[str, Any] | None = None
    if doc is not None:
        doc.accuracy = {**accuracy, "retrained": retrained}
        try:
            from trade_integrations.dataflows.index_research.cascade.calibrator import (
                run_cascade_calibration,
            )

            vix = None
            regime = doc.regime or {}
            if isinstance(regime, dict):
                vix = regime.get("india_vix")
            for row in doc.global_factors or []:
                if row.get("factor") == "india_vix" and vix is None:
                    vix = row.get("value")
            cal = run_cascade_calibration(
                ticker="NIFTY",
                india_vix=float(vix) if vix is not None else None,
            )
            doc.cascade_calibration = cal.to_dict()
            cascade_summary = {
                "status": cal.status,
                "as_of": cal.as_of,
                "regime": cal.regime,
                "primaries": len(cal.rules),
            }
        except Exception as exc:
            cascade_summary = {"status": "error", "message": str(exc)}

        try:
            from trade_integrations.dataflows.index_research.event_overlay import overlay_summary_for_ui

            doc.news_shock_calibration = overlay_summary_for_ui("NIFTY")
        except Exception:
            pass

        save_index_research(doc)

    return {
        "reconciled_rows": reconciled,
        "accuracy": accuracy,
        "retrained": retrained,
        "model_mae": artifact.mae if artifact else None,
        "backfill": backfill_summary,
        "model_coefficients": len(artifact.coefficients) if artifact else 0,
        "cascade_calibration": cascade_summary,
        "news_pipeline": news_pipeline,
    }
