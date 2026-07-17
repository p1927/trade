"""MCP-facing handlers for news scenario pipeline tools."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from trade_integrations.dataflows.index_research.news_event_scenarios import (
    NewsScenarioError,
    load_news_event_scenario,
    list_recent_news_scenarios,
    run_news_event_scenario,
    save_news_scenario_draft,
)
from trade_integrations.dataflows.index_research.pipeline_snapshot import (
    PipelineSnapshotError,
    resolve_bound_pipeline_doc,
    snapshot_summary,
)
from trade_integrations.dataflows.index_research.playground_context import build_playground_context
from trade_integrations.dataflows.index_research.simulate import (
    macro_factors_from_rows,
    simulate_index_prediction,
)
from trade_integrations.dataflows.index_research.cascade.calibration_store import (
    load_calibration_from_doc,
)


def _error_payload(exc: Exception) -> str:
    if isinstance(exc, PipelineSnapshotError):
        return json.dumps({"status": "error", **exc.to_dict()}, indent=2)
    if isinstance(exc, NewsScenarioError):
        return json.dumps({"status": "error", **exc.to_dict()}, indent=2)
    return json.dumps({"status": "error", "message": str(exc)}, indent=2)


def tool_get_pipeline_snapshot(ticker: str, pipeline_as_of: str) -> str:
    try:
        doc, _ = resolve_bound_pipeline_doc(ticker, pipeline_as_of)
        return json.dumps({"status": "ok", "snapshot": snapshot_summary(doc)}, indent=2, default=str)
    except Exception as exc:
        return _error_payload(exc)


def tool_query_factor_explanation(ticker: str, pipeline_as_of: str, limit: int = 8) -> str:
    try:
        doc, _ = resolve_bound_pipeline_doc(ticker, pipeline_as_of)
        contributors = (doc.factor_explanation or {}).get("contributors") or []
        return json.dumps(
            {
                "status": "ok",
                "contributors": contributors[:limit],
                "global_factors": (doc.global_factors or [])[:20],
            },
            indent=2,
            default=str,
        )
    except Exception as exc:
        return _error_payload(exc)


def tool_query_factor_sensitivity(ticker: str, pipeline_as_of: str, limit: int = 8) -> str:
    try:
        doc, _ = resolve_bound_pipeline_doc(ticker, pipeline_as_of)
        return json.dumps(
            {
                "status": "ok",
                "factor_sensitivity": (doc.factor_sensitivity or [])[:limit],
                "event_impact_curves": (doc.event_impact_curves or [])[:limit],
            },
            indent=2,
            default=str,
        )
    except Exception as exc:
        return _error_payload(exc)


def tool_query_equation_coefficients(ticker: str, pipeline_as_of: str) -> str:
    try:
        doc, model = resolve_bound_pipeline_doc(ticker, pipeline_as_of)
        pred = doc.prediction or {}
        return json.dumps(
            {
                "status": "ok",
                "equation": pred.get("equation"),
                "model_artifact": model,
            },
            indent=2,
            default=str,
        )
    except Exception as exc:
        return _error_payload(exc)


def tool_query_constituent_drivers(ticker: str, pipeline_as_of: str, limit: int = 10) -> str:
    try:
        doc, _ = resolve_bound_pipeline_doc(ticker, pipeline_as_of)
        signals = sorted(
            doc.constituent_signals or [],
            key=lambda row: abs(float(row.get("contribution_to_index_pct") or 0)),
            reverse=True,
        )
        return json.dumps(
            {
                "status": "ok",
                "constituent_signals": signals[:limit],
                "sector_breadth": doc.sector_breadth,
            },
            indent=2,
            default=str,
        )
    except Exception as exc:
        return _error_payload(exc)


def tool_get_pipeline_news_items(
    ticker: str,
    pipeline_as_of: str,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 20,
) -> str:
    try:
        doc, _ = resolve_bound_pipeline_doc(ticker, pipeline_as_of)
        items = list((doc.news_impact or {}).get("items") or [])
        if start_date and end_date:
            try:
                start = date.fromisoformat(start_date[:10])
                end = date.fromisoformat(end_date[:10])

                def _in_range(item: dict[str, Any]) -> bool:
                    pub = str(item.get("publish_date") or item.get("published_at") or "")[:10]
                    if not pub:
                        return True
                    try:
                        d = date.fromisoformat(pub)
                    except ValueError:
                        return True
                    return start <= d <= end

                items = [i for i in items if isinstance(i, dict) and _in_range(i)]
            except ValueError:
                pass
        return json.dumps({"status": "ok", "items": items[:limit]}, indent=2, default=str)
    except Exception as exc:
        return _error_payload(exc)


def tool_get_playground_context(ticker: str, pipeline_as_of: str) -> str:
    try:
        doc, _ = resolve_bound_pipeline_doc(ticker, pipeline_as_of)
        ctx = build_playground_context(doc, ticker=ticker)
        return json.dumps({"status": "ok", "context": ctx}, indent=2, default=str)
    except Exception as exc:
        return _error_payload(exc)


def tool_simulate_pipeline_scenario(
    ticker: str,
    pipeline_as_of: str,
    *,
    factor_overrides: dict[str, float] | None = None,
    primary_factor: str | None = None,
    primary_shock_pct: float | None = None,
    horizon_days: int | None = None,
) -> str:
    try:
        doc, _ = resolve_bound_pipeline_doc(ticker, pipeline_as_of)
        macro = macro_factors_from_rows(doc.global_factors or [])
        pred = doc.prediction or {}
        india_vix = macro.get("india_vix")
        if india_vix is None and isinstance(doc.regime, dict):
            india_vix = doc.regime.get("india_vix")
        sim = simulate_index_prediction(
            macro_factors=macro,
            factor_overrides=factor_overrides,
            spot=float(doc.spot or 0),
            bottom_up_return_pct=float(pred.get("bottom_up_return_pct") or 0.0),
            horizon_days=horizon_days or (doc.horizon or {}).get("days"),
            headline_return_pct=float(pred.get("expected_return_pct") or 0.0),
            primary_factor=primary_factor,
            primary_shock_pct=primary_shock_pct,
            event_impact_curves=doc.event_impact_curves,
            cascade_calibration=load_calibration_from_doc(doc),
            india_vix=india_vix,
        )
        return json.dumps({"status": "ok", "simulation": sim}, indent=2, default=str)
    except Exception as exc:
        return _error_payload(exc)


def tool_save_news_scenario_draft(
    ticker: str,
    pipeline_as_of: str,
    draft_json: str,
) -> str:
    try:
        draft = json.loads(draft_json) if isinstance(draft_json, str) else draft_json
        if not isinstance(draft, dict):
            raise ValueError("draft must be a JSON object")
        saved = save_news_scenario_draft(
            ticker=ticker,
            pipeline_as_of=pipeline_as_of,
            draft=draft,
        )
        return json.dumps({"status": "ok", "draft": saved}, indent=2, default=str)
    except Exception as exc:
        return _error_payload(exc)


def tool_run_news_event_scenario(
    ticker: str,
    pipeline_as_of: str,
    draft_id: str,
    session_id: str | None = None,
) -> str:
    try:
        product = run_news_event_scenario(
            ticker=ticker,
            pipeline_as_of=pipeline_as_of,
            draft_id=draft_id,
            session_id=session_id,
        )
        payload: dict[str, Any] = {"status": "ok", "scenario": product}
        if product.get("warnings"):
            payload["warnings"] = product["warnings"]
        if product.get("errors"):
            payload["errors"] = product["errors"]
        return json.dumps(payload, indent=2, default=str)
    except Exception as exc:
        return _error_payload(exc)


def tool_get_news_scenario_widget(
    ticker: str,
    pipeline_as_of: str,
    scenario_id: str,
    selected_outcome_id: str | None = None,
) -> str:
    try:
        from trade_integrations.dataflows.index_research.news_scenario_widget import (
            build_news_scenario_widget,
        )

        resolve_bound_pipeline_doc(ticker, pipeline_as_of)
        widget = build_news_scenario_widget(
            ticker=ticker,
            scenario_id=scenario_id,
            selected_outcome_id=selected_outcome_id,
        )
        from trade_integrations.trade_widgets.store import persist_trade_widget

        scenario = load_news_event_scenario(ticker, scenario_id)
        if isinstance(scenario, dict):
            meta = widget.setdefault("meta", {})
            if scenario.get("warnings"):
                meta["scenario_warnings"] = scenario["warnings"]
            if scenario.get("errors"):
                meta["scenario_errors"] = scenario["errors"]
                widget["plan_status"] = scenario.get("status") or "partial"
        persist_trade_widget(widget)
        return json.dumps(widget, indent=2, default=str)
    except Exception as exc:
        return _error_payload(exc)
