"""Index prediction ledger reconciliation and model retrain orchestration."""

from __future__ import annotations

from typing import Any

from trade_integrations.context.hub import load_index_research_json, save_index_research
from trade_integrations.dataflows.index_research.calibrator import retrain, should_retrain
from trade_integrations.dataflows.index_research.prediction_ledger import (
    compute_accuracy_metrics,
    reconcile_predictions,
)


def run_calibration(
    *,
    horizon_days: int | None = None,
    skip_retrain: bool = False,
) -> dict[str, Any]:
    """Reconcile ledger, score accuracy, retrain on drift, update hub artifact."""
    reconciled = reconcile_predictions()
    accuracy = compute_accuracy_metrics()
    retrained = False
    artifact = None

    if not skip_retrain and should_retrain(accuracy.get("mae_14d_pct")):
        artifact = retrain(horizon_days=horizon_days)
        retrained = artifact is not None

    doc = load_index_research_json("NIFTY")
    if doc is not None:
        doc.accuracy = {**accuracy, "retrained": retrained}
        save_index_research(doc)

    return {
        "reconciled_rows": reconciled,
        "accuracy": accuracy,
        "retrained": retrained,
        "model_mae": artifact.mae if artifact else None,
    }
