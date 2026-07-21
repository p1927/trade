"""Sklearn Pipeline for macro Ridge — scaling, poly, nested walk-forward tuning."""

from __future__ import annotations

from typing import Any

import numpy as np

from trade_integrations.dataflows.index_research.horizon import HorizonProfile

_RIDGE_ALPHA_GRID = (50.0, 100.0, 200.0, 500.0)
_RIDGE_SOLVER = "lsqr"
_MIN_WALK_FORWARD_TRAIN = 15
_DIRECTION_LOGISTIC_C = 0.5
_DIRECTION_LOGISTIC_MAX_ITER = 2000
_DIRECTION_LOGISTIC_TOL = 1e-4
_HIGH_CORR_THRESHOLD = 0.85


def _require_sklearn():
    try:
        from sklearn.linear_model import LogisticRegression, Ridge
        from sklearn.metrics import mean_absolute_error, r2_score
        from sklearn.pipeline import Pipeline
        from sklearn.preprocessing import PolynomialFeatures, StandardScaler
    except ImportError as exc:
        raise ImportError("scikit-learn is required for index predictor training") from exc
    return (
        Ridge,
        LogisticRegression,
        mean_absolute_error,
        r2_score,
        PolynomialFeatures,
        StandardScaler,
        Pipeline,
    )


def poly_degree_candidates(horizon: HorizonProfile) -> tuple[int, ...]:
    """Horizon B may use degree 1 or 2; others use profile default only."""
    if horizon.name == "B":
        return (1, 2)
    return (horizon.poly_degree,)


def make_ridge_pipeline(*, alpha: float, poly_degree: int) -> Any:
    """StandardScaler → PolynomialFeatures → Ridge."""
    Ridge, _, _, _, PolynomialFeatures, StandardScaler, Pipeline = _require_sklearn()
    return Pipeline(
        [
            ("scaler", StandardScaler()),
            (
                "poly",
                PolynomialFeatures(
                    degree=poly_degree,
                    interaction_only=True,
                    include_bias=False,
                ),
            ),
            ("ridge", Ridge(alpha=alpha, solver=_RIDGE_SOLVER)),
        ]
    )


def _direction_logistic_c(*, n_samples: int, n_features: int) -> float:
    if n_features <= n_samples:
        return _DIRECTION_LOGISTIC_C
    ratio = max(n_samples / n_features, 0.01)
    return _DIRECTION_LOGISTIC_C * ratio


def make_direction_pipeline(*, poly_degree: int, n_samples: int, n_features: int) -> Any:
    """Parallel pipeline for binary direction head."""
    _, LogisticRegression, _, _, PolynomialFeatures, StandardScaler, Pipeline = _require_sklearn()
    use_dual = n_features >= n_samples
    return Pipeline(
        [
            ("scaler", StandardScaler()),
            (
                "poly",
                PolynomialFeatures(
                    degree=poly_degree,
                    interaction_only=True,
                    include_bias=False,
                ),
            ),
            (
                "clf",
                LogisticRegression(
                    C=_direction_logistic_c(n_samples=n_samples, n_features=n_features),
                    solver="liblinear",
                    dual=use_dual,
                    max_iter=_DIRECTION_LOGISTIC_MAX_ITER,
                    tol=_DIRECTION_LOGISTIC_TOL,
                ),
            ),
        ]
    )


def extract_scaler_params(pipeline: Any) -> tuple[list[float], list[float]]:
    scaler = pipeline.named_steps["scaler"]
    means = getattr(scaler, "mean_", None)
    scales = getattr(scaler, "scale_", None)
    if means is None or scales is None:
        return [], []
    safe_scales = np.where(np.asarray(scales) < 1e-9, 1.0, scales)
    return [float(v) for v in means], [float(v) for v in safe_scales]


def poly_feature_names(pipeline: Any, feature_names: list[str]) -> list[str]:
    poly = pipeline.named_steps["poly"]
    return [str(name) for name in poly.get_feature_names_out(feature_names)]


def pipeline_predict(pipeline: Any, X: np.ndarray) -> np.ndarray:
    sanitized = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
    return pipeline.predict(sanitized)


