"""Bridge domain models — watch specs, execution intents, handoffs."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal


class BridgeSignal(str, Enum):
    REVIEW_NEEDED = "REVIEW_NEEDED"
    EXIT_NOW = "EXIT_NOW"
    THESIS_BROKEN = "THESIS_BROKEN"
    HALT_TRADING = "HALT_TRADING"
    EXECUTE_INTENT = "EXECUTE_INTENT"


class IntentAction(str, Enum):
    ENTER = "ENTER"
    ADJUST = "ADJUST"
    EXIT = "EXIT"
    HOLD = "HOLD"


WatchMetric = Literal[
    "spot_move_pct",
    "level_above",
    "level_below",
    "oi_change_pct",
    "volume_spike_pct",
]
WatchDirection = Literal["either", "up", "down"]


@dataclass
class WatchGate:
    """Skip expensive LLM turns when market state is unchanged."""

    skip_if_unchanged_minutes: int = 30

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> WatchGate:
        if not payload:
            return cls()
        raw = payload.get("skip_if_unchanged_minutes", 30)
        try:
            minutes = int(raw)
        except (TypeError, ValueError):
            minutes = 30
        return cls(skip_if_unchanged_minutes=max(1, minutes))


@dataclass
class WatchRule:
    symbol: str
    metric: WatchMetric
    threshold: float
    direction: WatchDirection = "either"
    exchange: str = "NSE"
    baseline_ltp: float | None = None
    label: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> WatchRule:
        symbol = str(payload.get("symbol") or "").strip().upper()
        if not symbol:
            raise ValueError("watch rule symbol is required")
        metric = str(payload.get("metric") or payload.get("type") or "spot_move_pct")
        if metric not in (
            "spot_move_pct",
            "level_above",
            "level_below",
            "oi_change_pct",
            "volume_spike_pct",
        ):
            raise ValueError(f"unsupported watch metric: {metric}")
        threshold = float(payload.get("threshold") or payload.get("value") or 0)
        direction = str(payload.get("direction") or "either").lower()
        if direction not in ("either", "up", "down"):
            direction = "either"
        baseline = payload.get("baseline_ltp")
        baseline_ltp = float(baseline) if baseline is not None else None
        return cls(
            symbol=symbol,
            metric=metric,  # type: ignore[arg-type]
            threshold=threshold,
            direction=direction,  # type: ignore[arg-type]
            exchange=str(payload.get("exchange") or "NSE").upper(),
            baseline_ltp=baseline_ltp,
            label=(str(payload["label"]).strip() if payload.get("label") else None),
        )


@dataclass
class WatchSpec:
    rules: list[WatchRule] = field(default_factory=list)
    gate: WatchGate = field(default_factory=WatchGate)
    review_triggers: list[str] = field(
        default_factory=lambda: ["watch_rule_fired", "thesis_break", "news_material"]
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "rules": [rule.to_dict() for rule in self.rules],
            "gate": self.gate.to_dict(),
            "review_triggers": list(self.review_triggers),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> WatchSpec:
        if not payload:
            return cls()
        rules = [
            WatchRule.from_dict(row)
            for row in (payload.get("rules") or payload.get("watch_rules") or [])
            if isinstance(row, dict)
        ]
        gate = WatchGate.from_dict(payload.get("gate") if isinstance(payload.get("gate"), dict) else None)
        triggers = payload.get("review_triggers")
        if isinstance(triggers, list):
            review_triggers = [str(item) for item in triggers if str(item).strip()]
        else:
            review_triggers = ["watch_rule_fired", "thesis_break", "news_material"]
        return cls(rules=rules, gate=gate, review_triggers=review_triggers)


@dataclass
class QuoteSnapshot:
    symbol: str
    exchange: str
    ltp: float
    open: float | None = None
    high: float | None = None
    low: float | None = None
    volume: float | None = None
    oi: float | None = None
    fetched_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_openalgo_row(cls, symbol: str, exchange: str, row: dict[str, Any]) -> QuoteSnapshot | None:
        ltp_raw = row.get("ltp") or row.get("last_price") or row.get("close")
        if ltp_raw is None:
            return None
        try:
            ltp = float(ltp_raw)
        except (TypeError, ValueError):
            return None

        def _opt_float(key: str) -> float | None:
            value = row.get(key)
            if value is None:
                return None
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        return cls(
            symbol=symbol.upper(),
            exchange=exchange.upper(),
            ltp=ltp,
            open=_opt_float("open"),
            high=_opt_float("high"),
            low=_opt_float("low"),
            volume=_opt_float("volume"),
            oi=_opt_float("oi"),
        )


@dataclass
class WatchAlert:
    signal: BridgeSignal
    rule: WatchRule | None
    symbol: str
    message: str
    ltp: float | None = None
    move_pct: float | None = None
    fired_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["signal"] = self.signal.value
        if self.rule is not None:
            payload["rule"] = self.rule.to_dict()
        return payload


@dataclass
class ExecutionLeg:
    symbol: str
    exchange: str
    action: str
    quantity: int
    product: str = "NRML"
    price: float | None = None
    order_type: str = "MARKET"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ExecutionLeg:
        return cls(
            symbol=str(payload.get("symbol") or "").upper(),
            exchange=str(payload.get("exchange") or "NFO").upper(),
            action=str(payload.get("action") or payload.get("side") or "BUY").upper(),
            quantity=int(payload.get("quantity") or payload.get("qty") or 0),
            product=str(payload.get("product") or "NRML").upper(),
            price=(float(payload["price"]) if payload.get("price") is not None else None),
            order_type=str(payload.get("order_type") or payload.get("pricetype") or "MARKET").upper(),
        )


@dataclass
class ExecutionIntent:
    action: IntentAction
    agent_id: str
    rationale: str
    confidence: int = 0
    legs: list[ExecutionLeg] = field(default_factory=list)
    strategy: str = "nautilus_bridge"
    widget_id: str | None = None
    underlying: str | None = None
    intent_id: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["action"] = self.action.value
        payload["legs"] = [leg.to_dict() for leg in self.legs]
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ExecutionIntent:
        action_raw = str(payload.get("action") or "HOLD").upper()
        action = IntentAction(action_raw) if action_raw in IntentAction.__members__ else IntentAction.HOLD
        legs = [
            ExecutionLeg.from_dict(row)
            for row in (payload.get("legs") or [])
            if isinstance(row, dict)
        ]
        confidence_raw = payload.get("confidence", 0)
        try:
            confidence = int(confidence_raw)
        except (TypeError, ValueError):
            confidence = 0
        return cls(
            action=action,
            agent_id=str(payload.get("agent_id") or ""),
            rationale=str(payload.get("rationale") or ""),
            confidence=confidence,
            legs=legs,
            strategy=str(payload.get("strategy") or "nautilus_bridge"),
            widget_id=(str(payload["widget_id"]) if payload.get("widget_id") else None),
            underlying=(str(payload["underlying"]).upper() if payload.get("underlying") else None),
            intent_id=(str(payload["intent_id"]) if payload.get("intent_id") else None),
            created_at=str(payload.get("created_at") or datetime.now(timezone.utc).isoformat()),
        )


@dataclass
class StopRules:
    max_loss_inr: float | None = None
    spot_stop_pct: float | None = None
    flatten_at_close: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> StopRules:
        if not payload:
            return cls()
        max_loss = payload.get("max_loss_inr")
        spot_stop = payload.get("spot_stop_pct")
        return cls(
            max_loss_inr=(float(max_loss) if max_loss is not None else None),
            spot_stop_pct=(float(spot_stop) if spot_stop is not None else None),
            flatten_at_close=bool(payload.get("flatten_at_close", True)),
        )


@dataclass
class PositionHandoff:
    agent_id: str
    widget_id: str | None
    underlying: str
    legs: list[ExecutionLeg]
    entry_spot: float
    watch_spec: WatchSpec = field(default_factory=WatchSpec)
    stop_rules: StopRules = field(default_factory=StopRules)
    vibe_session_id: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "widget_id": self.widget_id,
            "underlying": self.underlying,
            "legs": [leg.to_dict() for leg in self.legs],
            "entry_spot": self.entry_spot,
            "watch_spec": self.watch_spec.to_dict(),
            "stop_rules": self.stop_rules.to_dict(),
            "vibe_session_id": self.vibe_session_id,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> PositionHandoff:
        legs = [
            ExecutionLeg.from_dict(row)
            for row in (payload.get("legs") or [])
            if isinstance(row, dict)
        ]
        watch_spec = WatchSpec.from_dict(
            payload.get("watch_spec") if isinstance(payload.get("watch_spec"), dict) else None
        )
        stop_rules = StopRules.from_dict(
            payload.get("stop_rules") if isinstance(payload.get("stop_rules"), dict) else None
        )
        return cls(
            agent_id=str(payload.get("agent_id") or ""),
            widget_id=(str(payload["widget_id"]) if payload.get("widget_id") else None),
            underlying=str(payload.get("underlying") or "NIFTY").upper(),
            legs=legs,
            entry_spot=float(payload.get("entry_spot") or 0),
            watch_spec=watch_spec,
            stop_rules=stop_rules,
            vibe_session_id=(str(payload["vibe_session_id"]) if payload.get("vibe_session_id") else None),
            created_at=str(payload.get("created_at") or datetime.now(timezone.utc).isoformat()),
        )
