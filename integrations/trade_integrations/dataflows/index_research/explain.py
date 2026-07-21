"""Factor attribution (SHAP + marginal), sensitivity curves, and event-impact graphs."""

from __future__ import annotations

import copy
import logging
from typing import Any

from trade_integrations.dataflows.index_research.factor_matrix import MACRO_FACTOR_KEYS
from trade_integrations.dataflows.index_research.horizon import HorizonProfile
from trade_integrations.dataflows.index_research.predictor import (
    ModelArtifact,
    _predict_macro_delta,
    cap_macro_delta,
    load_stored_model_artifact,
)

_NON_SCALAR_MACRO_KEYS = frozenset({"rbi_events", "rbi_context", "metadata", "source"})


def _macro_factor_value(macro_factors: dict[str, Any], factor: str) -> float:
    """Coerce one macro factor to float; non-scalar entries resolve to 0."""
    raw = macro_factors.get(factor, 0.0)
    if raw is None or isinstance(raw, (dict, list, tuple, set)):
        return 0.0
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def _iter_macro_factor_names(
    macro_factors: dict[str, Any],
    artifact: ModelArtifact | None,
) -> list[str]:
    if artifact and artifact.feature_names:
        return list(artifact.feature_names)
    known = [key for key in MACRO_FACTOR_KEYS if key in macro_factors]
    if known:
        return known
    return [
        key
        for key, raw in macro_factors.items()
        if key not in _NON_SCALAR_MACRO_KEYS and not isinstance(raw, (dict, list, tuple, set))
    ]

logger = logging.getLogger(__name__)


def _uncapped_macro_delta(
    macro_factors: dict[str, Any],
    horizon: HorizonProfile,
    artifact: ModelArtifact | None,
) -> float:
    """Raw Ridge macro delta before the ±5% cap (used for attribution math)."""
    return _predict_macro_delta(macro_factors, horizon, artifact)


def _capped_macro_delta(
    macro_factors: dict[str, Any],
    horizon: HorizonProfile,
    artifact: ModelArtifact | None,
) -> float:
    return cap_macro_delta(_uncapped_macro_delta(macro_factors, horizon, artifact))


