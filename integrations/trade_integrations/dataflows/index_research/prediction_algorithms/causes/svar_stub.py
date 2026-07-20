"""H2 SVAR placeholder — impulse responses not computed in production path."""

from __future__ import annotations

from typing import Any


def run_svar_stub(*, endogenous: list[str] | None = None, lags: int = 4) -> dict[str, Any]:
    """Return empty IRF payload; real SVAR deferred to research notebooks."""
    variables = list(endogenous or ["india_vix", "fii_net_5d", "nifty_return_7d"])
    return {
        "status": "not_run",
        "method": "svar_stub",
        "endogenous": variables,
        "lags": lags,
        "irfs": {},
        "note": "SVAR estimation deferred — use offline statsmodels VAR/SVAR when needed.",
    }
