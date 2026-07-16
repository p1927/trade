"""Offline cascade calibration job — estimates VAR IRFs and blends with heuristics."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from trade_integrations.dataflows.index_research.cascade.blender import blend_all_rules
from trade_integrations.dataflows.index_research.cascade.calibration_store import (
    save_cascade_calibration,
)
from trade_integrations.dataflows.index_research.cascade.constants import (
    DEFAULT_BLEND_ALPHA,
    DEFAULT_VAR_WINDOW_DAYS,
    VAR_FACTOR_KEYS,
)
from trade_integrations.dataflows.index_research.cascade.irf_converter import (
    var_rules_from_fit,
    var_rules_to_serializable,
)
from trade_integrations.dataflows.index_research.cascade.regime_scaler import (
    classify_cascade_regime,
)
from trade_integrations.dataflows.index_research.cascade.types import CascadeCalibration
from trade_integrations.dataflows.index_research.cascade.var_estimator import (
    fit_var1,
    prepare_var_matrix,
)
from trade_integrations.dataflows.index_research.sources.history_loader import (
    load_aligned_factor_history,
)

logger = logging.getLogger(__name__)


def run_cascade_calibration(
    *,
    ticker: str = "NIFTY",
    window_days: int = DEFAULT_VAR_WINDOW_DAYS,
    blend_alpha: float = DEFAULT_BLEND_ALPHA,
    india_vix: float | None = None,
) -> CascadeCalibration:
    """Estimate rolling VAR spillovers and persist blended cascade rules."""
    as_of = datetime.now(timezone.utc).date().isoformat()
    regime = classify_cascade_regime(india_vix=india_vix)

    aligned = load_aligned_factor_history(days=window_days + 30)
    if aligned.empty:
        cal = CascadeCalibration(
            as_of=as_of,
            window_days=window_days,
            blend_alpha=blend_alpha,
            regime=regime,
            status="insufficient_data",
            message="No aligned factor history for VAR calibration",
            var_factors=list(VAR_FACTOR_KEYS),
        )
        save_cascade_calibration(cal, ticker=ticker)
        return cal

    matrix = prepare_var_matrix(aligned, factors=VAR_FACTOR_KEYS)
    if len(matrix) < window_days // 2:
        matrix = matrix.tail(max(len(matrix), 0))
    else:
        matrix = matrix.tail(window_days)

    fit = fit_var1(matrix)
    if fit is None:
        cal = CascadeCalibration(
            as_of=as_of,
            window_days=window_days,
            blend_alpha=blend_alpha,
            regime=regime,
            status="insufficient_data",
            message=f"Need more observations (have {len(matrix)})",
            var_factors=list(VAR_FACTOR_KEYS),
        )
        save_cascade_calibration(cal, ticker=ticker)
        return cal

    var_rules = var_rules_from_fit(fit)
    blended = blend_all_rules(var_rules, alpha=blend_alpha)
    serial_rules: dict[str, list[dict[str, Any]]] = {}
    for primary, rows in blended.items():
        serial_rules[primary] = [
            {
                "secondary": r.secondary,
                "multiplier": round(r.multiplier, 6),
                "mode": r.mode,
                "source": r.source,
                "heuristic_multiplier": r.heuristic_multiplier,
                "var_multiplier": r.var_multiplier,
            }
            for r in rows
        ]

    cal = CascadeCalibration(
        as_of=as_of,
        method="rolling_ols_var1",
        window_days=window_days,
        blend_alpha=blend_alpha,
        regime=regime,
        status="ok",
        var_factors=list(fit.factors),
        rules=serial_rules,
        diagnostics={
            "n_obs": fit.n_obs,
            "var_edges": sum(len(v) for v in var_rules.values()),
            "blended_primaries": len(serial_rules),
            "raw_var_rules": var_rules_to_serializable(var_rules),
        },
    )
    save_cascade_calibration(cal, ticker=ticker)
    logger.info(
        "cascade calibration %s: regime=%s primaries=%s n_obs=%s",
        ticker,
        regime,
        len(serial_rules),
        fit.n_obs,
    )
    return cal
