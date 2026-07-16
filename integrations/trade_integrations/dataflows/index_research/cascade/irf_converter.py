"""Convert VAR impulse responses into cascade multiplier rules."""

from __future__ import annotations

from trade_integrations.dataflows.index_research.cascade.constants import (
    ABSOLUTE_SECONDARY,
    VAR_FACTOR_KEYS,
)
from trade_integrations.dataflows.index_research.cascade.heuristic_rules import (
    HEURISTIC_CASCADE_RULES,
)
from trade_integrations.dataflows.index_research.cascade.types import CascadeSecondaryRule
from trade_integrations.dataflows.index_research.cascade.var_estimator import (
    VarFitResult,
    impulse_response,
)


def _secondary_mode(secondary: str) -> str:
    return "absolute" if secondary in ABSOLUTE_SECONDARY else "relative"


def _irf_multiplier(
    primary: str,
    secondary: str,
    irf_value: float,
    *,
    shock_size: float = 1.0,
) -> float | None:
    """Map 1-step IRF (transformed units) to cascade multiplier per 1% primary shock."""
    if shock_size == 0 or not _finite(irf_value):
        return None
    raw = irf_value / shock_size
    if not _finite(raw):
        return None
    # Cap extreme IRF-derived multipliers to keep UI stable.
    cap = 2.0 if _secondary_mode(secondary) == "relative" else 3.0
    return max(-cap, min(cap, raw))


def _finite(v: float) -> bool:
    return v == v and abs(v) != float("inf")


def var_rules_from_fit(
    fit: VarFitResult,
    *,
    primaries: tuple[str, ...] | None = None,
    irf_horizon: int = 1,
) -> dict[str, list[CascadeSecondaryRule]]:
    """Derive cascade rules from VAR IRFs for known primary→secondary edges."""
    primary_list = primaries or tuple(HEURISTIC_CASCADE_RULES.keys())
    out: dict[str, list[CascadeSecondaryRule]] = {}

    for primary in primary_list:
        if primary not in fit.factors:
            continue
        edges = HEURISTIC_CASCADE_RULES.get(primary, [])
        if not edges:
            continue

        paths = impulse_response(fit, shock_factor=primary, shock_size=1.0, horizon=irf_horizon)
        if not paths:
            continue

        rules: list[CascadeSecondaryRule] = []
        for secondary, _heur_mult, mode in edges:
            if secondary not in fit.factors or secondary == primary:
                continue
            series = paths.get(secondary) or []
            if not series:
                continue
            # Use first post-shock step (index 0 is contemporaneous response).
            irf_val = series[0] if irf_horizon <= 1 else series[min(irf_horizon - 1, len(series) - 1)]
            var_mult = _irf_multiplier(primary, secondary, irf_val)
            if var_mult is None:
                continue
            rules.append(
                CascadeSecondaryRule(
                    secondary=secondary,
                    multiplier=var_mult,
                    mode=mode,  # type: ignore[arg-type]
                    source="var",
                    var_multiplier=var_mult,
                )
            )
        if rules:
            out[primary] = rules

    return out


def var_rules_to_serializable(
    rules: dict[str, list[CascadeSecondaryRule]],
) -> dict[str, list[dict]]:
    """Serialize VAR rules for hub persistence."""
    payload: dict[str, list[dict]] = {}
    for primary, rows in rules.items():
        payload[primary] = [
            {
                "secondary": r.secondary,
                "multiplier": round(r.multiplier, 6),
                "mode": r.mode,
                "source": r.source,
                "var_multiplier": r.var_multiplier,
            }
            for r in rows
        ]
    return payload
