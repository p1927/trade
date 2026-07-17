"""Run required research stages and persist hub artifacts before widget build."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Literal

from trade_integrations.research.registry import ResearchKind, get_contract

ResearchStatus = Literal["complete", "incomplete", "partial"]


def _require_debate_for_execute() -> bool:
    return os.getenv("TRADINGAGENTS_REQUIRE_DEBATE_FOR_EXECUTE", "true").lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _nested_get(obj: Any, path: str) -> Any:
    cur = obj
    for part in path.split("."):
        if cur is None:
            return None
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            cur = getattr(cur, part, None)
    return cur


def _doc_to_check_dict(doc: Any) -> dict[str, Any]:
    if doc is None:
        return {}
    if isinstance(doc, dict):
        return doc
    from dataclasses import asdict, is_dataclass

    if is_dataclass(doc):
        return asdict(doc)
    return getattr(doc, "__dict__", {}) or {}


def _missing_required_fields(doc: Any, required: tuple[str, ...]) -> list[str]:
    data = _doc_to_check_dict(doc)
    rec = data.get("recommended") or {}
    is_hold = str(rec.get("action") or "").upper() == "HOLD" or rec.get("name") == "hold_cash"
    missing: list[str] = []
    for path in required:
        val = _nested_get(data, path)
        if val is None:
            missing.append(path)
        elif (
            isinstance(val, float)
            and path.endswith(("max_profit", "max_loss"))
            and val == 0
            and not is_hold
        ):
            missing.append(path)
    return missing


def _hub_doc_needs_data_refresh(doc: Any, kind: ResearchKind) -> bool:
    """True when cached hub doc lacks trustworthy spot or chain failed."""
    from trade_integrations.monitor.doc_spot import resolve_doc_spot

    if doc is None:
        return True
    kind_str = "stock" if kind == ResearchKind.STOCK else "options"
    if resolve_doc_spot(doc, kind=kind_str) is None:  # type: ignore[arg-type]
        return True
    if kind == ResearchKind.OPTIONS:
        data = _doc_to_check_dict(doc)
        for stage in data.get("stages") or []:
            if isinstance(stage, dict) and stage.get("stage") == "chain" and stage.get("status") == "error":
                return True
    return False


@dataclass
class ResearchResult:
    status: ResearchStatus
    kind: ResearchKind
    ticker: str
    doc: Any | None = None
    stages_run: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    debate_pending: bool = False
    error: str | None = None


def _is_hub_fresh(ticker: str, kind: ResearchKind, *, refresh: bool) -> bool:
    if refresh:
        return False
    from trade_integrations.context.hub import (
        is_agent_debate_cache_fresh,
        is_index_research_cache_fresh,
        is_options_cache_fresh,
        is_stock_cache_fresh,
    )

    if kind == ResearchKind.STOCK:
        return is_stock_cache_fresh(ticker)
    if kind == ResearchKind.OPTIONS:
        return is_options_cache_fresh(ticker)
    return is_index_research_cache_fresh(ticker)


def _load_hub_doc(ticker: str, kind: ResearchKind) -> Any | None:
    from trade_integrations.context.hub import (
        load_index_research_json,
        load_options_research_json,
        load_stock_research_json,
    )

    sym = ticker.strip().upper().replace(".NS", "").replace(".BO", "")
    if kind == ResearchKind.STOCK:
        return load_stock_research_json(sym)
    if kind == ResearchKind.OPTIONS:
        return load_options_research_json(sym)
    return load_index_research_json(sym)


def _run_batch_pipeline(
    ticker: str,
    kind: ResearchKind,
    *,
    horizon_days: int,
    expiry_date: str | None,
    refresh_constituents: bool,
) -> Any:
    sym = ticker.strip().upper().replace(".NS", "").replace(".BO", "")
    if kind == ResearchKind.STOCK:
        from trade_integrations.dataflows.stock_research.aggregator import run_stock_research

        return run_stock_research(sym, lookahead_days=horizon_days)
    if kind == ResearchKind.OPTIONS:
        from trade_integrations.dataflows.options_research.aggregator import run_options_research

        return run_options_research(sym, expiry_date=expiry_date, lookahead_days=horizon_days)
    from trade_integrations.dataflows.index_research.aggregator import run_index_research

    return run_index_research(sym, horizon_days=horizon_days, refresh_constituents=refresh_constituents)


def _save_hub_doc(ticker: str, kind: ResearchKind, doc: Any) -> None:
    sym = ticker.strip().upper().replace(".NS", "").replace(".BO", "")
    from trade_integrations.context.hub import (
        save_index_research,
        save_options_research,
        save_stock_research,
    )

    if kind == ResearchKind.STOCK:
        save_stock_research(doc)
    elif kind == ResearchKind.OPTIONS:
        save_options_research(doc)
    else:
        save_index_research(doc)


def _debate_stage_status(ticker: str, *, required: bool) -> tuple[bool, bool]:
    """Return (fresh, pending). pending=True when required but not fresh."""
    from trade_integrations.context.hub import is_agent_debate_cache_fresh

    sym = ticker.strip().upper().replace(".NS", "").replace(".BO", "")
    fresh = is_agent_debate_cache_fresh(sym)
    if required and not fresh and _require_debate_for_execute():
        return fresh, True
    return fresh, False


def ensure_research_complete(
    ticker: str,
    *,
    kind: ResearchKind | str,
    refresh: bool = False,
    horizon_days: int = 14,
    expiry_date: str | None = None,
    require_debate: bool | None = None,
    refresh_constituents: bool = False,
) -> ResearchResult:
    """
    Run missing batch research for kind, save hub artifact, validate required fields.

    Debate synthesis and quant predict stages are invoked inside batch pipelines
    when those modules are wired (Tasks 3–4). This orchestrator gates on hub
    freshness, debate presence, and required widget fields.
    """
    resolved_kind = ResearchKind(kind) if isinstance(kind, str) else kind
    contract = get_contract(resolved_kind)
    sym = ticker.strip().upper().replace(".NS", "").replace(".BO", "")

    if not contract.eligibility(sym):
        return ResearchResult(
            status="incomplete",
            kind=resolved_kind,
            ticker=sym,
            error=f"{sym} not eligible for {resolved_kind.value} research",
        )

    stages_run: list[str] = []
    doc: Any | None = None

    debate_required = require_debate
    if debate_required is None:
        debate_stage = next((s for s in contract.stages if s.id == "agent_debate"), None)
        debate_required = bool(debate_stage and debate_stage.required)

    debate_fresh, debate_pending = _debate_stage_status(sym, required=bool(debate_required))
    if debate_fresh:
        stages_run.append("agent_debate")

    use_cache = _is_hub_fresh(sym, resolved_kind, refresh=refresh)
    if use_cache:
        doc = _load_hub_doc(sym, resolved_kind)
        if doc is not None and _hub_doc_needs_data_refresh(doc, resolved_kind):
            use_cache = False
            doc = None
        elif doc is not None:
            stages_run.append(f"{contract.hub_subdir}:cache")

    if doc is None or refresh or not use_cache:
        try:
            from trade_integrations.hub_capture.channel import resolve_registered_entity, warm_entity_channel

            if resolve_registered_entity(sym):
                warm_entity_channel(sym, kind=resolved_kind.value)
                stages_run.append("hub_channel:warm")
        except Exception:
            pass
        try:
            doc = _run_batch_pipeline(
                sym,
                resolved_kind,
                horizon_days=horizon_days,
                expiry_date=expiry_date,
                refresh_constituents=refresh_constituents,
            )
            _save_hub_doc(sym, resolved_kind, doc)
            stages_run.append(f"{contract.hub_subdir}:run")
        except Exception as exc:
            return ResearchResult(
                status="incomplete",
                kind=resolved_kind,
                ticker=sym,
                stages_run=stages_run,
                debate_pending=debate_pending,
                error=str(exc),
            )

    missing = _missing_required_fields(doc, contract.required_widget_fields)
    from trade_integrations.monitor.doc_spot import resolve_doc_spot

    kind_str = "stock" if resolved_kind == ResearchKind.STOCK else "options"
    if resolve_doc_spot(doc, kind=kind_str) is None:  # type: ignore[arg-type]
        if "spot" not in missing:
            missing.append("spot")

    if debate_pending:
        status: ResearchStatus = "partial"
    elif missing:
        status = "incomplete"
    else:
        status = "complete"

    if hasattr(doc, "meta") and isinstance(doc.meta, dict):
        doc.meta["research_orchestrator"] = {
            "status": status,
            "stages_run": stages_run,
            "missing": missing,
            "debate_pending": debate_pending,
        }

    return ResearchResult(
        status=status,
        kind=resolved_kind,
        ticker=sym,
        doc=doc,
        stages_run=stages_run,
        missing=missing,
        debate_pending=debate_pending,
    )


def _stage_is_complete(stage: Any, result: ResearchResult, contract: Any) -> bool:
    """Map orchestrator stages_run tokens to UI/agent stage ids."""
    if stage.id in result.stages_run:
        return True
    if stage.id == "agent_debate" and not result.debate_pending:
        return True
    if result.status == "complete":
        return not (stage.id == "agent_debate" and result.debate_pending)
    hub = getattr(stage, "hub_subdir", None)
    if hub:
        if f"{hub}:cache" in result.stages_run or f"{hub}:run" in result.stages_run:
            return True
    if stage.id == "company_research":
        batch_token = f"{contract.hub_subdir}:cache"
        if batch_token in result.stages_run or f"{contract.hub_subdir}:run" in result.stages_run:
            return True
    if stage.producer in ("synthesis", "live_quote") and result.status in ("complete", "partial"):
        return not result.missing
    return False


def get_research_status(
    ticker: str,
    *,
    kind: ResearchKind | str | None = None,
) -> dict[str, Any]:
    """Expose stage checklist for agent/UI without building a widget."""
    from trade_integrations.research.registry import resolve_kind_for_ticker

    if kind is not None:
        resolved = ResearchKind(kind) if isinstance(kind, str) else kind
    else:
        resolved = resolve_kind_for_ticker(ticker)
    if resolved is None:
        return {"ticker": ticker, "status": "ineligible", "kinds": []}

    contract = get_contract(resolved)
    sym = ticker.strip().upper().replace(".NS", "").replace(".BO", "")
    result = ensure_research_complete(
        sym,
        kind=resolved,
        refresh=False,
        require_debate=False,
    )
    stages = [
        {
            "id": s.id,
            "required": s.required,
            "producer": s.producer,
            "complete": _stage_is_complete(s, result, contract),
        }
        for s in contract.stages
    ]
    staleness_block: dict[str, Any] = {}
    try:
        from trade_integrations.monitor.service import MonitorService

        report = MonitorService().evaluate_ticker(sym, kind=resolved.value)
        if report is not None:
            staleness_block = {
                "staleness_status": report.status,
                "staleness_reasons": list(report.reasons or []),
                "live_spot": report.live_spot,
                "plan_spot": report.plan_spot,
                "suggested_action": report.suggested_action,
            }
    except Exception:
        pass
    return {
        "ticker": sym,
        "kind": resolved.value,
        "status": result.status,
        "stages": stages,
        "missing_fields": result.missing,
        "debate_pending": result.debate_pending,
        "error": result.error,
        **staleness_block,
    }
