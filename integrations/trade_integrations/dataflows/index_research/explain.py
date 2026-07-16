"""Factor attribution (SHAP + marginal), sensitivity curves, and event-impact graphs."""

from __future__ import annotations

import copy
import logging
from typing import Any

from trade_integrations.dataflows.index_research.horizon import HorizonProfile
from trade_integrations.dataflows.index_research.predictor import (
    ModelArtifact,
    _predict_macro_delta,
    load_stored_model_artifact,
)

logger = logging.getLogger(__name__)

_FACTOR_LABELS: dict[str, str] = {
    "oil_brent": "Brent crude",
    "oil_wti": "WTI crude",
    "usd_inr": "USD/INR",
    "gold": "Gold",
    "sp500": "S&P 500",
    "us_10y": "US 10Y yield",
    "india_vix": "India VIX",
    "fii_net_5d": "FII net (5d)",
    "nifty_pe": "Nifty PE",
    "cpi_yoy_proxy": "CPI (proxy)",
    "repo_rate": "Repo rate",
    "index_sentiment": "Index sentiment",
}

# Event → relative factor shocks (fraction of current level, or absolute for rates/vix)
_EVENT_SHOCKS: list[dict[str, Any]] = [
    {
        "event": "oil_spike",
        "outcome": "supply_shock",
        "factor_shocks": {"oil_brent": 0.10, "usd_inr": 0.015, "india_vix": 1.5},
        "probability": 0.2,
    },
    {
        "event": "rbi_policy",
        "outcome": "hawkish_surprise",
        "factor_shocks": {"repo_rate": 0.25, "usd_inr": 0.01, "india_vix": 2.0},
        "probability": 0.2,
    },
    {
        "event": "rbi_policy",
        "outcome": "dovish_hold",
        "factor_shocks": {"repo_rate": -0.1, "usd_inr": -0.008, "india_vix": -1.0},
        "probability": 0.35,
    },
    {
        "event": "fii_outflow",
        "outcome": "risk_off",
        "factor_shocks": {"fii_net_5d": -0.30, "usd_inr": 0.02, "india_vix": 3.0, "sp500": -0.03},
        "probability": 0.25,
    },
    {
        "event": "earnings_cluster",
        "outcome": "positive_surprises",
        "factor_shocks": {"index_sentiment": 0.15, "india_vix": -0.5},
        "probability": 0.35,
    },
    {
        "event": "earnings_cluster",
        "outcome": "negative_surprises",
        "factor_shocks": {"index_sentiment": -0.20, "india_vix": 2.0},
        "probability": 0.25,
    },
]

_ABSOLUTE_SHOCK_FACTORS = frozenset({"repo_rate", "india_vix", "us_10y", "fii_net_5d"})


def _factor_label(key: str) -> str:
    return _FACTOR_LABELS.get(key, key.replace("_", " ").title())


def _apply_shock(factors: dict[str, Any], factor: str, shock: float) -> dict[str, Any]:
    out = copy.deepcopy(factors)
    base = float(out.get(factor, 0.0) or 0.0)
    if factor in _ABSOLUTE_SHOCK_FACTORS:
        out[factor] = base + shock
    else:
        out[factor] = base * (1.0 + shock) if base else shock
    return out


def _apply_event_shocks(factors: dict[str, Any], shocks: dict[str, float]) -> dict[str, Any]:
    out = copy.deepcopy(factors)
    for factor, shock in shocks.items():
        out = _apply_shock(out, factor, shock)
    return out


def _marginal_macro_impact(
    macro_factors: dict[str, Any],
    factor: str,
    artifact: ModelArtifact | None,
    horizon: HorizonProfile,
    *,
    step_pct: float = 0.05,
) -> float:
    base = _predict_macro_delta(macro_factors, horizon, artifact)
    base_val = float(macro_factors.get(factor, 0.0) or 0.0)
    if factor in _ABSOLUTE_SHOCK_FACTORS:
        step = max(abs(step_pct) * 10, 0.1)
        perturbed = copy.deepcopy(macro_factors)
        perturbed[factor] = base_val + step
    else:
        step = abs(base_val * step_pct) if base_val else 0.01
        perturbed = copy.deepcopy(macro_factors)
        perturbed[factor] = base_val + step
    bumped = _predict_macro_delta(perturbed, horizon, artifact)
    return bumped - base


