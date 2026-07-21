"""Calibrate direction confidence from walk-forward backtest OOS (not in-sample holdout)."""

from __future__ import annotations

from typing import Any

import numpy as np


def load_walk_forward_accuracy(*, ticker: str = "NIFTY") -> dict[str, Any]:
    """Load spaced walk-forward metrics from backtest_latest.json."""
    try:
        from trade_integrations.dataflows.index_research.backtest_runner import load_backtest_report

        backtest = load_backtest_report(ticker) or {}
    except Exception:
        backtest = {}

    metrics = backtest.get("metrics") or {}
    walk_forward = metrics.get("direction_hit_rate_walk_forward") or metrics.get("direction_hit_rate")
    bootstrap_ci = metrics.get("direction_bootstrap_ci") or {}
    return {
        "direction_hit_rate_walk_forward": walk_forward,
        "regime_direction_hit_rates": metrics.get("regime_direction_hit_rates") or {},
        "eval_count": backtest.get("eval_count"),
        "mae_pct": metrics.get("mae_pct"),
        "direction_bootstrap_ci": bootstrap_ci,
        "insufficient_evidence": bool(bootstrap_ci.get("insufficient_evidence")),
        "sign_magnitude_score_mean": metrics.get("sign_magnitude_score_mean"),
        "eval_protocol": backtest.get("eval_protocol"),
    }


def regime_oos_hit_rate(metrics: dict[str, Any], regime_label: str) -> float | None:
    """Regime-bucket walk-forward direction hit rate, if available."""
    bucket = (metrics.get("regime_direction_hit_rates") or {}).get(regime_label) or {}
    rate = bucket.get("direction_hit_rate")
    if rate is None:
        return None
    try:
        value = float(rate)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(value):
        return None
    return value


def artifact_direction_hit_rate(metrics: dict[str, Any]) -> float | None:
    """Global walk-forward direction hit rate from backtest protocol only."""
    rate = metrics.get("direction_hit_rate_walk_forward")
    if rate is None:
        return None
    try:
        value = float(rate)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(value):
        return None
    return value


def calibrate_direction_confidence(
    raw_prob: float | None,
    regime_label: str,
    metrics: dict[str, Any],
) -> float:
    """
    Cap logistic distance from 0.5 using measured OOS hit rate.

    When walk-forward OOS ≈ 53%, calibrated confidence stays near 0.5–0.56,
    not raw logistic 0.99.
    """
    if raw_prob is None:
        return 0.5
    try:
        prob = float(raw_prob)
    except (TypeError, ValueError):
        return 0.5
    if not np.isfinite(prob):
        return 0.5

    regime_cap = regime_oos_hit_rate(metrics, regime_label)
    global_cap = artifact_direction_hit_rate(metrics)
    cap = regime_cap if regime_cap is not None else global_cap
    if cap is None:
        cap = 0.5

    cap = float(np.clip(cap, 0.5, 0.85))
    return platt_scale_probability(prob, hit_rate=cap)


def sync_artifact_direction_oos(artifact: Any, *, ticker: str = "NIFTY") -> float | None:
    """Overwrite artifact.direction_hit_rate_oos with backtest-protocol walk-forward rate."""
    metrics = load_walk_forward_accuracy(ticker=ticker)
    rate = artifact_direction_hit_rate(metrics)
    if rate is not None and hasattr(artifact, "direction_hit_rate_oos"):
        artifact.direction_hit_rate_oos = rate
    return rate


def platt_scale_probability(raw_prob: float, *, hit_rate: float | None) -> float:
    """Map raw logistic score toward OOS base rate (Platt-style shrinkage)."""
    if hit_rate is None:
        return raw_prob
    base = float(np.clip(hit_rate, 0.5, 0.85))
    distance = abs(raw_prob - 0.5)
    max_distance = max(0.02, base - 0.5)
    scaled = min(distance, max_distance)
    sign = 1.0 if raw_prob >= 0.5 else -1.0
    return float(np.clip(0.5 + sign * scaled, 0.15, 0.85))
