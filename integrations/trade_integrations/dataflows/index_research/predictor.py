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
_VIEW_BULL_THRESHOLD = 0.3
_VIEW_BEAR_THRESHOLD = -0.3


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
        )


def _require_sklearn():
    try:
        from sklearn.linear_model import Ridge
        from sklearn.metrics import mean_absolute_error, r2_score
        from sklearn.preprocessing import PolynomialFeatures
    except ImportError as exc:
        raise ImportError("scikit-learn is required for index predictor training") from exc
    return Ridge, mean_absolute_error, r2_score, PolynomialFeatures


def _expand_poly(raw: np.ndarray, feature_names: list[str], poly_degree: int) -> tuple[np.ndarray, list[str]]:
    _, _, _, PolynomialFeatures = _require_sklearn()
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
    expanded, poly_names = _expand_poly(raw, artifact.feature_names, artifact.poly_degree)
    coefs = np.array([artifact.coefficients.get(name, 0.0) for name in poly_names], dtype=float)
    return float(artifact.intercept + np.dot(expanded.flatten(), coefs))


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


def train_macro_ridge(
    history_df: pd.DataFrame,
    horizon: HorizonProfile,
) -> ModelArtifact:
    """Train Ridge on polynomial macro features; return artifact for inference."""
    Ridge, mean_absolute_error, r2_score, PolynomialFeatures = _require_sklearn()

    X, y, feature_names = build_factor_matrix(history_df, horizon)
    if X.size == 0 or len(y) < 3:
        return ModelArtifact(
            poly_degree=horizon.poly_degree,
            feature_names=feature_names,
            trained_at=datetime.now(timezone.utc).isoformat(),
            horizon_name=horizon.name,
        )

    poly = PolynomialFeatures(
        degree=horizon.poly_degree,
        interaction_only=True,
        include_bias=False,
    )
    X_poly = poly.fit_transform(X)
    model = Ridge(alpha=1.0)
    model.fit(X_poly, y)

    preds = model.predict(X_poly)
    mae = float(mean_absolute_error(y, preds))
    r2 = float(r2_score(y, preds)) if len(y) > 1 else None

    poly_names = poly.get_feature_names_out(feature_names)
    coefficients = {
        str(name): float(coef)
        for name, coef in zip(poly_names, model.coef_, strict=False)
        if abs(coef) > 1e-9
    }

    return ModelArtifact(
        coefficients=coefficients,
        intercept=float(model.intercept_),
        mae=mae,
        r2_walk_forward=r2,
        poly_degree=horizon.poly_degree,
        feature_names=feature_names,
        trained_at=datetime.now(timezone.utc).isoformat(),
        horizon_name=horizon.name,
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
    expected_return_pct = bottom_up + macro_delta
    mae = artifact.mae if artifact else _DEFAULT_MAE_PCT

    range_low = spot * (1 + expected_return_pct / 100 - mae / 100)
    range_high = spot * (1 + expected_return_pct / 100 + mae / 100)

    coefficients = artifact.coefficients if artifact else {}
    intercept = artifact.intercept if artifact else 0.0
    r2 = artifact.r2_walk_forward if artifact else None

    return {
        "view": _classify_view(expected_return_pct),
        "expected_return_pct": expected_return_pct,
        "bottom_up_return_pct": bottom_up,
        "macro_delta_pct": macro_delta,
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
        },
        "horizon": {"name": horizon.name, "days": horizon.days},
    }