_FACTOR_LABELS: dict[str, str] = {
    "oil_brent": "Brent crude",
    "oil_wti": "WTI crude",
    "usd_inr": "USD/INR",
    "gold": "Gold",
    "sp500": "S&P 500",
    "us_10y": "US 10Y yield",
    "india_vix": "India VIX",
    "fii_net_5d": "FII net (5d)",
    "dii_net_5d": "DII net (5d)",
    "nifty_pe": "Nifty PE",
    "nifty_earnings_yield": "Nifty earnings yield (E/P)",
    "nifty_dividend_yield": "Nifty dividend yield (D/P)",
    "nifty_pb": "Nifty price-to-book",
    "nifty_book_to_market": "Nifty book-to-market (B/M)",
    "nifty_pb_zscore_5y": "Nifty P/B z-score (5y)",
    "equity_risk_premium": "Equity risk premium (E/P − bond)",
    "india_10y": "India 10Y G-Sec yield",
    "india_91d_tbill": "India 91D T-Bill yield",
    "india_term_spread": "India term spread (10Y − T-Bill)",
    "india_credit_spread": "India credit spread",
    "india_vix_velocity_3d": "India VIX velocity (3d)",
    "usd_inr_momentum_5d": "USD/INR momentum (5d)",
    "us_10y_velocity_3d": "US 10Y velocity (3d)",
    "fii_net_5d_momentum": "FII net 5d momentum",
    "cpi_yoy_proxy": "CPI (proxy)",
    "repo_rate": "Repo rate",
    "index_sentiment": "Index sentiment",
    "nifty_pcr": "NIFTY PCR",
    "nifty_return_7d": "NIFTY 7d return",
    "nifty_return_14d": "NIFTY 14d return",
    "nifty_rsi_14": "NIFTY RSI(14)",
    "nifty_realized_vol_20d": "NIFTY realized vol",
    "nifty_ma20_distance_pct": "Distance from 20d MA",
    "constituent_momentum_7d": "Constituent momentum (7d)",
    "days_to_monthly_expiry": "Days to expiry",
    "is_budget_week": "Budget week",
    "is_results_season": "Results season",
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

_ABSOLUTE_SHOCK_FACTORS = frozenset({"repo_rate", "india_vix", "us_10y", "fii_net_5d", "dii_net_5d"})

# Always emit sensitivity curves for these drivers (flows, vol, oil, FX).
PINNED_SENSITIVITY_FACTORS: tuple[str, ...] = (
    "fii_net_5d",
    "dii_net_5d",
    "oil_brent",
    "india_vix",
    "nifty_pcr",
    "usd_inr",
)


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
    base = _uncapped_macro_delta(macro_factors, horizon, artifact)
    base_val = _macro_factor_value(macro_factors, factor)
    if factor in _ABSOLUTE_SHOCK_FACTORS:
        step = max(abs(step_pct) * 10, 0.1)
        perturbed = copy.deepcopy(macro_factors)
        perturbed[factor] = base_val + step
    else:
        step = abs(base_val * step_pct) if base_val else 0.01
        perturbed = copy.deepcopy(macro_factors)
        perturbed[factor] = base_val + step
    bumped = _uncapped_macro_delta(perturbed, horizon, artifact)
    return bumped - base


def _correlation_caveat_factors(artifact: ModelArtifact | None) -> set[str]:
    if artifact is None:
        return set()
    caveat: set[str] = set()
    for pair in artifact.correlated_pairs or []:
        if abs(float(pair.get("correlation") or 0.0)) >= 0.7:
            caveat.add(str(pair.get("factor_a") or ""))
            caveat.add(str(pair.get("factor_b") or ""))
    caveat.discard("")
    return caveat


def _union_find_clusters(
    pairs: list[tuple[str, str]],
    seed_groups: list[list[str]],
) -> list[list[str]]:
    """Merge redundancy seeds and high-|r| pairs into perturbation clusters."""
    parent: dict[str, str] = {}

    def find(node: str) -> str:
        parent.setdefault(node, node)
        if parent[node] != node:
            parent[node] = find(parent[node])
        return parent[node]

    def union(left: str, right: str) -> None:
        root_left = find(left)
        root_right = find(right)
        if root_left != root_right:
            parent[root_right] = root_left

    for group in seed_groups:
        if len(group) < 2:
            continue
        head = str(group[0])
        for member in group[1:]:
            union(head, str(member))

    for left, right in pairs:
        if left and right:
            union(left, right)

    clusters: dict[str, list[str]] = {}
    for node in parent:
        root = find(node)
        clusters.setdefault(root, []).append(node)

    return [sorted(members) for members in clusters.values() if len(members) >= 2]


def _build_perturbation_groups(artifact: ModelArtifact | None) -> list[list[str]]:
    """Domain redundancy groups plus live artifact correlated pairs (|r| ≥ 0.7)."""
    from trade_integrations.dataflows.index_research.factor_matrix import redundancy_audit

    audit = redundancy_audit()
    seed_groups = [list(group) for group in audit.get("redundancy_groups") or []]
    pairs: list[tuple[str, str]] = []
    for pair in audit.get("redundancy_pairs") or []:
        if isinstance(pair, (list, tuple)) and len(pair) >= 2:
            pairs.append((str(pair[0]), str(pair[1])))

    if artifact:
        for row in artifact.correlated_pairs or []:
            if abs(float(row.get("correlation") or 0.0)) >= 0.7:
                factor_a = str(row.get("factor_a") or "")
                factor_b = str(row.get("factor_b") or "")
                if factor_a and factor_b:
                    pairs.append((factor_a, factor_b))

    return _union_find_clusters(pairs, seed_groups)


def _aggregate_poly_shap_to_base(
    poly_shap: dict[str, float],
    feature_names: list[str],
) -> dict[str, float]:
    """Roll interaction terms into parent macro factors."""
    base: dict[str, float] = {name: 0.0 for name in feature_names}
    for term, value in poly_shap.items():
        parent = term.split(" ")[0] if term else term
        if parent in base:
            base[parent] += float(value)
        else:
            for name in feature_names:
                if term.startswith(name):
                    base[name] += float(value)
                    break
    return {k: v for k, v in base.items() if abs(v) > 1e-9}


def _try_linear_shap_contributions(
    macro_factors: dict[str, Any],
    artifact: ModelArtifact | None,
    horizon: HorizonProfile,
) -> dict[str, float] | None:
    if artifact is None or not artifact.feature_names or not artifact.coefficients:
        return None
    try:
        import numpy as np
        import shap
        from sklearn.linear_model import Ridge
        from sklearn.pipeline import Pipeline
        from sklearn.preprocessing import PolynomialFeatures, StandardScaler

        from trade_integrations.dataflows.index_research.ridge_pipeline import make_ridge_pipeline
    except ImportError:
        return None

    names = list(artifact.feature_names)
    values = np.array([_macro_factor_value(macro_factors, n) for n in names], dtype=float).reshape(1, -1)
    pipe = make_ridge_pipeline(alpha=float(artifact.ridge_alpha or 50.0), poly_degree=artifact.poly_degree)
    scaler = pipe.named_steps["scaler"]
    if artifact.feature_means and artifact.feature_stds:
        scaler.mean_ = np.asarray(artifact.feature_means, dtype=float)
        scaler.scale_ = np.asarray(artifact.feature_stds, dtype=float)
        scaler.var_ = scaler.scale_ ** 2
        scaler.n_features_in_ = len(artifact.feature_means)
    else:
        scaler.fit(values)
    poly = pipe.named_steps["poly"]
    poly.fit(scaler.transform(values))
    poly_names = [str(n) for n in poly.get_feature_names_out(names)]
    ridge = Ridge(alpha=float(artifact.ridge_alpha or 50.0), solver="lsqr")
    ridge.coef_ = np.array([artifact.coefficients.get(n, 0.0) for n in poly_names], dtype=float)
    ridge.intercept_ = float(artifact.intercept)

    try:
        explainer = shap.LinearExplainer(ridge, scaler.transform(values), feature_perturbation="interventional")
        shap_values = explainer.shap_values(scaler.transform(values))
        if isinstance(shap_values, list):
            shap_row = shap_values[0][0]
        else:
            shap_row = shap_values[0]
        poly_map = {poly_names[i]: float(shap_row[i]) for i in range(len(poly_names))}
        return _aggregate_poly_shap_to_base(poly_map, names)
    except Exception as exc:
        logger.debug("LinearExplainer failed: %s", exc)
        return None


def _grouped_marginal_impacts(
    macro_factors: dict[str, Any],
    artifact: ModelArtifact | None,
    horizon: HorizonProfile,
    *,
    groups: list[list[str]] | None = None,
) -> dict[str, float]:
    """Perturb correlation clusters together (group Shapley-style marginal attribution)."""
    perturb_groups = groups if groups is not None else _build_perturbation_groups(artifact)
    present = set(_iter_macro_factor_names(macro_factors, artifact))
    raw: dict[str, float] = {}
    perturbed_members: set[str] = set()

    for group in perturb_groups:
        members = [member for member in group if member in present]
        if len(members) < 2:
            continue
        base = _uncapped_macro_delta(macro_factors, horizon, artifact)
        perturbed = copy.deepcopy(macro_factors)
        for member in members:
            val = _macro_factor_value(macro_factors, member)
            perturbed[member] = val * 1.05 if member not in _ABSOLUTE_SHOCK_FACTORS else val + 0.05
        bumped = _uncapped_macro_delta(perturbed, horizon, artifact)
        share = (bumped - base) / len(members)
        for member in members:
            raw[member] = raw.get(member, 0.0) + share
            perturbed_members.add(member)

    for factor in _iter_macro_factor_names(macro_factors, artifact):
        if factor in perturbed_members:
            continue
        if factor in macro_factors or (artifact and factor in artifact.feature_names):
            raw[factor] = _marginal_macro_impact(macro_factors, factor, artifact, horizon)
    return raw


def _try_shap_macro_contributions(
    macro_factors: dict[str, Any],
    artifact: ModelArtifact | None,
    horizon: HorizonProfile,
) -> dict[str, float] | None:
    linear = _try_linear_shap_contributions(macro_factors, artifact, horizon)
    if linear:
        return linear
    if artifact is None or not artifact.feature_names:
        return None
    try:
        import numpy as np
        import shap
    except ImportError:
        return None

    names = list(artifact.feature_names)
    baseline = np.array([_macro_factor_value(macro_factors, n) for n in names], dtype=float)

    def predict_fn(X: np.ndarray) -> np.ndarray:
        out = []
        for row in X:
            row_factors = {names[i]: float(row[i]) for i in range(len(names))}
            out.append(_uncapped_macro_delta(row_factors, horizon, artifact))
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
    macro_delta = _capped_macro_delta(macro_factors, horizon, artifact)
    caveat_factors = _correlation_caveat_factors(artifact)
    use_grouped = bool(artifact and artifact.multicollinearity_warning)

    if use_grouped:
        raw = _grouped_marginal_impacts(macro_factors, artifact, horizon)
        method = "grouped_marginal"
        attribution_disclaimer = (
            "Group attribution perturbs correlated macro blocks together; "
            "per-factor splits are approximate. Not causal — model sensitivity only."
        )
    else:
        shap_raw = _try_shap_macro_contributions(macro_factors, artifact, horizon)
        if shap_raw:
            raw = shap_raw
            method = "linear_shap"
        else:
            raw = _grouped_marginal_impacts(macro_factors, artifact, horizon)
            method = "marginal"
        attribution_disclaimer = (
            "Attribution shows model sensitivity, not causal effect. "
            "Correlated factors share credit."
        )

    contributors = _normalize_contributions(raw, macro_delta)

    total_return = bottom_up_return_pct + macro_delta
    for row in contributors:
        row["contribution_index_pts"] = round(spot * row["contribution_pct"] / 100.0, 2)
        row["value"] = macro_factors.get(row["factor"])
        row["correlation_caveat"] = row["factor"] in caveat_factors
        if total_return:
            row["share_of_total_equation"] = round(
                row["contribution_pct"] / total_return,
                4,
            )

    channel_attribution: dict[str, float] | None = None
    try:
        from trade_integrations.dataflows.index_research.prediction_algorithms.causes.channel_attribution import (
            channel_attribution_from_contributors,
        )

        channel_attribution = channel_attribution_from_contributors(contributors)
    except Exception:
        channel_attribution = None

    return {
        "method": method,
        "attribution_disclaimer": attribution_disclaimer,
        "macro_delta_pct": round(macro_delta, 4),
        "bottom_up_return_pct": round(bottom_up_return_pct, 4),
        "total_return_pct": round(total_return, 4),
        "contributors": contributors,
        "channel_attribution": channel_attribution,
        "multicollinearity_warning": bool(artifact and artifact.multicollinearity_warning),
        "correlated_pairs": list(artifact.correlated_pairs or [])[:5] if artifact else [],
    }


def _factor_available(
    factor: str,
    macro_factors: dict[str, Any],
    artifact: ModelArtifact | None,
) -> bool:
    if factor in macro_factors:
        return True
    return bool(artifact and factor in (artifact.feature_names or []))


def _merge_sensitivity_factors(
    top_factors: list[str],
    macro_factors: dict[str, Any],
    artifact: ModelArtifact | None,
    *,
    max_factors: int,
) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for factor in PINNED_SENSITIVITY_FACTORS:
        if factor not in seen:
            ordered.append(factor)
            seen.add(factor)
    for factor in top_factors:
        if factor not in seen:
            ordered.append(factor)
            seen.add(factor)
    for factor in _iter_macro_factor_names(macro_factors, artifact):
        if factor not in seen and _factor_available(factor, macro_factors, artifact):
            ordered.append(factor)
            seen.add(factor)
    cap = max(max_factors, len(PINNED_SENSITIVITY_FACTORS))
    return ordered[:cap]


def build_factor_sensitivity(
    macro_factors: dict[str, Any],
    *,
    horizon: HorizonProfile,
    spot: float,
    bottom_up_return_pct: float,
    headline_return_pct: float | None = None,
    artifact: ModelArtifact | None = None,
    sweep_pct: tuple[int, int, int] = (-10, 10, 1),
    max_factors: int = 12,
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
    top_factors = _merge_sensitivity_factors(
        [
            row["factor"]
            for row in explanation.get("contributors", [])[:max_factors]
        ],
        macro_factors,
        artifact,
        max_factors=max_factors,
    )
    if not top_factors:
        top_factors = [
            k
            for k in _iter_macro_factor_names(macro_factors, artifact)
            if k in macro_factors
        ][:max_factors]

    base_ridge_macro = _capped_macro_delta(macro_factors, horizon, artifact)
    reconciled_base_macro = (
        headline_return_pct - bottom_up_return_pct
        if headline_return_pct is not None
        else base_ridge_macro
    )
    reconcile_scale = (
        reconciled_base_macro / base_ridge_macro
        if headline_return_pct is not None and abs(base_ridge_macro) > 1e-9
        else 1.0
    )

    curves: list[dict[str, Any]] = []
    start, end, step = sweep_pct
    pct_grid = list(range(start, end + 1, step))

    for factor in top_factors:
        base_val = _macro_factor_value(macro_factors, factor)
        points: list[dict[str, Any]] = []
        for pct in pct_grid:
            perturbed = copy.deepcopy(macro_factors)
            if factor in _ABSOLUTE_SHOCK_FACTORS:
                delta = (pct / 100.0) * max(abs(base_val), 1.0)
                perturbed[factor] = base_val + delta
            else:
                perturbed[factor] = base_val * (1.0 + pct / 100.0) if base_val else pct / 100.0

            shocked_ridge_macro = _capped_macro_delta(perturbed, horizon, artifact)
            raw_marginal = shocked_ridge_macro - base_ridge_macro
            macro_delta = round(raw_marginal * reconcile_scale, 4)
            if pct == 0 and headline_return_pct is not None:
                total_return = headline_return_pct
                macro_delta = round(reconciled_base_macro, 4)
            else:
                total_return = round(bottom_up_return_pct + reconciled_base_macro + macro_delta, 4)
            index_level = spot * (1.0 + total_return / 100.0)
            points.append(
                {
                    "factor_delta_pct": pct,
                    "factor_value": perturbed.get(factor),
                    "macro_delta_pct": macro_delta,
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
        macro_delta = _capped_macro_delta(shocked_factors, horizon, artifact)
        total_return = bottom_up_return_pct + macro_delta
        index_level = spot * (1.0 + total_return / 100.0)

        primary = max(shocks.keys(), key=lambda k: abs(shocks[k]))
        base_primary = float(macro_factors.get(primary, 0.0) or 0.0)
        shock_primary = float(shocks[primary])

        event_points: list[dict[str, Any]] = []
        for t in (0.0, 0.25, 0.5, 0.75, 1.0):
            partial_shocks = {k: v * t for k, v in shocks.items()}
            partial_factors = _apply_event_shocks(macro_factors, partial_shocks)
            partial_macro = _capped_macro_delta(partial_factors, horizon, artifact)
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


def _rescale_explanation_to_headline(
    explanation: dict[str, Any],
    *,
    spot: float,
    bottom_up_return_pct: float,
    headline_return_pct: float,
) -> dict[str, Any]:
    """Align contributor rows with reconciled headline macro delta (not raw Ridge cap)."""
    ridge_macro = float(explanation.get("macro_delta_pct") or 0.0)
    reconciled_macro = round(headline_return_pct - bottom_up_return_pct, 4)
    explanation["ridge_macro_delta_pct"] = round(ridge_macro, 4)
    explanation["total_return_pct"] = round(headline_return_pct, 4)
    explanation["macro_delta_pct"] = reconciled_macro

    contributors = explanation.get("contributors") or []
    if not contributors:
        return explanation

    if abs(ridge_macro) < 1e-9:
        for row in contributors:
            row["contribution_pct"] = 0.0
            row["share_of_macro"] = 0.0
            row["contribution_index_pts"] = 0.0
        explanation["attribution_rescaled"] = True
        return explanation

    if abs(reconciled_macro - ridge_macro) <= 0.01:
        return explanation

    scale = reconciled_macro / ridge_macro
    total_return = bottom_up_return_pct + reconciled_macro
    for row in contributors:
        row["contribution_pct"] = round(float(row.get("contribution_pct") or 0.0) * scale, 4)
        if "marginal_impact_pct" in row:
            row["marginal_impact_pct"] = round(float(row["marginal_impact_pct"]) * scale, 4)
        if abs(reconciled_macro) > 1e-9:
            row["share_of_macro"] = round(row["contribution_pct"] / reconciled_macro, 4)
        if total_return:
            row["share_of_total_equation"] = round(row["contribution_pct"] / total_return, 4)
        row["contribution_index_pts"] = round(spot * row["contribution_pct"] / 100.0, 2)

    explanation["attribution_rescaled"] = True
    return explanation


def build_factor_explanation_bundle(
    macro_factors: dict[str, Any],
    scenarios: list[dict[str, Any]],
    *,
    horizon: HorizonProfile,
    spot: float,
    bottom_up_return_pct: float,
    headline_return_pct: float | None = None,
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
    if headline_return_pct is not None:
        explanation = _rescale_explanation_to_headline(
            explanation,
            spot=spot,
            bottom_up_return_pct=bottom_up_return_pct,
            headline_return_pct=headline_return_pct,
        )
    sensitivity = build_factor_sensitivity(
        macro_factors,
        horizon=horizon,
        spot=spot,
        bottom_up_return_pct=bottom_up_return_pct,
        headline_return_pct=headline_return_pct,
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