def _inner_walk_forward_mae(
    X: np.ndarray,
    y: np.ndarray,
    *,
    alpha: float,
    poly_degree: int,
) -> float | None:
    """Nested WF: MAE on last ~15% of train slice."""
    _, _, mean_absolute_error, _, _, _, _ = _require_sklearn()
    holdout = max(3, min(len(y) // 5, 15))
    start = len(y) - holdout
    if start < _MIN_WALK_FORWARD_TRAIN:
        return None

    oos_true: list[float] = []
    oos_pred: list[float] = []
    for i in range(start, len(y)):
        if i < _MIN_WALK_FORWARD_TRAIN:
            continue
        X_train = X[:i]
        X_test = X[i : i + 1]
        if not np.all(np.isfinite(X_train)) or not np.all(np.isfinite(y[:i])):
            continue
        if X_train.shape[0] < 2:
            continue
        pipe = make_ridge_pipeline(alpha=alpha, poly_degree=poly_degree)
        try:
            pipe.fit(X_train, y[:i])
            pred_val = float(pipeline_predict(pipe, X_test)[0])
        except (ValueError, FloatingPointError):
            continue
        if not np.isfinite(pred_val):
            continue
        oos_pred.append(pred_val)
        oos_true.append(float(y[i]))

    if len(oos_true) < 2:
        return None
    return float(mean_absolute_error(oos_true, oos_pred))


def select_ridge_hyperparams(
    X: np.ndarray,
    y: np.ndarray,
    horizon: HorizonProfile,
) -> tuple[float, int]:
    """Pick (alpha, poly_degree) by nested walk-forward MAE on train slice."""
    best_alpha = _RIDGE_ALPHA_GRID[0]
    best_degree = horizon.poly_degree
    best_mae: float | None = None

    for poly_degree in poly_degree_candidates(horizon):
        for alpha in _RIDGE_ALPHA_GRID:
            mae = _inner_walk_forward_mae(X, y, alpha=alpha, poly_degree=poly_degree)
            if mae is None:
                continue
            if best_mae is None or mae < best_mae:
                best_mae = mae
                best_alpha = alpha
                best_degree = poly_degree

    return best_alpha, best_degree


def train_holdout_metrics(
    X: np.ndarray,
    y: np.ndarray,
    *,
    alpha: float,
    poly_degree: int,
) -> tuple[float | None, float | None, float | None]:
    """Diagnostic holdout on train tail — not canonical OOS."""
    Ridge, _, mean_absolute_error, r2_score, _, _, _ = _require_sklearn()
    holdout = max(3, min(len(y) // 5, 15))
    start = len(y) - holdout
    if start < _MIN_WALK_FORWARD_TRAIN:
        return None, None, None

    oos_true: list[float] = []
    oos_pred: list[float] = []
    hits = 0
    total = 0
    labels = (y > 0).astype(int)

    for i in range(start, len(y)):
        if i < _MIN_WALK_FORWARD_TRAIN:
            continue
        X_train = X[:i]
        X_test = X[i : i + 1]
        if not np.all(np.isfinite(X_train)) or not np.all(np.isfinite(y[:i])):
            continue
        if X_train.shape[0] < 2:
            continue
        pipe = make_ridge_pipeline(alpha=alpha, poly_degree=poly_degree)
        try:
            pipe.fit(X_train, y[:i])
            pred_val = float(pipeline_predict(pipe, X_test)[0])
        except (ValueError, FloatingPointError):
            continue
        if not np.isfinite(pred_val):
            continue
        oos_pred.append(pred_val)
        oos_true.append(float(y[i]))
        hits += int((pred_val > 0) == (labels[i] == 1))
        total += 1

    if len(oos_true) < 2:
        return None, None, None
    direction_hit = (hits / total) if total else None
    return (
        float(mean_absolute_error(oos_true, oos_pred)),
        float(r2_score(oos_true, oos_pred)),
        direction_hit,
    )


def fit_ridge_artifact_components(
    X: np.ndarray,
    y: np.ndarray,
    feature_names: list[str],
    horizon: HorizonProfile,
) -> dict[str, Any]:
    """Train final pipeline + direction head; return serializable components."""
    Ridge, _, mean_absolute_error, r2_score, _, _, _ = _require_sklearn()

    alpha, poly_degree = select_ridge_hyperparams(X, y, horizon)
    ridge_pipe = make_ridge_pipeline(alpha=alpha, poly_degree=poly_degree)
    ridge_pipe.fit(X, y)

    poly_names = poly_feature_names(ridge_pipe, feature_names)
    ridge_model = ridge_pipe.named_steps["ridge"]
    coefficients = {
        str(name): float(coef)
        for name, coef in zip(poly_names, ridge_model.coef_, strict=False)
        if abs(coef) > 1e-9
    }

    train_mae, train_r2, train_direction = train_holdout_metrics(
        X, y, alpha=alpha, poly_degree=poly_degree
    )
    in_sample_mae = float(mean_absolute_error(y, pipeline_predict(ridge_pipe, X)))
    mae = train_mae if train_mae is not None else in_sample_mae
    r2 = train_r2 if train_r2 is not None else (
        float(r2_score(y, pipeline_predict(ridge_pipe, X))) if len(y) > 1 else None
    )

    labels = (y > 0).astype(int)
    direction_coefficients: dict[str, float] = {}
    direction_intercept = 0.0
    if len(set(labels.tolist())) >= 2:
        dir_pipe = make_direction_pipeline(
            poly_degree=poly_degree,
            n_samples=len(y),
            n_features=len(poly_names),
        )
        dir_pipe.fit(X, labels)
        clf = dir_pipe.named_steps["clf"]
        direction_coefficients = {
            str(name): float(coef)
            for name, coef in zip(poly_names, clf.coef_.flatten(), strict=False)
            if abs(coef) > 1e-9
        }
        direction_intercept = float(clf.intercept_[0])

    means, stds = extract_scaler_params(ridge_pipe)
    multicollinearity = compute_multicollinearity_diagnostics(X, feature_names)

    return {
        "coefficients": coefficients,
        "intercept": float(ridge_model.intercept_),
        "mae": mae,
        "r2_walk_forward": r2,
        "poly_degree": poly_degree,
        "ridge_alpha": alpha,
        "feature_names": feature_names,
        "direction_coefficients": direction_coefficients,
        "direction_intercept": direction_intercept,
        "direction_hit_rate_train_holdout": train_direction,
        "feature_means": means,
        "feature_stds": stds,
        "multicollinearity_warning": multicollinearity.get("multicollinearity_warning", False),
        "correlated_pairs": multicollinearity.get("correlated_pairs", []),
        "vif_scores": multicollinearity.get("vif_scores", []),
    }


def compute_multicollinearity_diagnostics(
    X: np.ndarray,
    feature_names: list[str],
) -> dict[str, Any]:
    """VIF approximations and high-|r| pairs among selected features."""
    if X.size == 0 or len(feature_names) < 2:
        return {
            "multicollinearity_warning": False,
            "correlated_pairs": [],
            "vif_scores": [],
        }

    frame = np.nan_to_num(X, nan=0.0)
    n_features = frame.shape[1]
    correlated_pairs: list[dict[str, Any]] = []
    for i in range(n_features):
        for j in range(i + 1, n_features):
            a = frame[:, i]
            b = frame[:, j]
            if float(np.std(a)) < 1e-12 or float(np.std(b)) < 1e-12:
                continue
            corr = float(np.corrcoef(a, b)[0, 1])
            if abs(corr) >= 0.7:
                correlated_pairs.append(
                    {
                        "factor_a": feature_names[i],
                        "factor_b": feature_names[j],
                        "correlation": round(corr, 4),
                    }
                )

    correlated_pairs.sort(key=lambda row: abs(row["correlation"]), reverse=True)
    vif_scores: list[dict[str, Any]] = []
    try:
        from statsmodels.stats.outliers_influence import variance_inflation_factor

        for idx, name in enumerate(feature_names):
            if frame.shape[0] <= n_features:
                break
            vif = float(variance_inflation_factor(frame, idx))
            if np.isfinite(vif):
                vif_scores.append({"factor": name, "vif": round(vif, 2)})
    except Exception:
        pass

    warning = bool(
        any(abs(p["correlation"]) >= _HIGH_CORR_THRESHOLD for p in correlated_pairs)
        or any(v.get("vif", 0) >= 10 for v in vif_scores)
    )
    return {
        "multicollinearity_warning": warning,
        "correlated_pairs": correlated_pairs[:10],
        "vif_scores": sorted(vif_scores, key=lambda r: r.get("vif", 0), reverse=True)[:10],
    }


def predict_from_artifact_row(
    values: np.ndarray,
    *,
    feature_names: list[str],
    poly_degree: int,
    ridge_alpha: float,
    coefficients: dict[str, float],
    intercept: float,
    feature_means: list[float],
    feature_stds: list[float],
) -> float:
    """Predict uncapped macro delta using persisted artifact params."""
    raw = values.reshape(1, -1)
    pipe = make_ridge_pipeline(alpha=ridge_alpha, poly_degree=poly_degree)
    scaler = pipe.named_steps["scaler"]
    poly = pipe.named_steps["poly"]
    if feature_means and feature_stds:
        scaler.mean_ = np.asarray(feature_means, dtype=float)
        scaler.scale_ = np.asarray(feature_stds, dtype=float)
        scaler.var_ = scaler.scale_ ** 2
        scaler.n_features_in_ = len(feature_means)
    else:
        sanitized = np.nan_to_num(raw, nan=0.0)
        scaler.fit(sanitized)
    poly.fit(scaler.transform(np.nan_to_num(raw, nan=0.0)))
    poly_names = poly_feature_names(pipe, feature_names)
    expanded = poly.transform(scaler.transform(np.nan_to_num(raw, nan=0.0)))
    coefs = np.array([coefficients.get(name, 0.0) for name in poly_names], dtype=float)
    return float(intercept + np.dot(expanded.flatten(), coefs))