def _try_shap_macro_contributions(
    macro_factors: dict[str, Any],
    artifact: ModelArtifact | None,
    horizon: HorizonProfile,
) -> dict[str, float] | None:
    if artifact is None or not artifact.feature_names:
        return None
    try:
        import numpy as np
        import shap
    except ImportError:
        return None

    names = list(artifact.feature_names)
    baseline = np.array([float(macro_factors.get(n, 0.0) or 0.0) for n in names], dtype=float)

    def predict_fn(X: np.ndarray) -> np.ndarray:
        out = []
        for row in X:
            row_factors = {names[i]: float(row[i]) for i in range(len(names))}
            out.append(_predict_macro_delta(row_factors, horizon, artifact))
        return np.array(out, dtype=float)

    try:
        explainer = shap.Explainer(predict_fn, baseline.reshape(1, -1))
        values = explainer(baseline.reshape(1, -1))
        shap_row = values.values[0]
        return {names[i]: float(shap_row[i]) for i in range(len(names))}
    except Exception as exc:
        logger.debug("SHAP explain failed, using marginal attribution: %s", exc)
        return None


def _normalize_contributions(
    raw: dict[str, float],
    macro_delta: float,
) -> list[dict[str, Any]]:
    total_raw = sum(raw.values())
    if abs(total_raw) < 1e-12:
        return []

    contributors: list[dict[str, Any]] = []
    for factor, impact in raw.items():
        if abs(impact) < 1e-9:
            continue
        share = impact / total_raw if total_raw else 0.0
        contribution_pct = macro_delta * share
        contributors.append(
            {
                "factor": factor,
                "label": _factor_label(factor),
                "marginal_impact_pct": round(impact, 4),
                "contribution_pct": round(contribution_pct, 4),
                "share_of_macro": round(share, 4),
            }
        )
    contributors.sort(key=lambda row: abs(row["contribution_pct"]), reverse=True)
    return contributors


def explain_macro_factors(
    macro_factors: dict[str, Any],
    *,
    horizon: HorizonProfile,
    spot: float,
    bottom_up_return_pct: float,
    artifact: ModelArtifact | None = None,
) -> dict[str, Any]:
    """Attribute macro portion of the prediction to each factor."""
    artifact = artifact or load_stored_model_artifact()
    macro_delta = _predict_macro_delta(macro_factors, horizon, artifact)

    shap_raw = _try_shap_macro_contributions(macro_factors, artifact, horizon)
    method = "shap" if shap_raw else "marginal"

    if shap_raw:
        raw = shap_raw
    else:
        factors = artifact.feature_names if artifact and artifact.feature_names else list(macro_factors.keys())
        raw = {
            f: _marginal_macro_impact(macro_factors, f, artifact, horizon)
            for f in factors
            if f in macro_factors or (artifact and f in artifact.feature_names)
        }

    contributors = _normalize_contributions(raw, macro_delta)

    total_return = bottom_up_return_pct + macro_delta
    for row in contributors:
        row["contribution_index_pts"] = round(spot * row["contribution_pct"] / 100.0, 2)
        row["value"] = macro_factors.get(row["factor"])
        if total_return:
            row["share_of_total_equation"] = round(
                row["contribution_pct"] / total_return,
                4,
            )

    return {
        "method": method,
        "macro_delta_pct": round(macro_delta, 4),
        "bottom_up_return_pct": round(bottom_up_return_pct, 4),
        "total_return_pct": round(total_return, 4),
        "contributors": contributors,
    }


def build_factor_sensitivity(
    macro_factors: dict[str, Any],
    *,
    horizon: HorizonProfile,
    spot: float,
    bottom_up_return_pct: float,
    artifact: ModelArtifact | None = None,
    sweep_pct: tuple[int, int, int] = (-10, 10, 1),
    max_factors: int = 6,
) -> list[dict[str, Any]]:
    """Per-factor sweep: how index level changes when one factor moves ±%."""
    artifact = artifact or load_stored_model_artifact()
    explanation = explain_macro_factors(
        macro_factors,
        horizon=horizon,
        spot=spot,
        bottom_up_return_pct=bottom_up_return_pct,
        artifact=artifact,
    )
    top_factors = [
        row["factor"]
        for row in explanation.get("contributors", [])[:max_factors]
    ]
    if not top_factors:
        top_factors = [k for k in macro_factors if k not in {"rbi_events"}][:max_factors]

    curves: list[dict[str, Any]] = []
    start, end, step = sweep_pct
    pct_grid = list(range(start, end + 1, step))

    for factor in top_factors:
        base_val = float(macro_factors.get(factor, 0.0) or 0.0)
        points: list[dict[str, Any]] = []
        for pct in pct_grid:
            perturbed = copy.deepcopy(macro_factors)
            if factor in _ABSOLUTE_SHOCK_FACTORS:
                delta = (pct / 100.0) * max(abs(base_val), 1.0)
                perturbed[factor] = base_val + delta
            else:
                perturbed[factor] = base_val * (1.0 + pct / 100.0) if base_val else pct / 100.0

            macro_delta = _predict_macro_delta(perturbed, horizon, artifact)
            total_return = bottom_up_return_pct + macro_delta
            index_level = spot * (1.0 + total_return / 100.0)
            points.append(
                {
                    "factor_delta_pct": pct,
                    "factor_value": perturbed.get(factor),
                    "macro_delta_pct": round(macro_delta, 4),
                    "return_pct": round(total_return, 4),
                    "index_level": round(index_level, 2),
                }
            )

        curves.append(
            {
                "factor": factor,
                "label": _factor_label(factor),
                "current_value": base_val,
                "points": points,
            }
        )
    return curves


