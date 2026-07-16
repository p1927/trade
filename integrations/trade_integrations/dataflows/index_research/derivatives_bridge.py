"""Bridge options_research chain analytics into index factor snapshot."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_DERIVATIVE_FACTOR_KEYS = (
    "qfinindia_skew",
    "qfinindia_expected_move",
    "qfinindia_tail_risk",
)


def load_derivatives_implied_factors(ticker: str = "NIFTY") -> list[dict[str, Any]]:
    """Read skew / expected move / tail risk from hub options_research artifact."""
    rows: list[dict[str, Any]] = []
    try:
        from trade_integrations.context.hub import load_options_research_json

        doc = load_options_research_json(ticker.strip().upper())
        if doc is None:
            return rows
        pred = getattr(doc, "prediction", None) or {}
        if hasattr(doc, "model_dump"):
            pred = doc.model_dump().get("prediction") or pred
        elif isinstance(doc, dict):
            pred = doc.get("prediction") or {}

        analytics = pred.get("analytics") or pred
        mapping = {
            "qfinindia_skew": analytics.get("skew") or analytics.get("vol_skew"),
            "qfinindia_expected_move": analytics.get("expected_move_pct")
            or analytics.get("expected_move"),
            "qfinindia_tail_risk": analytics.get("tail_risk"),
        }
        for key, raw in mapping.items():
            if raw is None:
                continue
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            rows.append(
                {
                    "factor": key,
                    "value": value,
                    "source": "options_research_bridge",
                }
            )
    except Exception as exc:
        logger.debug("derivatives bridge failed for %s: %s", ticker, exc)
    return rows
