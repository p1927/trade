"""MinTrace forecast reconciliation — Hyndman-style coherence for hybrid tracks."""

from __future__ import annotations

from typing import Any

import numpy as np


def min_trace_reconcile(
    forecasts: dict[str, float],
    *,
    weights: dict[str, float] | None = None,
) -> tuple[float, dict[str, float]]:
    """
    Reconcile same-level forecasts with inverse-variance weights (MinTrace diagonal W).

    Returns (reconciled_scalar, normalized_weights).
    """
    if not forecasts:
        return 0.0, {}
    keys = list(forecasts.keys())
    values = np.array([float(forecasts[k]) for k in keys], dtype=float)
    if weights:
        w = np.array([max(1e-6, float(weights.get(k, 1.0))) for k in keys], dtype=float)
    else:
        w = np.ones(len(keys), dtype=float)
    w = w / w.sum()
    reconciled = float(np.dot(w, values))
    weight_map = {k: round(float(w_i), 4) for k, w_i in zip(keys, w, strict=False)}
    return reconciled, weight_map


def reconcile_hybrid_forecast(
    bottom_up_pct: float,
    macro_only_pct: float,
    *,
    bottom_up_mae: float = 1.5,
    macro_mae: float = 1.5,
) -> dict[str, Any]:
    """Two-level index reconciliation: bottom-up + macro top-down."""
    weights = {
        "bottom_up": 1.0 / max(0.01, bottom_up_mae),
        "macro_only": 1.0 / max(0.01, macro_mae),
    }
    reconciled, blend = min_trace_reconcile(
        {"bottom_up": bottom_up_pct, "macro_only": macro_only_pct},
        weights=weights,
    )
    return {
        "expected_return_pct": round(reconciled, 4),
        "reconciliation_weights": blend,
        "bottom_up_return_pct": round(bottom_up_pct, 4),
        "macro_only_return_pct": round(macro_only_pct, 4),
    }
