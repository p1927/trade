"""Walk-forward evaluation helpers — purged indices and bootstrap confidence."""

from __future__ import annotations

import random
from typing import Any

_BOOTSTRAP_SAMPLES = 500
_BOOTSTRAP_CONFIDENCE = 0.95


def purged_train_end_index(
    eval_index: int,
    *,
    horizon_days: int,
    eval_step: int,
) -> int:
    """Last train row index (exclusive) excluding overlapping forward-return labels."""
    if eval_step >= horizon_days:
        return eval_index
    # Drop train rows whose H-day forward return window overlaps the test label at eval_index.
    return max(0, eval_index - horizon_days)


def expanding_eval_indices(
    *,
    min_train_rows: int,
    max_index: int,
    eval_step: int,
) -> list[int]:
    return list(range(min_train_rows, max_index + 1, max(1, eval_step)))


def bootstrap_direction_ci(
    eval_rows: list[dict[str, Any]],
    *,
    n_samples: int = _BOOTSTRAP_SAMPLES,
    confidence: float = _BOOTSTRAP_CONFIDENCE,
) -> dict[str, Any]:
    """Binomial bootstrap CI on walk-forward direction hits."""
    hits = [bool(r.get("direction_correct")) for r in eval_rows if r.get("direction_correct") is not None]
    n = len(hits)
    if n < 2:
        return {
            "n": n,
            "direction_hit_rate": None,
            "ci_lower": None,
            "ci_upper": None,
            "insufficient_evidence": True,
        }

    observed = sum(hits) / n
    rng = random.Random(42)
    deltas: list[float] = []
    for _ in range(n_samples):
        sample = [hits[rng.randrange(n)] for _ in range(n)]
        deltas.append(sum(sample) / n)

    deltas.sort()
    alpha = 1.0 - confidence
    lower_idx = max(0, int((alpha / 2) * n_samples))
    upper_idx = min(n_samples - 1, int((1 - alpha / 2) * n_samples) - 1)
    ci_lower = deltas[lower_idx]
    ci_upper = deltas[upper_idx]
    insufficient = ci_lower <= 0.5 <= ci_upper

    return {
        "n": n,
        "direction_hit_rate": round(observed, 4),
        "ci_lower": round(ci_lower, 4),
        "ci_upper": round(ci_upper, 4),
        "insufficient_evidence": insufficient,
    }


def sign_magnitude_score(predicted_pct: float, actual_pct: float) -> float:
    """Direction sign agreement weighted by min absolute move."""
    if predicted_pct == 0 or actual_pct == 0:
        return 0.0
    sign_ok = (predicted_pct > 0) == (actual_pct > 0)
    weight = min(abs(predicted_pct), abs(actual_pct))
    return weight if sign_ok else -weight
