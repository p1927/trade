"""Rolling OLS VAR(1) estimation — numpy only, no statsmodels dependency."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from trade_integrations.dataflows.index_research.cascade.constants import (
    MIN_VAR_OBSERVATIONS,
    VAR_FACTOR_KEYS,
)


@dataclass(frozen=True)
class VarFitResult:
    """Fitted VAR(1) system in transformed (stationary) units."""

    factors: tuple[str, ...]
    intercept: np.ndarray
    coef: np.ndarray  # shape (K, K) — Y_t = intercept + coef @ Y_{t-1} + e
    residual_cov: np.ndarray
    n_obs: int
    transform: str = "mixed_returns"


def _transform_series(frame: pd.DataFrame, factor: str) -> pd.Series:
    series = pd.to_numeric(frame[factor], errors="coerce")
    if factor in {"india_vix", "fii_net_5d", "dii_net_5d", "repo_rate"}:
        return series.diff()
    return series.pct_change(fill_method=None) * 100.0


def prepare_var_matrix(
    aligned: pd.DataFrame,
    *,
    factors: tuple[str, ...] = VAR_FACTOR_KEYS,
) -> pd.DataFrame:
    """Build stationary transformed matrix for VAR estimation."""
    cols: dict[str, pd.Series] = {}
    for factor in factors:
        if factor not in aligned.columns:
            continue
        cols[factor] = _transform_series(aligned, factor)
    if not cols:
        return pd.DataFrame()
    matrix = pd.DataFrame(cols).replace([np.inf, -np.inf], np.nan).dropna()
    return matrix


def fit_var1(matrix: pd.DataFrame) -> VarFitResult | None:
    """Estimate VAR(1) via equation-by-equation OLS."""
    if matrix.empty or len(matrix) < MIN_VAR_OBSERVATIONS:
        return None

    factors = tuple(matrix.columns.tolist())
    y = matrix.to_numpy(dtype=float)
    n, k = y.shape
    y_lag = y[:-1]
    y_now = y[1:]
    x = np.column_stack([np.ones(len(y_lag)), y_lag])

    intercept = np.zeros(k)
    coef = np.zeros((k, k))
    residuals = np.zeros_like(y_now)

    for i in range(k):
        beta, *_ = np.linalg.lstsq(x, y_now[:, i], rcond=None)
        intercept[i] = beta[0]
        coef[i, :] = beta[1:]
        residuals[:, i] = y_now[:, i] - x @ beta

    if residuals.shape[0] < 2:
        return None

    cov = np.cov(residuals, rowvar=False)
    if cov.ndim == 0:
        cov = np.array([[float(cov)]])

    return VarFitResult(
        factors=factors,
        intercept=intercept,
        coef=coef,
        residual_cov=cov,
        n_obs=n - 1,
    )


def impulse_response(
    fit: VarFitResult,
    *,
    shock_factor: str,
    shock_size: float = 1.0,
    horizon: int = 5,
) -> dict[str, list[float]]:
    """Compute orthogonalized IRF paths for a one-unit shock in transformed space."""
    if shock_factor not in fit.factors:
        return {}

    k = len(fit.factors)
    idx = fit.factors.index(shock_factor)
    response = np.zeros(k)
    response[idx] = shock_size

    paths: dict[str, list[float]] = {f: [] for f in fit.factors}
    current = response.copy()
    for _ in range(max(1, horizon)):
        for j, factor in enumerate(fit.factors):
            paths[factor].append(float(current[j]))
        current = fit.coef @ current

    return paths
