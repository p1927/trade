"""Shared types for factor cascade calibration and simulation."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

CascadeMode = Literal["relative", "absolute"]
CascadeSource = Literal["heuristic", "var", "blended"]
CascadeRegime = Literal["calm", "elevated", "crisis"]


@dataclass(frozen=True)
class CascadeSecondaryRule:
    """One secondary factor response to a primary shock."""

    secondary: str
    multiplier: float
    mode: CascadeMode
    source: CascadeSource = "heuristic"
    heuristic_multiplier: float | None = None
    var_multiplier: float | None = None


@dataclass
class CascadeCalibration:
    """Persisted VAR-calibrated cascade metadata."""

    as_of: str
    method: str = "rolling_ols_var1"
    window_days: int = 90
    blend_alpha: float = 0.5
    regime: CascadeRegime = "calm"
    status: str = "ok"
    message: str | None = None
    var_factors: list[str] = field(default_factory=list)
    rules: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> CascadeCalibration | None:
        if not raw:
            return None
        rules = raw.get("rules") or {}
        if not isinstance(rules, dict):
            rules = {}
        return cls(
            as_of=str(raw.get("as_of") or ""),
            method=str(raw.get("method") or "rolling_ols_var1"),
            window_days=int(raw.get("window_days") or 90),
            blend_alpha=float(raw.get("blend_alpha") or 0.5),
            regime=str(raw.get("regime") or "calm"),  # type: ignore[arg-type]
            status=str(raw.get("status") or "ok"),
            message=raw.get("message"),
            var_factors=list(raw.get("var_factors") or []),
            rules={str(k): list(v or []) for k, v in rules.items()},
            diagnostics=dict(raw.get("diagnostics") or {}),
        )


@dataclass(frozen=True)
class CascadeAppliedRow:
    """Audit row for a single factor move in a scenario."""

    factor: str
    before: float
    after: float
    reason: str
    source: CascadeSource | None = None
    var_implied_after: float | None = None
    heuristic_after: float | None = None
