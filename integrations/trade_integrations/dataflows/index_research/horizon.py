"""Horizon router — maps horizon_days to A/B/C prediction profiles."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class HorizonProfile:
    """Feature and model settings for a prediction horizon bucket."""

    name: str
    days: int
    feature_window: int
    poly_degree: int


def resolve_horizon(horizon_days: int | None = None) -> HorizonProfile:
    """Resolve horizon_days (or env default) to an A/B/C profile."""
    days = horizon_days or int(os.getenv("INDEX_RESEARCH_HORIZON_DAYS", "14"))
    if days <= 3:
        return HorizonProfile(name="A", days=days, feature_window=5, poly_degree=1)
    if days <= 21:
        return HorizonProfile(name="B", days=days, feature_window=14, poly_degree=2)
    return HorizonProfile(name="C", days=days, feature_window=60, poly_degree=2)