def build_event_impact_curves(
    macro_factors: dict[str, Any],
    scenarios: list[dict[str, Any]],
    *,
    horizon: HorizonProfile,
    spot: float,
    bottom_up_return_pct: float,
    artifact: ModelArtifact | None = None,
) -> list[dict[str, Any]]:
    """Event scenarios with factor shocks and index response curves on the primary driver."""
    artifact = artifact or load_stored_model_artifact()
    scenario_keys = {
        (str(s.get("event")), str(s.get("outcome")))
        for s in scenarios
        if s.get("event")
    }

    curves: list[dict[str, Any]] = []
    for template in _EVENT_SHOCKS:
        key = (template["event"], template["outcome"])
        if scenario_keys and key not in scenario_keys:
            continue

        shocks = template["factor_shocks"]
        shocked_factors = _apply_event_shocks(macro_factors, shocks)
        macro_delta = _predict_macro_delta(shocked_factors, horizon, artifact)
        total_return = bottom_up_return_pct + macro_delta
        index_level = spot * (1.0 + total_return / 100.0)

        primary = max(shocks.keys(), key=lambda k: abs(shocks[k]))
        base_primary = float(macro_factors.get(primary, 0.0) or 0.0)
        shock_primary = float(shocks[primary])

        event_points: list[dict[str, Any]] = []
        for t in (0.0, 0.25, 0.5, 0.75, 1.0):
            partial_shocks = {k: v * t for k, v in shocks.items()}
            partial_factors = _apply_event_shocks(macro_factors, partial_shocks)
            partial_macro = _predict_macro_delta(partial_factors, horizon, artifact)
            partial_return = bottom_up_return_pct + partial_macro
            event_points.append(
                {
                    "shock_progress": t,
                    "primary_factor": primary,
                    "primary_value": round(
                        base_primary * (1.0 + shock_primary * t)
                        if primary not in _ABSOLUTE_SHOCK_FACTORS
                        else base_primary + shock_primary * t,
                        4,
                    ),
                    "return_pct": round(partial_return, 4),
                    "index_level": round(spot * (1.0 + partial_return / 100.0), 2),
                }
            )

        prob = template.get("probability")
        for scenario in scenarios:
            if scenario.get("event") == template["event"] and scenario.get("outcome") == template["outcome"]:
                prob = scenario.get("probability", prob)
                break

        curves.append(
            {
                "event": template["event"],
                "outcome": template["outcome"],
                "probability": prob,
                "factor_shocks": shocks,
                "spot": spot,
                "index_level": round(index_level, 2),
                "return_pct": round(total_return, 4),
                "macro_delta_pct": round(macro_delta, 4),
                "primary_factor": primary,
                "curve": event_points,
            }
        )

    if not curves and scenarios:
        return build_event_impact_curves(
            macro_factors,
            [],
            horizon=horizon,
            spot=spot,
            bottom_up_return_pct=bottom_up_return_pct,
            artifact=artifact,
        )

    return curves[:6]


def build_factor_explanation_bundle(
    macro_factors: dict[str, Any],
    scenarios: list[dict[str, Any]],
    *,
    horizon: HorizonProfile,
    spot: float,
    bottom_up_return_pct: float,
    artifact: ModelArtifact | None = None,
) -> dict[str, Any]:
    """Full explainability payload for hub artifact and widgets."""
    explanation = explain_macro_factors(
        macro_factors,
        horizon=horizon,
        spot=spot,
        bottom_up_return_pct=bottom_up_return_pct,
        artifact=artifact,
    )
    sensitivity = build_factor_sensitivity(
        macro_factors,
        horizon=horizon,
        spot=spot,
        bottom_up_return_pct=bottom_up_return_pct,
        artifact=artifact,
    )
    event_curves = build_event_impact_curves(
        macro_factors,
        scenarios,
        horizon=horizon,
        spot=spot,
        bottom_up_return_pct=bottom_up_return_pct,
        artifact=artifact,
    )
    return {
        "factor_explanation": explanation,
        "factor_sensitivity": sensitivity,
        "event_impact_curves": event_curves,
    }
