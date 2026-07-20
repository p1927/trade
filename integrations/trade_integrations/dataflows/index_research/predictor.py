"""Hybrid Nifty predictor — bottom-up constituents + macro Ridge regression."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

from trade_integrations.dataflows.index_research.attribution import (
    attribute_constituents,
    rollup_attribution,
)
from trade_integrations.dataflows.index_research.factor_matrix import (
    MACRO_FACTOR_KEYS,
    build_factor_matrix,
)
from trade_integrations.dataflows.index_research.factor_store import (
    load_model_artifact,
    save_model_artifact,
)
from trade_integrations.dataflows.index_research.horizon import HorizonProfile
from trade_integrations.dataflows.index_research.models import ConstituentSignal
from trade_integrations.dataflows.index_research.pipeline_log import PipelineLogger

_DEFAULT_MAE_PCT = 1.5
_MACRO_DELTA_CAP_PCT = 5.0
_MACRO_SHRINK_THRESHOLD_PCT = 3.0
_RIDGE_ALPHA = 50.0
_MACRO_TRUST_MAE_GOOD = 1.5
_MACRO_TRUST_MAE_POOR = 7.0
from trade_integrations.dataflows.index_research.views import classify_index_view

_DIRECTION_PROB_BULL = 0.55
_DIRECTION_PROB_BEAR = 0.45
_MIN_WALK_FORWARD_TRAIN = 15


def cap_macro_delta(macro_delta: float) -> float:
    """Clamp raw Ridge macro delta to a plausible 14d move."""
    return max(-_MACRO_DELTA_CAP_PCT, min(_MACRO_DELTA_CAP_PCT, macro_delta))


def shrink_macro_delta(
    raw_macro: float,
    scenario_anchor_return_pct: float | None = None,
) -> float:
    """Shrink extreme raw macro toward scenario anchor before hard cap."""
    adjusted = raw_macro
    if scenario_anchor_return_pct is not None and abs(raw_macro) > _MACRO_SHRINK_THRESHOLD_PCT:
        if raw_macro * scenario_anchor_return_pct < 0:
            # Sign conflict: saturated macro vs scenario — blend toward anchor first.
            conflict_weight = min(0.85, abs(raw_macro) / _MACRO_DELTA_CAP_PCT)
            adjusted = raw_macro * (1.0 - conflict_weight) + scenario_anchor_return_pct * conflict_weight
    if scenario_anchor_return_pct is None or abs(adjusted) <= _MACRO_SHRINK_THRESHOLD_PCT:
        return cap_macro_delta(adjusted)
    excess = abs(adjusted) - _MACRO_SHRINK_THRESHOLD_PCT
    weight = min(0.75, excess / 4.0)
    shrunk = adjusted * (1.0 - weight) + scenario_anchor_return_pct * weight
    return cap_macro_delta(shrunk)


@dataclass
class ModelArtifact:
    """Serialized Ridge + polynomial macro model."""

    coefficients: dict[str, float] = field(default_factory=dict)
    intercept: float = 0.0
    mae: float = _DEFAULT_MAE_PCT
    r2_walk_forward: float | None = None
    poly_degree: int = 2
    feature_names: list[str] = field(default_factory=list)
    trained_at: str = ""
    horizon_name: str = "B"
    direction_coefficients: dict[str, float] = field(default_factory=dict)
    direction_intercept: float = 0.0
    direction_hit_rate_oos: float | None = None
    feature_means: list[float] = field(default_factory=list)
    feature_stds: list[float] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> ModelArtifact | None:
        if not payload:
            return None
        return cls(
            coefficients={str(k): float(v) for k, v in (payload.get("coefficients") or {}).items()},
            intercept=float(payload.get("intercept") or 0.0),
            mae=float(payload.get("mae") or _DEFAULT_MAE_PCT),
            r2_walk_forward=(
                float(payload["r2_walk_forward"])
                if payload.get("r2_walk_forward") is not None
                else None
            ),
            poly_degree=int(payload.get("poly_degree") or 2),
            feature_names=[str(name) for name in (payload.get("feature_names") or [])],
            trained_at=str(payload.get("trained_at") or ""),
            horizon_name=str(payload.get("horizon_name") or "B"),
            direction_coefficients={
                str(k): float(v) for k, v in (payload.get("direction_coefficients") or {}).items()
            },
            direction_intercept=float(payload.get("direction_intercept") or 0.0),
            direction_hit_rate_oos=(
                float(payload["direction_hit_rate_oos"])
                if payload.get("direction_hit_rate_oos") is not None
                else None
            ),
            feature_means=[float(v) for v in (payload.get("feature_means") or [])],
            feature_stds=[float(v) for v in (payload.get("feature_stds") or [])],
        )


def _require_sklearn():
    try:
        from sklearn.linear_model import LogisticRegression, Ridge
        from sklearn.metrics import mean_absolute_error, r2_score
        from sklearn.preprocessing import PolynomialFeatures, StandardScaler
    except ImportError as exc:
        raise ImportError("scikit-learn is required for index predictor training") from exc
    return Ridge, LogisticRegression, mean_absolute_error, r2_score, PolynomialFeatures, StandardScaler


def _fit_feature_scaler(X: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    means = X.mean(axis=0)
    stds = X.std(axis=0)
    safe_stds = np.where(stds < 1e-9, 1.0, stds)
    return means, safe_stds


def _scale_features(
    X: np.ndarray,
    means: np.ndarray | list[float],
    stds: np.ndarray | list[float],
) -> np.ndarray:
    mean_arr = np.asarray(means, dtype=float)
    std_arr = np.asarray(stds, dtype=float)
    if mean_arr.size == 0 or std_arr.size == 0:
        return np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
    safe_stds = np.where(std_arr < 1e-9, 1.0, std_arr)
    scaled = (X - mean_arr) / safe_stds
    return np.nan_to_num(scaled, nan=0.0, posinf=0.0, neginf=0.0)


def _finite_float(raw: Any, default: float = 0.0) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    if not np.isfinite(value):
        return default
    return value


def _macro_trust_weight(mae: float) -> float:
    if mae <= _MACRO_TRUST_MAE_GOOD:
        return 1.0
    if mae >= _MACRO_TRUST_MAE_POOR:
        return 0.15
    span = _MACRO_TRUST_MAE_POOR - _MACRO_TRUST_MAE_GOOD
    return max(0.15, 1.0 - (mae - _MACRO_TRUST_MAE_GOOD) / span * 0.85)


def _expand_poly(raw: np.ndarray, feature_names: list[str], poly_degree: int) -> tuple[np.ndarray, list[str]]:
    _, _, _, _, PolynomialFeatures, _ = _require_sklearn()
    poly = PolynomialFeatures(
        degree=poly_degree,
        interaction_only=True,
        include_bias=False,
    )
    template = np.vstack([np.zeros(len(feature_names)), np.ones(len(feature_names))])
    poly.fit(template)
    sanitized = np.nan_to_num(raw, nan=0.0, posinf=0.0, neginf=0.0)
    expanded = poly.transform(sanitized)
    names = [str(name) for name in poly.get_feature_names_out(feature_names)]
    return expanded, names


def _predict_macro_delta(
    macro_factors: dict[str, Any],
    horizon: HorizonProfile,
    artifact: ModelArtifact | None,
    *,
    macro_trust_multiplier: float = 1.0,
) -> float:
    if artifact is None or not artifact.feature_names:
        return 0.0

    values: list[float] = []
    for name in artifact.feature_names:
        raw = macro_factors.get(name, 0.0)
        if raw is None or isinstance(raw, (dict, list, tuple, set)):
            values.append(0.0)
            continue
        values.append(_finite_float(raw, 0.0))
    raw = np.array(values, dtype=float).reshape(1, -1)
    if artifact.feature_means and artifact.feature_stds:
        raw = _scale_features(raw, artifact.feature_means, artifact.feature_stds)
    expanded, poly_names = _expand_poly(raw, artifact.feature_names, artifact.poly_degree)
    coefs = np.array([artifact.coefficients.get(name, 0.0) for name in poly_names], dtype=float)
    trust = _macro_trust_weight(float(artifact.mae or _DEFAULT_MAE_PCT)) * max(0.0, macro_trust_multiplier)
    return float(artifact.intercept + np.dot(expanded.flatten(), coefs)) * trust


def build_macro_features(macro_row: dict[str, Any], horizon: HorizonProfile) -> np.ndarray:
    """Extract ordered macro feature vector from a factor snapshot row."""
    del horizon  # reserved for horizon-specific feature subsets
    values: list[float] = []
    for key in MACRO_FACTOR_KEYS:
        if key in macro_row and macro_row[key] is not None:
            values.append(float(macro_row[key]))
    if not values:
        for key, raw in macro_row.items():
            if key in {"factor", "date", "source", "metadata", "z_score"}:
                continue
            try:
                values.append(float(raw))
            except (TypeError, ValueError):
                continue
    if not values:
        return np.zeros((1, 1), dtype=float)
    return np.array(values[: len(MACRO_FACTOR_KEYS)], dtype=float).reshape(1, -1)


def _walk_forward_metrics(
    X: np.ndarray,
    y: np.ndarray,
    *,
    poly_degree: int,
) -> tuple[float | None, float | None]:
    """Expanding-window out-of-sample MAE and R² on the holdout tail."""
    Ridge, _, mean_absolute_error, r2_score, PolynomialFeatures, _ = _require_sklearn()
    holdout = max(3, min(len(y) // 5, 15))
    start = len(y) - holdout
    if start < _MIN_WALK_FORWARD_TRAIN:
        return None, None

    oos_true: list[float] = []
    oos_pred: list[float] = []
    for i in range(start, len(y)):
        if i < _MIN_WALK_FORWARD_TRAIN:
            continue
        X_train_raw = X[:i]
        X_test_raw = X[i : i + 1]
        train_means, train_stds = _fit_feature_scaler(X_train_raw)
        X_train = _scale_features(X_train_raw, train_means, train_stds)
        X_test = _scale_features(X_test_raw, train_means, train_stds)
        poly = PolynomialFeatures(
            degree=poly_degree,
            interaction_only=True,
            include_bias=False,
        )
        X_train_poly = poly.fit_transform(X_train)
        X_test_poly = poly.transform(X_test)
        model = Ridge(alpha=_RIDGE_ALPHA)
        model.fit(X_train_poly, y[:i])
        oos_pred.append(float(model.predict(X_test_poly)[0]))
        oos_true.append(float(y[i]))

    if len(oos_true) < 2:
        return None, None
    return (
        float(mean_absolute_error(oos_true, oos_pred)),
        float(r2_score(oos_true, oos_pred)),
    )


def _walk_forward_direction_hit_rate(
    X: np.ndarray,
    y: np.ndarray,
    *,
    poly_degree: int,
) -> float | None:
    """Out-of-sample direction hit rate for a logistic regression head."""
    _, LogisticRegression, _, _, PolynomialFeatures, _ = _require_sklearn()
    holdout = max(3, min(len(y) // 5, 15))
    start = len(y) - holdout
    if start < _MIN_WALK_FORWARD_TRAIN:
        return None

    labels = (y > 0).astype(int)
    if len(set(labels.tolist())) < 2:
        return None

    hits = 0
    total = 0
    for i in range(start, len(y)):
        if i < _MIN_WALK_FORWARD_TRAIN:
            continue
        X_train_raw = X[:i]
        X_test_raw = X[i : i + 1]
        train_means, train_stds = _fit_feature_scaler(X_train_raw)
        X_train = _scale_features(X_train_raw, train_means, train_stds)
        X_test = _scale_features(X_test_raw, train_means, train_stds)
        poly = PolynomialFeatures(
            degree=poly_degree,
            interaction_only=True,
            include_bias=False,
        )
        X_train_poly = poly.fit_transform(X_train)
        X_test_poly = poly.transform(X_test)
        clf = LogisticRegression(C=0.5, max_iter=1000)
        clf.fit(X_train_poly, labels[:i])
        prob = float(clf.predict_proba(X_test_poly)[0, 1])
        pred_up = prob >= 0.5
        actual_up = labels[i] == 1
        hits += int(pred_up == actual_up)
        total += 1
    if total == 0:
        return None
    return hits / total


def _train_direction_head(
    X_poly: np.ndarray,
    y: np.ndarray,
    poly_names: list[str],
) -> tuple[dict[str, float], float]:
    _, LogisticRegression, _, _, _, _ = _require_sklearn()
    labels = (y > 0).astype(int)
    if len(set(labels.tolist())) < 2:
        return {}, 0.0
    clf = LogisticRegression(C=0.5, max_iter=1000)
    clf.fit(X_poly, labels)
    coefficients = {
        str(name): float(coef)
        for name, coef in zip(poly_names, clf.coef_.flatten(), strict=False)
        if abs(coef) > 1e-9
    }
    return coefficients, float(clf.intercept_[0])


def _predict_direction_probability(
    macro_factors: dict[str, Any],
    artifact: ModelArtifact,
) -> float | None:
    if not artifact.direction_coefficients or not artifact.feature_names:
        return None
    values: list[float] = []
    for name in artifact.feature_names:
        raw = macro_factors.get(name, 0.0)
        try:
            values.append(float(raw))
        except (TypeError, ValueError):
            values.append(0.0)
    raw = np.array(values, dtype=float).reshape(1, -1)
    if artifact.feature_means and artifact.feature_stds:
        raw = _scale_features(raw, artifact.feature_means, artifact.feature_stds)
    expanded, poly_names = _expand_poly(raw, artifact.feature_names, artifact.poly_degree)
    coefs = np.array(
        [artifact.direction_coefficients.get(name, 0.0) for name in poly_names],
        dtype=float,
    )
    logit = artifact.direction_intercept + float(np.dot(expanded.flatten(), coefs))
    return float(1.0 / (1.0 + np.exp(-logit)))


def train_macro_ridge(
    history_df: pd.DataFrame,
    horizon: HorizonProfile,
    *,
    force_include_keys: tuple[str, ...] | None = None,
) -> ModelArtifact:
    """Train Ridge on polynomial macro features; return artifact for inference."""
    Ridge, _, mean_absolute_error, r2_score, PolynomialFeatures, _ = _require_sklearn()

    X, y, feature_names = build_factor_matrix(
        history_df,
        horizon,
        force_include_keys=force_include_keys,
    )
    if X.size == 0 or len(y) < 3:
        return ModelArtifact(
            poly_degree=horizon.poly_degree,
            feature_names=feature_names,
            trained_at=datetime.now(timezone.utc).isoformat(),
            horizon_name=horizon.name,
        )

    feature_means, feature_stds = _fit_feature_scaler(X)
    X_scaled = _scale_features(X, feature_means, feature_stds)
    poly = PolynomialFeatures(
        degree=horizon.poly_degree,
        interaction_only=True,
        include_bias=False,
    )
    X_poly = poly.fit_transform(X_scaled)
    model = Ridge(alpha=_RIDGE_ALPHA)
    model.fit(X_poly, y)

    oos_mae, oos_r2 = _walk_forward_metrics(X, y, poly_degree=horizon.poly_degree)
    direction_hit = _walk_forward_direction_hit_rate(X, y, poly_degree=horizon.poly_degree)
    mae = oos_mae if oos_mae is not None else float(mean_absolute_error(y, model.predict(X_poly)))
    r2 = oos_r2 if oos_r2 is not None else (float(r2_score(y, model.predict(X_poly))) if len(y) > 1 else None)

    poly_names = [str(name) for name in poly.get_feature_names_out(feature_names)]
    coefficients = {
        str(name): float(coef)
        for name, coef in zip(poly_names, model.coef_, strict=False)
        if abs(coef) > 1e-9
    }
    direction_coefficients, direction_intercept = _train_direction_head(X_poly, y, poly_names)

    return ModelArtifact(
        coefficients=coefficients,
        intercept=float(model.intercept_),
        mae=mae,
        r2_walk_forward=r2,
        poly_degree=horizon.poly_degree,
        feature_names=feature_names,
        trained_at=datetime.now(timezone.utc).isoformat(),
        horizon_name=horizon.name,
        direction_coefficients=direction_coefficients,
        direction_intercept=direction_intercept,
        direction_hit_rate_oos=direction_hit,
        feature_means=[float(v) for v in feature_means],
        feature_stds=[float(v) for v in feature_stds],
    )


def store_model_artifact(artifact: ModelArtifact) -> None:
    """Persist model artifact to the factor store."""
    save_model_artifact(artifact.to_dict())


def load_stored_model_artifact() -> ModelArtifact | None:
    """Load model artifact from the factor store."""
    return ModelArtifact.from_dict(load_model_artifact())



def detect_sign_conflict(
    raw_macro: float,
    scenario_anchor_return_pct: float | None,
    *,
    threshold_pct: float = _MACRO_SHRINK_THRESHOLD_PCT,
) -> bool:
    """True when gated macro and scenario anchor disagree on sign with material magnitude."""
    if scenario_anchor_return_pct is None:
        return False
    if abs(raw_macro) <= threshold_pct:
        return False
    return raw_macro * scenario_anchor_return_pct < 0


def apply_sign_conflict_gate(
    *,
    direction_view: str | None,
    direction_confidence: float | None,
    raw_macro: float,
    scenario_anchor_return_pct: float | None,
    regime_label: str,
    wf_metrics: dict[str, Any],
) -> tuple[str | None, float | None, bool]:
    """Neutralize direction and halve confidence when macro vs scenario conflict."""
    if not detect_sign_conflict(raw_macro, scenario_anchor_return_pct):
        return direction_view, direction_confidence, False

    conf = direction_confidence * 0.5 if direction_confidence is not None else None
    return "neutral", conf, True


def finalize_index_prediction(
    prediction: dict[str, Any],
    *,
    spot: float,
    mae_pct: float = 1.5,
    macro_factors: dict[str, Any] | None = None,
    scenario_anchor_return_pct: float | None = None,
) -> dict[str, Any]:
    """Sync headline view/range and re-apply sign-conflict gate after reconcile or debate."""
    if not prediction or spot <= 0:
        return prediction

    from trade_integrations.dataflows.index_research.direction_calibration import (
        load_walk_forward_accuracy,
    )
    from trade_integrations.dataflows.index_research.regime_gates import resolve_regime_label

    updated = dict(prediction)
    expected = float(updated.get("expected_return_pct") or 0.0)
    updated["view"] = classify_index_view(expected)

    anchor = scenario_anchor_return_pct
    if anchor is None and updated.get("scenario_anchor_return_pct") is not None:
        try:
            anchor = float(updated["scenario_anchor_return_pct"])
        except (TypeError, ValueError):
            anchor = None
    if anchor is not None:
        updated["scenario_anchor_return_pct"] = anchor

    raw_macro = float(
        updated.get("ridge_raw_macro_delta_pct")
        or updated.get("raw_macro_delta_pct")
        or updated.get("macro_delta_pct")
        or 0.0
    )
    wf_metrics = load_walk_forward_accuracy()
    regime_label = resolve_regime_label(macro_factors or {})

    direction_view, direction_conf, sign_conflict = apply_sign_conflict_gate(
        direction_view=updated.get("direction_view") or updated["view"],
        direction_confidence=updated.get("direction_confidence"),
        raw_macro=raw_macro,
        scenario_anchor_return_pct=anchor,
        regime_label=regime_label,
        wf_metrics=wf_metrics,
    )
    updated["direction_view"] = direction_view
    updated["direction_confidence"] = direction_conf
    updated["sign_conflict"] = sign_conflict
    if not sign_conflict:
        updated["direction_view"] = updated["view"]

    range_block = dict(updated.get("range") or {})
    updated["range"] = {
        **range_block,
        "low": spot * (1 + expected / 100 - mae_pct / 100),
        "high": spot * (1 + expected / 100 + mae_pct / 100),
    }
    return updated


def _predict_log(
    pipeline: PipelineLogger | None,
    message: str,
    **detail: Any,
) -> None:
    if pipeline is not None:
        pipeline.info("predict", message, **detail)


def predict_nifty(
    spot: float,
    signals: list[ConstituentSignal],
    macro_factors: dict[str, Any],
    horizon: HorizonProfile,
    *,
    model_artifact: ModelArtifact | None = None,
    scenario_anchor_return_pct: float | None = None,
    as_of_day: str | None = None,
    macro_trust_multiplier: float = 1.0,
    apply_event_overlay: bool = True,
    pipeline: PipelineLogger | None = None,
) -> dict[str, Any]:
    """Hybrid forecast: bottom-up constituent attribution + macro Ridge delta."""
    from trade_integrations.dataflows.index_research.event_overlay import enrich_macro_with_news_features
    from trade_integrations.dataflows.company_research.market import india_trading_date_iso

    as_of = (as_of_day or india_trading_date_iso())[:10]
    _predict_log(pipeline, "Enriching macro factors with news event features…")
    macro_factors = enrich_macro_with_news_features(macro_factors, as_of_day=as_of)

    _predict_log(pipeline, "Loading hybrid model artifact…")
    artifact = model_artifact or load_stored_model_artifact()
    _predict_log(pipeline, "Attributing bottom-up constituent contributions…")
    attributed = attribute_constituents(
        signals,
        horizon_days=horizon.days,
        pipeline=pipeline,
    )
    rollup = rollup_attribution(attributed)
    bottom_up = float(rollup["total_contribution_pct"])
    _predict_log(
        pipeline,
        f"Bottom-up rollup: {bottom_up:+.2f}%",
        bottom_up_return_pct=bottom_up,
    )

    _predict_log(pipeline, "Computing macro Ridge delta…")
    raw_macro = _predict_macro_delta(
        macro_factors,
        horizon,
        artifact,
        macro_trust_multiplier=macro_trust_multiplier,
    )
    from trade_integrations.dataflows.index_research.regime_gates import predict_macro_delta_gated

    gated_raw = predict_macro_delta_gated(
        macro_factors,
        horizon,
        artifact,
        macro_trust_multiplier=macro_trust_multiplier,
    )
    if abs(gated_raw) > 1e-9:
        raw_macro = gated_raw
    _predict_log(
        pipeline,
        f"Macro delta (post regime gates): {raw_macro:+.2f}%",
        macro_delta_pct=raw_macro,
    )

    from trade_integrations.dataflows.index_research.event_overlay import (
        compute_event_overlay,
        merge_overlay_into_macro,
    )

    if apply_event_overlay:
        raw_with_overlay, event_overlay = merge_overlay_into_macro(
            raw_macro,
            macro_factors,
            as_of_day=as_of,
        )
        macro_for_shrink = raw_with_overlay
    else:
        event_overlay = compute_event_overlay(
            macro_factors,
            as_of_day=as_of,
        )
        macro_for_shrink = raw_macro
    _predict_log(
        pipeline,
        "Applying event overlay and macro shrink toward scenarios…",
        event_overlay_pct=event_overlay.get("return_pct"),
    )
    macro_delta = shrink_macro_delta(macro_for_shrink, scenario_anchor_return_pct)
    expected_return_pct = bottom_up + macro_delta
    mae = artifact.mae if artifact else _DEFAULT_MAE_PCT

    range_low = spot * (1 + expected_return_pct / 100 - mae / 100)
    range_high = spot * (1 + expected_return_pct / 100 + mae / 100)

    coefficients = artifact.coefficients if artifact else {}
    intercept = artifact.intercept if artifact else 0.0
    r2 = artifact.r2_walk_forward if artifact else None

    direction_prob_raw = _predict_direction_probability(macro_factors, artifact) if artifact else None
    _predict_log(pipeline, "Scoring direction head and calibrating confidence…")
    from trade_integrations.dataflows.index_research.direction_calibration import (
        calibrate_direction_confidence,
        load_walk_forward_accuracy,
    )
    from trade_integrations.dataflows.index_research.regime_gates import resolve_regime_label

    wf_metrics = load_walk_forward_accuracy()
    regime_label = resolve_regime_label(macro_factors)
    direction_prob = (
        calibrate_direction_confidence(direction_prob_raw, regime_label, wf_metrics)
        if direction_prob_raw is not None
        else None
    )
    walk_forward_hit = wf_metrics.get("direction_hit_rate_walk_forward")
    direction_view = None
    if direction_prob is not None:
        if direction_prob >= _DIRECTION_PROB_BULL:
            direction_view = "bullish"
        elif direction_prob <= _DIRECTION_PROB_BEAR:
            direction_view = "bearish"
        else:
            direction_view = "neutral"

    direction_view, direction_prob, sign_conflict = apply_sign_conflict_gate(
        direction_view=direction_view,
        direction_confidence=direction_prob,
        raw_macro=raw_macro,
        scenario_anchor_return_pct=scenario_anchor_return_pct,
        regime_label=regime_label,
        wf_metrics=wf_metrics,
    )

    return {
        "view": classify_index_view(expected_return_pct),
        "expected_return_pct": expected_return_pct,
        "bottom_up_return_pct": bottom_up,
        "macro_delta_pct": macro_delta,
        "raw_macro_delta_pct": round(raw_macro, 4),
        "event_overlay_pct": event_overlay.get("return_pct"),
        "event_overlay": event_overlay,
        "direction_view": direction_view,
        "direction_confidence": direction_prob,
        "direction_confidence_raw": direction_prob_raw,
        "direction_model_score": direction_prob_raw,
        "sign_conflict": sign_conflict,
        "direction_hit_rate_oos": walk_forward_hit,
        "direction_hit_rate_walk_forward": walk_forward_hit,
        "direction_eval_count": wf_metrics.get("eval_count"),
        "range": {
            "low": range_low,
            "high": range_high,
            "confidence": min(0.95, max(0.35, (r2 or 0.35))),
        },
        "equation": {
            "form": "delta_nifty = sum(w_i * f_i) + beta · poly(X_macro)",
            "coefficients": coefficients,
            "intercept": intercept,
            "r2_walk_forward": r2,
            "direction_coefficients": artifact.direction_coefficients if artifact else {},
            "direction_intercept": artifact.direction_intercept if artifact else 0.0,
            "direction_hit_rate_oos": walk_forward_hit,
        },
        "horizon": {"name": horizon.name, "days": horizon.days},
    }
