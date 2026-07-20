"""H3 DoWhy placeholder — causal effect estimates not computed in production path."""

from __future__ import annotations

from typing import Any


def run_dowhy_stub(
    *,
    treatment: str = "fii_net_5d",
    outcome: str = "nifty_return_14d",
) -> dict[str, Any]:
    """Return documented not-run payload for DoWhy causal graph work."""
    return {
        "status": "not_run",
        "method": "dowhy_stub",
        "treatment": treatment,
        "outcome": outcome,
        "effect_estimate": None,
        "note": "DoWhy causal identification deferred — requires curated confounder panel.",
    }
