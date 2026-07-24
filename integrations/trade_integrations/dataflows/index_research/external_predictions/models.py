"""Dataclasses for third-party NIFTY prediction records."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

SourceKind = Literal["media", "broker", "global_bank"]
AddedBy = Literal["seed", "user", "discover"]
FetchStatus = Literal["ok", "stale", "not_found", "error"]
Direction = Literal["bullish", "bearish", "neutral"]
Confidence = Literal["high", "medium", "low"]
NavigationAction = Literal[
    "goto",
    "click",
    "wait",
    "scroll",
    "dismiss",
    "click_text",
    "press_key",
]
PathApprovedBy = Literal["auto", "user"]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class ExternalPredictionTarget:
    low: float | None = None
    mid: float | None = None
    high: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "low": self.low,
            "mid": self.mid,
            "high": self.high,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> ExternalPredictionTarget:
        if not isinstance(data, dict):
            return cls()
        return cls(
            low=_maybe_float(data.get("low")),
            mid=_maybe_float(data.get("mid")),
            high=_maybe_float(data.get("high")),
        )


_NAVIGATION_ACTIONS = frozenset(
    {"goto", "click", "wait", "scroll", "dismiss", "click_text", "press_key"}
)


@dataclass
class NavigationStep:
    action: NavigationAction = "goto"
    url: str = ""
    selector: str = ""
    text: str = ""
    target: str = ""
    wait_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "action": self.action,
            "url": self.url,
            "selector": self.selector,
            "text": self.text,
            "wait_ms": self.wait_ms,
        }
        if self.target:
            payload["target"] = self.target
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> NavigationStep:
        if not isinstance(data, dict):
            return cls()
        action = str(data.get("action") or "goto")
        if action not in _NAVIGATION_ACTIONS:
            action = "goto"
        wait_ms = data.get("wait_ms")
        try:
            wait_val = int(wait_ms or 0)
        except (TypeError, ValueError):
            wait_val = 0
        return cls(
            action=action,  # type: ignore[arg-type]
            url=str(data.get("url") or ""),
            selector=str(data.get("selector") or ""),
            text=str(data.get("text") or ""),
            target=str(data.get("target") or ""),
            wait_ms=max(0, wait_val),
        )


@dataclass
class NavigationTrace:
    steps: list[NavigationStep] = field(default_factory=list)
    final_url: str = ""
    approved_by: PathApprovedBy = "auto"
    stale: bool = False
    created_at: str = ""
    last_success_at: str = ""
    replay_failures: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "steps": [step.to_dict() for step in self.steps],
            "final_url": self.final_url,
            "approved_by": self.approved_by,
            "stale": self.stale,
            "created_at": self.created_at,
            "last_success_at": self.last_success_at,
            "replay_failures": self.replay_failures,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> NavigationTrace | None:
        if not isinstance(data, dict):
            return None
        steps = [
            NavigationStep.from_dict(row)
            for row in (data.get("steps") or [])
            if isinstance(row, dict)
        ]
        approved_by = str(data.get("approved_by") or "auto")
        if approved_by not in {"auto", "user"}:
            approved_by = "auto"
        try:
            replay_failures = int(data.get("replay_failures") or 0)
        except (TypeError, ValueError):
            replay_failures = 0
        return cls(
            steps=steps,
            final_url=str(data.get("final_url") or ""),
            approved_by=approved_by,  # type: ignore[arg-type]
            stale=bool(data.get("stale")),
            created_at=str(data.get("created_at") or ""),
            last_success_at=str(data.get("last_success_at") or ""),
            replay_failures=max(0, replay_failures),
        )


def _trace_map_from_dict(raw: Any) -> dict[str, NavigationTrace]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, NavigationTrace] = {}
    for key, value in raw.items():
        trace = NavigationTrace.from_dict(value if isinstance(value, dict) else None)
        if trace is not None:
            out[str(key)] = trace
    return out


@dataclass
class ExternalPredictionSource:
    id: str
    display_name: str
    kind: SourceKind = "media"
    search_queries: list[str] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    landing_urls: list[str] = field(default_factory=list)
    curated_urls: list[str] = field(default_factory=list)
    entry_urls: list[str] = field(default_factory=list)
    search_keywords: list[str] = field(default_factory=list)
    saved_paths: dict[str, NavigationTrace] = field(default_factory=dict)
    approved_paths: dict[str, NavigationTrace] = field(default_factory=dict)
    watchlisted: bool = True
    discovered_at: str | None = None
    added_by: AddedBy = "seed"
    removable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "kind": self.kind,
            "search_queries": list(self.search_queries),
            "domains": list(self.domains),
            "landing_urls": list(self.landing_urls),
            "curated_urls": list(self.curated_urls),
            "entry_urls": list(self.entry_urls),
            "search_keywords": list(self.search_keywords),
            "saved_paths": {k: v.to_dict() for k, v in self.saved_paths.items()},
            "approved_paths": {k: v.to_dict() for k, v in self.approved_paths.items()},
            "watchlisted": self.watchlisted,
            "discovered_at": self.discovered_at,
            "added_by": self.added_by,
            "removable": self.removable,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> ExternalPredictionSource | None:
        if not isinstance(data, dict):
            return None
        sid = str(data.get("id") or "").strip()
        if not sid:
            return None
        kind = str(data.get("kind") or "media")
        if kind not in {"media", "broker", "global_bank"}:
            kind = "media"
        added_by = str(data.get("added_by") or "seed")
        if added_by not in {"seed", "user", "discover"}:
            added_by = "user"
        return cls(
            id=sid,
            display_name=str(data.get("display_name") or sid),
            kind=kind,  # type: ignore[arg-type]
            search_queries=[str(q) for q in (data.get("search_queries") or []) if str(q).strip()],
            domains=[str(d) for d in (data.get("domains") or []) if str(d).strip()],
            landing_urls=[str(u) for u in (data.get("landing_urls") or []) if str(u).strip()],
            curated_urls=[str(u) for u in (data.get("curated_urls") or []) if str(u).strip()],
            entry_urls=[str(u) for u in (data.get("entry_urls") or []) if str(u).strip()],
            search_keywords=[str(k) for k in (data.get("search_keywords") or []) if str(k).strip()],
            saved_paths=_trace_map_from_dict(data.get("saved_paths")),
            approved_paths=_trace_map_from_dict(data.get("approved_paths")),
            watchlisted=bool(data.get("watchlisted", True)),
            discovered_at=data.get("discovered_at"),
            added_by=added_by,  # type: ignore[arg-type]
            removable=bool(data.get("removable", added_by == "user")),
        )


@dataclass
class ExternalPredictionRecord:
    source_id: str
    symbol: str = "NIFTY"
    horizon_days: int = 14
    as_of: str = ""
    published_at: str = ""
    spot_at_fetch: float | None = None
    target: ExternalPredictionTarget = field(default_factory=ExternalPredictionTarget)
    target_date: str = ""
    direction: Direction = "neutral"
    expected_return_pct: float | None = None
    rationale_bullets: list[str] = field(default_factory=list)
    confidence: Confidence = "medium"
    provenance: dict[str, Any] = field(default_factory=dict)
    extraction: dict[str, Any] = field(default_factory=dict)
    fetch_status: FetchStatus = "not_found"
    error_message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "symbol": self.symbol,
            "horizon_days": self.horizon_days,
            "as_of": self.as_of,
            "published_at": self.published_at,
            "spot_at_fetch": self.spot_at_fetch,
            "target": self.target.to_dict(),
            "target_date": self.target_date,
            "direction": self.direction,
            "expected_return_pct": self.expected_return_pct,
            "rationale_bullets": list(self.rationale_bullets),
            "confidence": self.confidence,
            "provenance": dict(self.provenance),
            "extraction": dict(self.extraction),
            "fetch_status": self.fetch_status,
            "error_message": self.error_message,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> ExternalPredictionRecord | None:
        if not isinstance(data, dict):
            return None
        source_id = str(data.get("source_id") or "").strip()
        if not source_id:
            return None
        direction = str(data.get("direction") or "neutral")
        if direction not in {"bullish", "bearish", "neutral"}:
            direction = "neutral"
        confidence = str(data.get("confidence") or "medium")
        if confidence not in {"high", "medium", "low"}:
            confidence = "medium"
        fetch_status = str(data.get("fetch_status") or "not_found")
        if fetch_status not in {"ok", "stale", "not_found", "error"}:
            fetch_status = "not_found"
        return cls(
            source_id=source_id,
            symbol=str(data.get("symbol") or "NIFTY").upper(),
            horizon_days=int(data.get("horizon_days") or 14),
            as_of=str(data.get("as_of") or ""),
            published_at=str(data.get("published_at") or ""),
            spot_at_fetch=_maybe_float(data.get("spot_at_fetch")),
            target=ExternalPredictionTarget.from_dict(data.get("target")),
            target_date=str(data.get("target_date") or ""),
            direction=direction,  # type: ignore[arg-type]
            expected_return_pct=_maybe_float(data.get("expected_return_pct")),
            rationale_bullets=[str(b) for b in (data.get("rationale_bullets") or []) if str(b).strip()],
            confidence=confidence,  # type: ignore[arg-type]
            provenance=dict(data.get("provenance") or {}),
            extraction=dict(data.get("extraction") or {}),
            fetch_status=fetch_status,  # type: ignore[arg-type]
            error_message=str(data.get("error_message") or ""),
        )


@dataclass
class ExternalPredictionSnapshot:
    symbol: str = "NIFTY"
    horizon_days: int = 14
    fetched_at: str = ""
    refresh_completed_at: str = ""
    cache_ttl_hours: int = 24
    is_stale: bool = True
    sources: list[ExternalPredictionSource] = field(default_factory=list)
    predictions: list[ExternalPredictionRecord] = field(default_factory=list)
    internal_forecast: dict[str, Any] | None = None
    sources_ok: int = 0
    sources_error: int = 0
    sources_not_found: int = 0
    had_errors: bool = False
    refresh_attempt_failures: int = 0

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "symbol": self.symbol,
            "horizon_days": self.horizon_days,
            "fetched_at": self.fetched_at,
            "refresh_completed_at": self.refresh_completed_at,
            "cache_ttl_hours": self.cache_ttl_hours,
            "is_stale": self.is_stale,
            "sources": [s.to_dict() for s in self.sources],
            "predictions": [p.to_dict() for p in self.predictions],
        }
        if self.internal_forecast:
            payload["internal_forecast"] = dict(self.internal_forecast)
        payload["sources_ok"] = self.sources_ok
        payload["sources_error"] = self.sources_error
        payload["sources_not_found"] = self.sources_not_found
        payload["had_errors"] = self.had_errors
        payload["refresh_attempt_failures"] = self.refresh_attempt_failures
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> ExternalPredictionSnapshot:
        if not isinstance(data, dict):
            return cls()
        sources = [
            s
            for row in (data.get("sources") or [])
            if (s := ExternalPredictionSource.from_dict(row)) is not None
        ]
        predictions = [
            p
            for row in (data.get("predictions") or [])
            if (p := ExternalPredictionRecord.from_dict(row)) is not None
        ]
        internal = data.get("internal_forecast")
        return cls(
            symbol=str(data.get("symbol") or "NIFTY").upper(),
            horizon_days=int(data.get("horizon_days") or 14),
            fetched_at=str(data.get("fetched_at") or ""),
            refresh_completed_at=str(data.get("refresh_completed_at") or ""),
            cache_ttl_hours=int(data.get("cache_ttl_hours") or 24),
            is_stale=bool(data.get("is_stale", True)),
            sources=sources,
            predictions=predictions,
            internal_forecast=dict(internal) if isinstance(internal, dict) else None,
            sources_ok=int(data.get("sources_ok") or 0),
            sources_error=int(data.get("sources_error") or 0),
            sources_not_found=int(data.get("sources_not_found") or 0),
            had_errors=bool(data.get("had_errors", False)),
            refresh_attempt_failures=int(data.get("refresh_attempt_failures") or 0),
        )

    @classmethod
    def empty(cls, *, symbol: str = "NIFTY", horizon_days: int = 14) -> ExternalPredictionSnapshot:
        return cls(
            symbol=symbol.upper(),
            horizon_days=horizon_days,
            fetched_at="",
            is_stale=True,
        )


def _maybe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not (out == out):  # NaN
        return None
    return out


def utc_now_iso() -> str:
    return _utc_now_iso()
