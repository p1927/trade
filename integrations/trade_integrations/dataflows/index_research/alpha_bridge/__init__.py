"""Loosely-coupled Alpha Zoo → NIFTY index factor bridge."""

from __future__ import annotations

from trade_integrations.dataflows.index_research.alpha_bridge.config import (
    is_bridge_enabled,
    load_alpha_zoo_config,
)
from trade_integrations.dataflows.index_research.alpha_bridge.promotion import (
    ALPHA_ZOO_FACTOR_KEYS,
    promoted_alpha_zoo_factor_keys,
)
from trade_integrations.dataflows.index_research.alpha_bridge.snapshot import (
    apply_alpha_zoo_to_macro,
    compute_alpha_zoo_snapshot,
    maybe_persist_alpha_zoo_factors,
)

__all__ = [
    "ALPHA_ZOO_FACTOR_KEYS",
    "apply_alpha_zoo_to_macro",
    "compute_alpha_zoo_snapshot",
    "is_bridge_enabled",
    "load_alpha_zoo_config",
    "maybe_persist_alpha_zoo_factors",
    "promoted_alpha_zoo_factor_keys",
]
