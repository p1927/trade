"""Static expert prior cascade rules (no data dependency)."""

from __future__ import annotations

from trade_integrations.dataflows.index_research.cascade.types import CascadeSecondaryRule

# secondary -> (multiplier per 1% primary shock, mode)
HEURISTIC_CASCADE_RULES: dict[str, list[tuple[str, float, str]]] = {
    "oil_brent": [
        ("usd_inr", 0.15, "relative"),
        ("india_vix", 0.15, "absolute"),
        ("gold", 0.05, "relative"),
    ],
    "oil_wti": [
        ("usd_inr", 0.12, "relative"),
        ("india_vix", 0.12, "absolute"),
    ],
    "usd_inr": [
        ("india_vix", 0.08, "absolute"),
        ("fii_net_5d", -0.02, "relative"),
    ],
    "fii_net_5d": [
        ("usd_inr", 0.10, "relative"),
        ("india_vix", 0.12, "absolute"),
        ("sp500", 0.08, "relative"),
    ],
    "dii_net_5d": [
        ("fii_net_5d", -0.05, "relative"),
        ("india_vix", -0.05, "absolute"),
    ],
    "sp500": [
        ("fii_net_5d", 0.10, "relative"),
        ("india_vix", -0.08, "absolute"),
        ("usd_inr", -0.05, "relative"),
    ],
    "us_10y": [
        ("usd_inr", 0.06, "relative"),
        ("sp500", -0.05, "relative"),
        ("india_vix", 0.05, "absolute"),
    ],
    "india_vix": [
        ("fii_net_5d", -0.08, "relative"),
        ("index_sentiment", -0.10, "relative"),
    ],
    "repo_rate": [
        ("usd_inr", 0.04, "relative"),
        ("india_vix", 0.10, "absolute"),
    ],
    "index_sentiment": [
        ("india_vix", -0.06, "absolute"),
    ],
    "nifty_pcr": [
        ("india_vix", 0.05, "absolute"),
        ("fii_net_5d", -0.05, "relative"),
    ],
}


def heuristic_rules_for(primary: str) -> list[CascadeSecondaryRule]:
    """Return heuristic secondary rules for a primary factor."""
    rows = HEURISTIC_CASCADE_RULES.get(primary.strip(), [])
    return [
        CascadeSecondaryRule(
            secondary=secondary,
            multiplier=mult,
            mode=mode,  # type: ignore[arg-type]
            source="heuristic",
            heuristic_multiplier=mult,
        )
        for secondary, mult, mode in rows
    ]
