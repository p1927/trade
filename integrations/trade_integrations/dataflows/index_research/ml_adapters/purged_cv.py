"""Purged time-series cross-validation — never random K-fold on finance data."""

from __future__ import annotations

from typing import Iterator

import numpy as np
from sklearn.model_selection import TimeSeriesSplit


def purged_time_series_splits(
    n_samples: int,
    *,
    n_splits: int = 5,
    embargo_days: int = 14,
    min_train_size: int = 60,
) -> Iterator[tuple[np.ndarray, np.ndarray]]:
    """Yield (train_idx, test_idx) with embargo gap after each train fold."""
    if n_samples < min_train_size + embargo_days + 1:
        return
    splitter = TimeSeriesSplit(n_splits=n_splits, gap=embargo_days)
    indices = np.arange(n_samples)
    for train_idx, test_idx in splitter.split(indices):
        if len(train_idx) < min_train_size:
            continue
        yield train_idx, test_idx


def last_train_indices(
    n_samples: int,
    *,
    min_train_rows: int = 120,
) -> np.ndarray:
    """Indices for expanding-window live prediction (all rows except tail holdout)."""
    if n_samples < min_train_rows:
        return np.array([], dtype=int)
    return np.arange(n_samples)
