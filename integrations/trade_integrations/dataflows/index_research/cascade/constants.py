"""Cascade constants shared across heuristic and VAR modules."""

from __future__ import annotations

# Primary factors shocked in absolute units (pts / bps style), not % of level.
ABSOLUTE_PRIMARY = frozenset(
    {"repo_rate", "india_vix", "us_10y", "fii_net_5d", "dii_net_5d"},
)

# Factors included in rolling VAR estimation (order matters for IRF indexing).
VAR_FACTOR_KEYS: tuple[str, ...] = (
    "oil_brent",
    "usd_inr",
    "india_vix",
    "sp500",
    "fii_net_5d",
    "us_10y",
    "nifty_pcr",
)

# Secondary factors that use absolute cascade mode when driven by a relative primary.
ABSOLUTE_SECONDARY = frozenset({"india_vix", "repo_rate", "us_10y"})

DEFAULT_BLEND_ALPHA = 0.5
DEFAULT_VAR_WINDOW_DAYS = 90
MIN_VAR_OBSERVATIONS = 30
