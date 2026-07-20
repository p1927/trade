"""Shared combiner math (numpy-free for v1 — stdlib only)."""

from __future__ import annotations

from typing import Any, Iterable

from trade_integrations.dataflows.index_research.prediction_algorithms.types import ForecastTrack
from trade_integrations.dataflows.index_research.views import classify_index_view

_EPS = 0.01


def available_tracks(tracks: dict[str, ForecastTrack], track_ids: Iterable[str]) -> list[ForecastTrack]:
    out: list[ForecastTrack] = []
    for tid in track_ids:
        row = tracks.get(tid)
        if row and row.available:
            out.append(row)
    return out


def equal_weight_combine(tracks: list[ForecastTrack]) -> tuple[float, dict[str, float]]:
    if not tracks:
        return 0.0, {}
    weights = {t.track_id: 1.0 / len(tracks) for t in tracks}
    value = sum(t.expected_return_pct * weights[t.track_id] for t in tracks)
    return value, weights


def inverse_mae_combine(
    tracks: list[ForecastTrack],
    mae_by_track: dict[str, float],
) -> tuple[float, dict[str, float]]:
    if not tracks:
        return 0.0, {}
    raw: dict[str, float] = {}
    for t in tracks:
        mae = max(_EPS, float(mae_by_track.get(t.track_id, _EPS)))
        raw[t.track_id] = 1.0 / mae
    total = sum(raw.values()) or 1.0
    weights = {k: v / total for k, v in raw.items()}
    value = sum(t.expected_return_pct * weights[t.track_id] for t in tracks)
    return value, weights


def shrink_weights(
    opt_weights: dict[str, float],
    equal_weights: dict[str, float],
    lam: float,
) -> dict[str, float]:
    keys = set(opt_weights) | set(equal_weights)
    blended = {k: lam * opt_weights.get(k, 0.0) + (1.0 - lam) * equal_weights.get(k, 0.0) for k in keys}
    total = sum(blended.values()) or 1.0
    return {k: v / total for k, v in blended.items()}


def alignment_combine(
    quant_return: float,
    scenario_return: float,
    lam: float,
) -> float:
    return lam * quant_return + (1.0 - lam) * scenario_return


def weighted_forecast(tracks: list[ForecastTrack], weights: dict[str, float]) -> float:
    return sum(t.expected_return_pct * weights.get(t.track_id, 0.0) for t in tracks)


def classify_combined(value: float) -> str:
    return classify_index_view(value)


def select_alignment_lambda(
    eval_rows: list[dict[str, Any]],
    *,
    before_date: str | None = None,
    grid: tuple[float, ...] = (0.25, 0.5, 0.75),
) -> float:
    """Pick λ minimizing MAE of λ·quant + (1-λ)·scenario on prior eval rows."""
    prior = [r for r in eval_rows if r.get("date") and (before_date is None or r["date"] < before_date)]
    by_date: dict[str, dict[str, float]] = {}
    for row in prior:
        tid = str(row.get("track_id") or "")
        if tid not in {"quant_ridge", "scenario_anchor"}:
            continue
        day = str(row["date"])
        by_date.setdefault(day, {})[tid] = float(row.get("predicted_pct") or 0.0)
        if row.get("actual_pct") is not None:
            by_date[day]["_actual"] = float(row["actual_pct"])

    pairs = [
        (vals["quant_ridge"], vals["scenario_anchor"], vals["_actual"])
        for vals in by_date.values()
        if "quant_ridge" in vals and "scenario_anchor" in vals and "_actual" in vals
    ]
    if not pairs:
        return 0.5

    best_lam = 0.5
    best_mae = float("inf")
    for lam in grid:
        errors = [abs(lam * q + (1.0 - lam) * s - actual) for q, s, actual in pairs]
        mae = sum(errors) / len(errors)
        if mae < best_mae:
            best_mae = mae
            best_lam = lam
    return best_lam
