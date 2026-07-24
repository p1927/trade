"""Unified agent intent schema — single authority for engagement, instruments, watch, capabilities."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

EngagementMode = Literal["observe", "trade"]
InstrumentClass = Literal["equity", "options", "futures", "index"]
WatchConditionKind = Literal[
    "schedule",
    "price_level",
    "price_move",
    "volume",
    "oi",
    "vix",
    "composite",
]

VALID_INSTRUMENTS = frozenset({"equity", "options", "futures", "index"})
VALID_WATCH_KINDS = frozenset(
    {"schedule", "price_level", "price_move", "volume", "oi", "vix", "composite"}
)
INTENT_FIELD_NAMES = frozenset(
    {
        "engagement",
        "instruments",
        "symbols",
        "schedules",
        "watch_conditions",
        "confidence_threshold",
        "constraints",
        "needs_clarification",
    }
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class WatchCondition:
    kind: WatchConditionKind
    symbol: str
    params: dict[str, Any] = field(default_factory=dict)
    label: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "symbol": self.symbol,
            "params": dict(self.params),
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> WatchCondition | None:
        if not isinstance(payload, dict):
            return None
        kind = str(payload.get("kind") or "").strip().lower()
        if kind not in VALID_WATCH_KINDS:
            return None
        symbol = str(payload.get("symbol") or "").strip().upper()
        if not symbol:
            return None
        params = payload.get("params")
        if not isinstance(params, dict):
            params = {}
        label_raw = payload.get("label")
        label = str(label_raw).strip() if label_raw else None
        return cls(kind=kind, symbol=symbol, params=dict(params), label=label)  # type: ignore[arg-type]


@dataclass
class AgentIntent:
    engagement: EngagementMode = "trade"
    instruments: list[InstrumentClass] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)
    schedules: dict[str, int] = field(default_factory=dict)
    watch_conditions: list[WatchCondition] = field(default_factory=list)
    capabilities: dict[str, bool] = field(default_factory=dict)
    confidence_threshold: int = 75
    constraints: dict[str, Any] = field(default_factory=dict)
    clarified: dict[str, bool] = field(default_factory=dict)
    needs_clarification: list[str] = field(default_factory=list)
    source_message_id: str = ""
    updated_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "engagement": self.engagement,
            "instruments": list(self.instruments),
            "symbols": list(self.symbols),
            "schedules": dict(self.schedules),
            "watch_conditions": [row.to_dict() for row in self.watch_conditions],
            "capabilities": dict(self.capabilities),
            "confidence_threshold": int(self.confidence_threshold),
            "constraints": dict(self.constraints),
            "clarified": dict(self.clarified),
            "needs_clarification": list(self.needs_clarification),
            "source_message_id": self.source_message_id,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> AgentIntent:
        if not isinstance(payload, dict):
            return default_agent_intent()
        engagement = str(payload.get("engagement") or "trade").strip().lower()
        if engagement not in ("observe", "trade"):
            engagement = "trade"
        instruments: list[InstrumentClass] = []
        for raw in payload.get("instruments") or []:
            inst = str(raw).strip().lower()
            if inst in VALID_INSTRUMENTS and inst not in instruments:
                instruments.append(inst)  # type: ignore[arg-type]
        symbols = [
            str(s).strip().upper() for s in (payload.get("symbols") or []) if str(s).strip()
        ]
        schedules_raw = payload.get("schedules") if isinstance(payload.get("schedules"), dict) else {}
        schedules: dict[str, int] = {}
        for key in ("watch_ms", "research_ms"):
            if key in schedules_raw:
                try:
                    schedules[key] = max(1, int(schedules_raw[key]))
                except (TypeError, ValueError):
                    pass
        watch_conditions: list[WatchCondition] = []
        for row in payload.get("watch_conditions") or []:
            parsed = WatchCondition.from_dict(row if isinstance(row, dict) else None)
            if parsed:
                watch_conditions.append(parsed)
        capabilities_raw = payload.get("capabilities")
        capabilities = dict(capabilities_raw) if isinstance(capabilities_raw, dict) else {}
        constraints_raw = payload.get("constraints")
        constraints = dict(constraints_raw) if isinstance(constraints_raw, dict) else {}
        clarified_raw = payload.get("clarified")
        clarified = dict(clarified_raw) if isinstance(clarified_raw, dict) else {}
        needs = [str(x) for x in (payload.get("needs_clarification") or []) if str(x).strip()]
        try:
            threshold = int(payload.get("confidence_threshold") or 75)
        except (TypeError, ValueError):
            threshold = 75
        return cls(
            engagement=engagement,  # type: ignore[arg-type]
            instruments=instruments,
            symbols=symbols,
            schedules=schedules,
            watch_conditions=watch_conditions,
            capabilities=capabilities,
            confidence_threshold=max(0, min(100, threshold)),
            constraints=constraints,
            clarified=clarified,
            needs_clarification=needs,
            source_message_id=str(payload.get("source_message_id") or ""),
            updated_at=str(payload.get("updated_at") or _now_iso()),
        )


@dataclass
class IntentDelta:
    """Partial update from one user message — only explicit_fields were stated."""

    engagement: EngagementMode | None = None
    instruments: list[InstrumentClass] | None = None
    symbols: list[str] | None = None
    schedules: dict[str, int] | None = None
    watch_conditions: list[WatchCondition] | None = None
    confidence_threshold: int | None = None
    constraints: dict[str, Any] | None = None
    explicit_fields: list[str] = field(default_factory=list)
    needs_clarification: list[str] = field(default_factory=list)
    source_message_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "explicit_fields": list(self.explicit_fields),
            "needs_clarification": list(self.needs_clarification),
            "source_message_id": self.source_message_id,
        }
        if self.engagement is not None:
            payload["engagement"] = self.engagement
        if self.instruments is not None:
            payload["instruments"] = list(self.instruments)
        if self.symbols is not None:
            payload["symbols"] = list(self.symbols)
        if self.schedules is not None:
            payload["schedules"] = dict(self.schedules)
        if self.watch_conditions is not None:
            payload["watch_conditions"] = [row.to_dict() for row in self.watch_conditions]
        if self.confidence_threshold is not None:
            payload["confidence_threshold"] = self.confidence_threshold
        if self.constraints is not None:
            payload["constraints"] = dict(self.constraints)
        return payload


def default_agent_intent(*, symbols: list[str] | None = None) -> AgentIntent:
    sym = [str(s).strip().upper() for s in (symbols or []) if str(s).strip()]
    intent = AgentIntent(symbols=sym or ["NIFTY"])
    from trade_integrations.autonomous_agents.intent_merge import derive_capabilities

    intent.capabilities = derive_capabilities(intent)
    return intent


def intent_json_schema_block() -> str:
    return """{
  "explicit_fields": ["engagement", "instruments", ...],
  "needs_clarification": ["instruments"],
  "engagement": "observe" | "trade",
  "instruments": ["equity" | "options" | "futures" | "index"],
  "symbols": ["NIFTY"],
  "schedules": {"watch_ms": 180000, "research_ms": 5400000},
  "watch_conditions": [
    {"kind": "schedule", "symbol": "NIFTY", "params": {"every_min": 3}, "label": "poll every 3 min"},
    {"kind": "price_move", "symbol": "NIFTY", "params": {"points": 50}, "label": "50 pt move"},
    {"kind": "price_level", "symbol": "NIFTY", "params": {"above": 24500}, "label": "breakout above 24500"}
  ],
  "confidence_threshold": 75,
  "constraints": {"budget_inr": 20000, "max_daily_loss_inr": 2000, "mode": "paper"}
}"""
