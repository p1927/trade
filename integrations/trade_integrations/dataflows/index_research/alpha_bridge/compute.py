"""Cross-sectional Alpha Zoo composites → index-level scalar factors."""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

from trade_integrations.dataflows.index_research.alpha_bridge.config import basket_alpha_ids

logger = logging.getLogger(__name__)

COMPOSITE_KEYS: tuple[str, ...] = (
    "alpha_zoo_ls_spread",
    "alpha_zoo_breadth",
    "alpha_zoo_momentum_consensus",
    "alpha_zoo_dispersion",
)


def _cross_sectional_zscore(row: pd.Series) -> pd.Series:
    values = row.astype(float)
    mean = values.mean(skipna=True)
    std = values.std(skipna=True)
    if std is None or not np.isfinite(std) or std < 1e-9:
        return values * 0.0
    return (values - mean) / std


def _load_registry() -> Any | None:
    try:
        from src.factors.registry import Registry  # type: ignore[import-not-found]
    except ImportError:
        logger.debug("alpha_bridge: vibetrading Registry unavailable")
        return None
    try:
        return Registry()
    except Exception as exc:  # noqa: BLE001
        logger.warning("alpha_bridge: Registry init failed: %s", exc)
        return None


def composites_from_consensus(consensus: pd.Series) -> dict[str, float]:
    """Map latest cross-section consensus vector to four composite scalars."""
    clean = consensus.dropna().astype(float)
    if clean.empty:
        return {}

    n = len(clean)
    if n < 5:
        ls_spread = float(clean.max() - clean.min())
    else:
        ranked = clean.sort_values()
        quintile = max(1, n // 5)
        ls_spread = float(ranked.iloc[-quintile:].mean() - ranked.iloc[:quintile].mean())

    breadth = float((clean > 0).sum() / n)
    momentum = float(clean.mean())
    dispersion = float(clean.std(ddof=0)) if n > 1 else 0.0

    return {
        "alpha_zoo_ls_spread": ls_spread,
        "alpha_zoo_breadth": breadth,
        "alpha_zoo_momentum_consensus": momentum,
        "alpha_zoo_dispersion": dispersion,
    }


def compute_composites_from_panel(
    panel: dict[str, pd.DataFrame],
    *,
    alpha_ids: tuple[str, ...] | None = None,
) -> dict[str, float]:
    """Compute composite alpha_zoo_* scalars for the latest panel bar."""
    if not panel or "close" not in panel or panel["close"].empty:
        return {}
    ids = alpha_ids or basket_alpha_ids()
    registry = _load_registry()
    if registry is None:
        return {}

    scores: list[pd.Series] = []
    for alpha_id in ids:
        try:
            factor_df = registry.compute(alpha_id, panel)
        except Exception as exc:  # noqa: BLE001
            logger.debug("alpha_bridge compute failed for %s: %s", alpha_id, exc)
            continue
        if factor_df is None or factor_df.empty:
            continue
        z = factor_df.apply(_cross_sectional_zscore, axis=1)
        if z.empty:
            continue
        scores.append(z.iloc[-1])

    if not scores:
        return {}

    consensus = pd.concat(scores, axis=1).mean(axis=1)
    return composites_from_consensus(consensus)


def compute_composites_history(
    panel: dict[str, pd.DataFrame],
    *,
    alpha_ids: tuple[str, ...] | None = None,
) -> pd.DataFrame:
    """Daily composite history aligned to panel close index."""
    close = panel.get("close")
    if close is None or close.empty:
        return pd.DataFrame()

    ids = alpha_ids or basket_alpha_ids()
    registry = _load_registry()
    if registry is None:
        return pd.DataFrame()

    z_frames: list[pd.DataFrame] = []
    for alpha_id in ids:
        try:
            factor_df = registry.compute(alpha_id, panel)
        except Exception as exc:  # noqa: BLE001
            logger.debug("alpha_bridge history failed for %s: %s", alpha_id, exc)
            continue
        if factor_df is None or factor_df.empty:
            continue
        z_frames.append(factor_df.apply(_cross_sectional_zscore, axis=1))

    if not z_frames:
        return pd.DataFrame()

    consensus = z_frames[0].copy()
    for extra in z_frames[1:]:
        consensus = consensus.add(extra, fill_value=np.nan)
    consensus = consensus / float(len(z_frames))

    rows: list[dict[str, Any]] = []
    for idx, row in consensus.iterrows():
        values = composites_from_consensus(row)
        if values:
            rows.append({"date": pd.Timestamp(idx).date().isoformat(), **values})
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)
