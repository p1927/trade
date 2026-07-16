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

_DEFAULT_MAE_PCT = 1.5
_MACRO_DELTA_CAP_PCT = 5.0
_RIDGE_ALPHA = 50.0
_MACRO_TRUST_MAE_GOOD = 1.5
_MACRO_TRUST_MAE_POOR = 7.0
_VIEW_BULL_THRESHOLD = 0.3
_VIEW_BEAR_THRESHOLD = -0.3
_DIRECTION_PROB_BULL = 0.55
_DIRECTION_PROB_BEAR = 0.45
_MIN_WALK_FORWARD_TRAIN = 15


def cap_macro_delta(macro_delta: float) -> float:
    """Clamp raw Ridge macro delta to a plausible 14d move."""
    return max(-_MACRO_DELTA_CAP_PCT, min(_MACRO_DELTA_CAP_PCT, macro_delta))


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
        return X
    return (X - mean_arr) / std_arr


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
    expanded = poly.transform(raw)
    names = [str(name) for name in poly.get_feature_names_out(feature_names)]
    return expanded, names


def _predict_macro_delta(
    macro_factors: dict[str, Any],
    horizon: HorizonProfile,
    artifact: ModelArtifact | None,
) -> float:
    if artifact is None or not artifact.feature_names:
        return 0.0

    values: list[float] = []
    for name in artifact.feature_names:
        raw = macro_factors.get(name, 0.0)
        if raw is None or isinstance(raw, (dict, list, tuple, set)):
            values.append(0.0)
            continue
        try:
            values.append(float(raw))
        except (TypeError, ValueError):
            values.append(0.0)
    raw = np.array(values, dtype=float).reshape(1, -1)
    if artifact.feature_means and artifact.feature_stds:
        raw = _scale_features(raw, artifact.feature_means, artifact.feature_stds)
    expanded, poly_names = _expand_poly(raw, artifact.feature_names, artifact.poly_degree)
    coefs = np.array([artifact.coefficients.get(name, 0.0) for name in poly_names], dtype=float)
    trust = _macro_trust_weight(float(artifact.mae or _DEFAULT_MAE_PCT))
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
) -> ModelArtifact:
    """Train Ridge on polynomial macro features; return artifact for inference."""
    Ridge, _, mean_absolute_error, r2_score, PolynomialFeatures, _ = _require_sklearn()

    X, y, feature_names = build_factor_matrix(history_df, horizon)
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



def _classify_view(expected_return_pct: float) -> str:
    if expected_return_pct >= _VIEW_BULL_THRESHOLD:
        return "bullish"
    if expected_return_pct <= _VIEW_BEAR_THRESHOLD:
        return "bearish"
    return "neutral"


def predict_nifty(
    spot: float,
    signals: list[ConstituentSignal],
    macro_factors: dict[str, Any],
    horizon: HorizonProfile,
    *,
    model_artifact: ModelArtifact | None = None,
) -> dict[str, Any]:
    """Hybrid forecast: bottom-up constituent attribution + macro Ridge delta."""
    artifact = model_artifact or load_stored_model_artifact()
    attributed = attribute_constituents(signals, horizon_days=horizon.days)
    rollup = rollup_attribution(attributed)
    bottom_up = float(rollup["total_contribution_pct"])

    macro_delta = _predict_macro_delta(macro_factors, horizon, artifact)
    macro_delta = cap_macro_delta(macro_delta)
    expected_return_pct = bottom_up + macro_delta
    mae = artifact.mae if artifact else _DEFAULT_MAE_PCT

    range_low = spot * (1 + expected_return_pct / 100 - mae / 100)
    range_high = spot * (1 + expected_return_pct / 100 + mae / 100)

    coefficients = artifact.coefficients if artifact else {}
    intercept = artifact.intercept if artifact else 0.0
    r2 = artifact.r2_walk_forward if artifact else None

    direction_prob = _predict_direction_probability(macro_factors, artifact) if artifact else None
    direction_view = None
    if direction_prob is not None:
        if direction_prob >= _DIRECTION_PROB_BULL:
            direction_view = "bullish"
        elif direction_prob <= _DIRECTION_PROB_BEAR:
            direction_view = "bearish"
        else:
            direction_view = "neutral"

    return {
        "view": _classify_view(expected_return_pct),
        "expected_return_pct": expected_return_pct,
        "bottom_up_return_pct": bottom_up,
        "macro_delta_pct": macro_delta,
        "direction_view": direction_view,
        "direction_confidence": direction_prob,
        "direction_hit_rate_oos": artifact.direction_hit_rate_oos if artifact else None,
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
        },
        "horizon": {"name": horizon.name, "days": horizon.days},
    }
