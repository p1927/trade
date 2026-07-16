"""Cascade rule providers — pluggable sources decoupled from the engine."""

from __future__ import annotations

from typing import Protocol

from trade_integrations.dataflows.index_research.cascade.heuristic_rules import heuristic_rules_for
from trade_integrations.dataflows.index_research.cascade.regime_scaler import scale_rules
from trade_integrations.dataflows.index_research.cascade.types import (
    CascadeCalibration,
    CascadeRegime,
    CascadeSecondaryRule,
)


class CascadeRuleProvider(Protocol):
    """Supply secondary cascade rules for a primary factor."""

    def rules_for(self, primary: str) -> list[CascadeSecondaryRule]: ...


class HeuristicRuleProvider:
    """Expert prior rules only."""

    def __init__(self, *, regime: CascadeRegime = "calm") -> None:
        self._regime = regime

    def rules_for(self, primary: str) -> list[CascadeSecondaryRule]:
        return scale_rules(heuristic_rules_for(primary), self._regime)


class CalibratedRuleProvider:
    """Pre-blended rules persisted by the offline calibration job."""

    def __init__(
        self,
        calibration: CascadeCalibration,
        *,
        regime: CascadeRegime | None = None,
    ) -> None:
        self._regime = regime or calibration.regime
        self._rules = _deserialize_rules(calibration.rules)

    def rules_for(self, primary: str) -> list[CascadeSecondaryRule]:
        rows = self._rules.get(primary)
        if not rows:
            return scale_rules(heuristic_rules_for(primary), self._regime)
        return scale_rules(rows, self._regime)


def _deserialize_rules(
    raw: dict[str, list[dict]],
) -> dict[str, list[CascadeSecondaryRule]]:
    out: dict[str, list[CascadeSecondaryRule]] = {}
    for primary, rows in raw.items():
        parsed: list[CascadeSecondaryRule] = []
        for row in rows:
            if not row.get("secondary"):
                continue
            parsed.append(
                CascadeSecondaryRule(
                    secondary=str(row.get("secondary") or ""),
                    multiplier=float(row.get("multiplier") or 0.0),
                    mode=row.get("mode") or "relative",  # type: ignore[arg-type]
                    source=row.get("source") or "blended",  # type: ignore[arg-type]
                    var_multiplier=row.get("var_multiplier"),
                    heuristic_multiplier=row.get("heuristic_multiplier"),
                )
            )
        if parsed:
            out[primary] = parsed
    return out


def build_rule_provider(
    calibration: CascadeCalibration | None,
    *,
    regime: CascadeRegime = "calm",
    force_heuristic: bool = False,
) -> CascadeRuleProvider:
    """Factory for the active cascade rule source."""
    if (
        not force_heuristic
        and calibration is not None
        and calibration.status == "ok"
        and calibration.rules
    ):
        return CalibratedRuleProvider(calibration, regime=regime)
    return HeuristicRuleProvider(regime=regime)
