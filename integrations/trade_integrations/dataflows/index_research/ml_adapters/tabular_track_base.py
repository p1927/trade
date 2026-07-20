"""Shared tabular ML train/predict for LightGBM and XGBoost tracks."""

from __future__ import annotations

from typing import Any, Callable

import numpy as np
import pandas as pd

from trade_integrations.dataflows.index_research.backtest_runner import _forward_return_pct
from trade_integrations.dataflows.index_research.event_overlay import enrich_macro_with_news_features
from trade_integrations.dataflows.index_research.horizon import HorizonProfile
from trade_integrations.dataflows.index_research.predictor import ModelArtifact
from trade_integrations.dataflows.index_research.sources.history_loader import load_aligned_factor_history

_MIN_TRAIN_ROWS = 120
_SKIP_COLS = frozenset({"date", "close"})


def _finite_float(raw: Any, default: float = 0.0) -> float:
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return default
    if val != val or val in (float("inf"), float("-inf")):
        return default
    return val


def artifact_feature_row(macro_factors: dict, feature_names: list[str]) -> list[float]:
    values: list[float] = []
    for name in feature_names:
        raw = macro_factors.get(name, 0.0)
        if raw is None or isinstance(raw, (dict, list, tuple, set)):
            values.append(0.0)
            continue
        values.append(_finite_float(raw, 0.0))
    return values


def as_float_matrix(rows_x: list[list[float]]) -> np.ndarray:
    """LightGBM 4.x requires ndarray input — plain nested lists fail in lgb.train."""
    if not rows_x:
        return np.empty((0, 0), dtype=np.float64)
    return np.asarray(rows_x, dtype=np.float64)


def as_float_vector(rows_y: list[float]) -> np.ndarray:
    return np.asarray(rows_y, dtype=np.float64)


def row_factor_dict(row: pd.Series, *, skip: frozenset[str] | None = None) -> dict[str, float]:
    skip_cols = skip or _SKIP_COLS
    out: dict[str, float] = {}
    for col in row.index:
        if col in skip_cols:
            continue
        val = row[col]
        if val is None or (hasattr(val, "__float__") and val != val):
            continue
        try:
            out[str(col)] = float(val)
        except (TypeError, ValueError):
            continue
    return out


def build_training_matrix(
    artifact: ModelArtifact,
    horizon: HorizonProfile,
    *,
    as_of_day: str | None = None,
    min_train_rows: int = _MIN_TRAIN_ROWS,
) -> tuple[list[list[float]], list[float], list[str], list[int]]:
    panel = load_aligned_factor_history(days=400)
    if panel is None or panel.empty or "close" not in panel.columns:
        raise ValueError("insufficient_panel_rows:0")

    feature_names = list(artifact.feature_names)
    horizon_days = int(horizon.days)
    closes = panel["close"].astype(float)
    fwd = _forward_return_pct(closes, horizon_days)
    rows_x: list[list[float]] = []
    rows_y: list[float] = []
    panel_indices: list[int] = []

    for i in range(len(panel)):
        target = fwd.iloc[i]
        if target != target:
            continue
        factors = row_factor_dict(panel.iloc[i])
        rows_x.append(artifact_feature_row(factors, feature_names))
        rows_y.append(float(target))
        panel_indices.append(i)

    if len(rows_y) < min_train_rows:
        raise ValueError(f"insufficient_training_pairs:{len(rows_y)}")
    return rows_x, rows_y, feature_names, panel_indices


def regime_mask_from_panel(
    panel: pd.DataFrame,
    *,
    regime_key: str = "repo_rate_velocity_3d",
) -> np.ndarray | None:
    if regime_key not in panel.columns:
        return None
    velocity = pd.to_numeric(panel[regime_key], errors="coerce").fillna(0.0)
    return (velocity >= 0).values


def split_by_regime(
    rows_x: list[list[float]],
    rows_y: list[float],
    mask: np.ndarray | None,
) -> tuple[tuple[list, list], tuple[list, list]]:
    if mask is None or len(mask) != len(rows_y):
        return (rows_x, rows_y), (rows_x, rows_y)
    rising_x = [x for x, m in zip(rows_x, mask) if m]
    rising_y = [y for y, m in zip(rows_y, mask) if m]
    falling_x = [x for x, m in zip(rows_x, mask) if not m]
    falling_y = [y for y, m in zip(rows_y, mask) if not m]
    return (rising_x, rising_y), (falling_x, falling_y)


def predict_tabular_macro(
    macro_factors: dict,
    horizon: HorizonProfile,
    artifact: ModelArtifact,
    *,
    as_of_day: str | None,
    train_fn: Callable[..., Any],
    predict_fn: Callable[[Any, list[list[float]]], np.ndarray | list[float]],
    regime_key: str | None = "repo_rate_velocity_3d",
) -> tuple[float, dict]:
    rows_x, rows_y, feature_names, panel_indices = build_training_matrix(
        artifact, horizon, as_of_day=as_of_day
    )
    panel = load_aligned_factor_history(days=400)
    mask = regime_mask_from_panel(panel, regime_key=regime_key) if regime_key else None
    if mask is not None and panel_indices:
        mask = mask[np.asarray(panel_indices, dtype=int)]
    rising, falling = split_by_regime(rows_x, rows_y, mask)

    live_factors = enrich_macro_with_news_features(dict(macro_factors), as_of_day=as_of_day)
    live_vec = as_float_matrix([artifact_feature_row(live_factors, feature_names)])

    regime_label = "all"
    train_x, train_y = rows_x, rows_y
    if regime_key and mask is not None:
        live_val = _finite_float(live_factors.get(regime_key, 0.0), 0.0)
        if live_val >= 0 and len(rising[1]) >= _MIN_TRAIN_ROWS // 2:
            train_x, train_y = rising
            regime_label = "rising_rates"
        elif live_val < 0 and len(falling[1]) >= _MIN_TRAIN_ROWS // 2:
            train_x, train_y = falling
            regime_label = "falling_rates"

    model = train_fn(as_float_matrix(train_x), as_float_vector(train_y), feature_names)
    pred = float(predict_fn(model, live_vec)[0])
    return round(pred, 4), {
        "train_rows": len(train_y),
        "feature_count": len(feature_names),
        "horizon_days": int(horizon.days),
        "regime": regime_label,
    }
