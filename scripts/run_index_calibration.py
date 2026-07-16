#!/usr/bin/env python3
"""Nightly index calibration — reconcile ledger and retrain on drift."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tradingagents"))

import trade_integrations  # noqa: F401

from trade_integrations.context.hub import load_index_research_json, save_index_research
from trade_integrations.dataflows.index_research.calibrator import retrain, should_retrain
from trade_integrations.dataflows.index_research.prediction_ledger import (
    compute_accuracy_metrics,
    reconcile_predictions,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reconcile index prediction ledger and retrain model on drift",
    )
    parser.add_argument(
        "--horizon-days",
        type=int,
        default=None,
        help="Horizon profile for retrain (default env / 14)",
    )
    parser.add_argument(
        "--skip-retrain",
        action="store_true",
        help="Reconcile and score only; do not retrain",
    )
    args = parser.parse_args()

    reconciled = reconcile_predictions()
    accuracy = compute_accuracy_metrics()
    retrained = False
    artifact = None

    if not args.skip_retrain and should_retrain(accuracy.get("mae_14d_pct")):
        artifact = retrain(horizon_days=args.horizon_days)
        retrained = artifact is not None

    doc = load_index_research_json("NIFTY")
    if doc is not None:
        doc.accuracy = {**accuracy, "retrained": retrained}
        save_index_research(doc)

    summary = {
        "reconciled_rows": reconciled,
        "accuracy": accuracy,
        "retrained": retrained,
        "model_mae": artifact.mae if artifact else None,
    }
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
