"""Aggregate hub inventory for the /hub status API."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trade_integrations.context.hub import get_hub_dir, is_cache_fresh, load_index_research_json
from trade_integrations.dataflows.index_research.constituents import load_nifty50_constituents
from trade_integrations.dataflows.index_research.data_completeness import measure_flow_coverage
from trade_integrations.dataflows.source_availability import list_all_statuses
from trade_integrations.dataflows.index_research.news_entity_worker import load_worker_last_summary
from trade_integrations.hub_storage.news_staging_store import (
    is_entity_pipeline_enabled,
    list_pending_refs,
    pipeline_pause_status,
    staging_queue_stats,
)
from trade_integrations.hub_storage.verified_news_store import count_verified_records, list_verified_records


def _parse_json_dict(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            import json

            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _normalize_news_item_for_hub(row: dict[str, Any]) -> dict[str, Any]:
    """Shape hub/staging rows for the /hub news inventory UI."""
    provenance = str(row.get("provenance") or "").strip().lower()
    if not provenance:
        provenance = "staging" if str(row.get("verification_status") or "") == "pending" else "distilled"

    structured = _parse_json_dict(row.get("structured_summary"))
    event_meta = structured.get("event_meta") if isinstance(structured.get("event_meta"), dict) else {}
    references = [r for r in (event_meta.get("references") or []) if isinstance(r, dict)]

    sources = [s for s in (row.get("sources") or []) if isinstance(s, dict)]
    url = str(row.get("url") or "").strip()
    if not sources and url:
        sources = [
            {
                "vendor": str(row.get("source") or "unknown"),
                "publisher": str(row.get("source") or "unknown"),
                "url": url,
            }
        ]

    if provenance == "staging" and not references:
        references = [
            {
                "ref_id": row.get("ref_id") or row.get("id") or row.get("canonical_story_id"),
                "title": row.get("title") or "",
                "url": url,
                "source": row.get("source") or "staging",
                "published_at": row.get("published_at") or "",
            }
        ]

    ref_count = int(event_meta.get("ref_count") or len(references) or len(sources) or 1)
    consensus = row.get("consensus") if isinstance(row.get("consensus"), dict) else event_meta.get("consensus")
    timeline = row.get("timeline") if isinstance(row.get("timeline"), list) else event_meta.get("timeline")
    event_id = str(row.get("event_id") or event_meta.get("event_id") or row.get("canonical_story_id") or row.get("id") or "")
    market_impact_status = str(
        row.get("market_impact_status")
        or event_meta.get("market_impact_status")
        or ""
    ).strip()
    if not market_impact_status:
        actual = row.get("actual_impact") or row.get("actual") or {}
        predicted = row.get("predicted_impact") or row.get("predicted") or {}
        if isinstance(actual, dict) and actual.get("nifty_points") is not None:
            market_impact_status = "observed"
        elif isinstance(predicted, dict) and predicted.get("nifty_points") is not None:
            market_impact_status = "predicted"
        elif event_meta.get("distilled_by") == "rule_fallback":
            market_impact_status = "claimed"
        else:
            market_impact_status = "unverified"

    return {
        "id": str(row.get("canonical_story_id") or row.get("id") or row.get("ref_id") or ""),
        "ref_id": str(row.get("ref_id") or row.get("id") or ""),
        "event_id": event_id,
        "title": str(row.get("title") or "")[:220],
        "summary": str(row.get("content_summary") or row.get("summary") or "")[:600],
        "url": url,
        "source": str(row.get("source") or ""),
        "published_at": str(row.get("published_at") or ""),
        "created_at": str(row.get("created_at") or ""),
        "ticker": str(row.get("ticker") or "NIFTY").upper(),
        "provenance": provenance,
        "verification_status": str(
            row.get("verification_status") or ("pending" if provenance == "staging" else "")
        ),
        "market_impact_status": market_impact_status,
        "event_kind": str(row.get("event_kind") or event_meta.get("event_kind") or ""),
        "parent_event_id": str(row.get("parent_event_id") or event_meta.get("parent_event_id") or "") or None,
        "sources": sources[:12],
        "references": references[:20],
        "ref_count": ref_count,
        "timeline": [t for t in (timeline or []) if isinstance(t, dict)][:30],
        "consensus": consensus if isinstance(consensus, dict) else {},
        "predicted_impact": row.get("predicted_impact") or row.get("predicted") or {},
        "actual_impact": row.get("actual_impact") or row.get("actual") or {},
        "tags": row.get("tags") if isinstance(row.get("tags"), dict) else {},
    }


def _recent_news_inventory(*, ticker: str, limit: int = 40) -> dict[str, Any]:
    """Union of distilled hub events + staging pending refs for the Hub page."""
    from trade_integrations.dataflows.news_hub_bridge import query_verified_news
    from trade_integrations.hub_storage.news_staging_store import (
        list_pending_refs,
        staging_queue_stats,
    )

    sym = ticker.strip().upper()
    pending_stats = staging_queue_stats(ticker=sym)

    union_raw = query_verified_news(
        ticker=sym,
        status=["approved", "partial"],
        limit=max(limit, 50),
        include_rejected=False,
    )
    distilled_items = [
        _normalize_news_item_for_hub(row)
        for row in union_raw
        if str(row.get("title") or "").strip() and row.get("provenance") != "staging"
    ]
    staging_queue = [
        _normalize_news_item_for_hub(row)
        for row in union_raw
        if row.get("provenance") == "staging"
    ]
    if not staging_queue:
        from trade_integrations.hub_storage.news_staging_store import (
            collect_distilled_urls,
            filter_staging_refs_not_in_urls,
            staging_ref_to_headline,
        )

        seen_urls = collect_distilled_urls(union_raw)
        staging_refs = filter_staging_refs_not_in_urls(
            list_pending_refs(ticker=sym, limit=min(limit, 80)),
            seen_urls,
        )
        staging_queue = [
            _normalize_news_item_for_hub({**staging_ref_to_headline(ref), **ref})
            for ref in staging_refs
        ]

    staging_cap = min(len(staging_queue), max(20, limit // 2))
    items = staging_queue[:staging_cap] + distilled_items[: max(0, limit - staging_cap)]

    staging_in_union = sum(1 for item in items if item.get("provenance") == "staging")

    from trade_integrations.dataflows.index_research.news_discard import list_discarded

    discarded_raw = list_discarded(ticker=sym, limit=50)
    discarded_items = [
        {
            "discard_id": row.get("discard_id"),
            "id": row.get("discard_id"),
            "ref_id": row.get("ref_id"),
            "event_id": row.get("event_id"),
            "title": row.get("title"),
            "url": row.get("url"),
            "reason": row.get("reason"),
            "source_kind": row.get("source_kind"),
            "discarded_at": row.get("discarded_at"),
            "expires_at": row.get("expires_at"),
            "provenance": "discarded",
            "ticker": row.get("ticker"),
            "relevance": row.get("relevance") or {},
        }
        for row in discarded_raw
    ]

    return {
        "pending_count": int(pending_stats.get("queued") or 0),
        "union_count": len(items),
        "staging_in_union": staging_in_union,
        "distilled_in_union": max(0, len(items) - staging_in_union),
        "discarded_count": len(discarded_items),
        "items": items,
        "staging_queue": staging_queue,
        "discarded_items": discarded_items,
    }


def _hub_relative(path: Path) -> str:
    hub = get_hub_dir()
    try:
        return str(path.relative_to(hub))
    except ValueError:
        return str(path)


def _staging_by_ticker(*, limit: int = 10) -> list[dict[str, Any]]:
    counts: Counter[str] = Counter()
    for ref in list_pending_refs(ticker=None, limit=10_000):
        sym = str(ref.get("ticker") or "NIFTY").strip().upper()
        counts[sym] += 1
    return [{"ticker": t, "queued": n} for t, n in counts.most_common(limit)]


def _verified_breakdown(tickers: list[str]) -> dict[str, Any]:
    from trade_integrations.hub_storage.news_events_store import count_events

    out: dict[str, Any] = {}
    for ticker in tickers:
        sym = ticker.strip().upper()
        records = list_verified_records(ticker=sym, limit=5000, include_rejected=True)
        status_counts: Counter[str] = Counter()
        for row in records:
            status_counts[str(row.get("verification_status") or "unknown")] += 1
        out[sym] = {
            "total": count_verified_records(ticker=sym),
            "events_count": count_events(ticker=sym),
            "by_status": dict(status_counts),
        }
    return out


def _constituent_cache_stats(constituents: list[Any] | None = None) -> dict[str, Any]:
    if constituents is None:
        constituents = load_nifty50_constituents()
    fresh = stale = missing = 0
    for row in constituents:
        symbol = (
            str(row.get("symbol") or "")
            if isinstance(row, dict)
            else str(getattr(row, "symbol", "") or "")
        ).strip().upper()
        if not symbol:
            continue
        doc_path = get_hub_dir() / symbol / "company_research" / "latest.json"
        if not doc_path.is_file():
            missing += 1
        elif is_cache_fresh(symbol):
            fresh += 1
        else:
            stale += 1
    total = fresh + stale + missing
    return {
        "total": total,
        "fresh": fresh,
        "stale": stale,
        "missing": missing,
    }


def _index_research_summary(ticker: str = "NIFTY") -> dict[str, Any]:
    doc = load_index_research_json(ticker)
    if doc is None:
        return {"ticker": ticker, "present": False}
    as_of = getattr(doc, "as_of", None)
    if hasattr(as_of, "isoformat"):
        as_of_str = as_of.isoformat()
    else:
        as_of_str = str(as_of) if as_of else None
    pipeline_log = list(getattr(doc, "pipeline_log", None) or [])
    last_stage = pipeline_log[-1] if pipeline_log else {}
    horizon = getattr(doc, "horizon", None) or {}
    return {
        "ticker": ticker,
        "present": True,
        "as_of": as_of_str,
        "horizon": horizon if isinstance(horizon, dict) else {},
        "last_pipeline_stage": last_stage.get("stage") if isinstance(last_stage, dict) else None,
        "last_pipeline_message": last_stage.get("message") if isinstance(last_stage, dict) else None,
    }


def _hub_paths() -> dict[str, str]:
    hub = get_hub_dir()
    paths = {
        "hub_root": _hub_relative(hub),
        "news_staging_pending": _hub_relative(hub / "_data" / "news_staging" / "pending.jsonl"),
        "news_events": _hub_relative(hub / "_data" / "news_events" / "events.parquet"),
        "news_event_index": _hub_relative(hub / "_data" / "news_events" / "event_index.parquet"),
        "news_events_migration_state": _hub_relative(
            hub / "_data" / "news_events" / "migration_state.json"
        ),
        "llm_wiki_project": _hub_relative(hub / "llm-wiki"),
        "index_research_latest": _hub_relative(hub / "NIFTY" / "index_research" / "latest.json"),
        "capture_registry": _hub_relative(hub / "_data" / "capture_registry.json"),
        "worker_last": _hub_relative(hub / "_data" / "news_staging" / "worker_last.json"),
    }
    return paths


_NEWS_MIGRATION_ACTION = "python scripts/migrate_hub_news_records_once.py --apply"


def _build_hub_gates(*, migration_state: dict[str, Any]) -> dict[str, Any]:
    """Fail-closed rollup for hub readiness — consumers need not inspect nested migration fields."""
    blocking: list[dict[str, Any]] = []
    if migration_state.get("error"):
        blocking.append(
            {
                "id": "news_events_migration",
                "passes": False,
                "needed": True,
                "action": _NEWS_MIGRATION_ACTION,
                "user_message": f"News migration state unavailable: {migration_state['error']}",
            }
        )
    elif migration_state.get("needed"):
        blocking.append(
            {
                "id": "news_events_migration",
                "passes": False,
                "needed": True,
                "action": _NEWS_MIGRATION_ACTION,
                "user_message": (
                    "Legacy news records must be migrated to the events SSOT before hub news is reliable. "
                    f"Run: {_NEWS_MIGRATION_ACTION}"
                ),
            }
        )
    return {
        "hub_ready": not blocking,
        "blocking": blocking,
    }


def build_hub_status(*, entity_id: str = "NIFTY") -> dict[str, Any]:
    """Return structured hub inventory for UI and debugging."""
    sym = entity_id.strip().upper()
    hub = get_hub_dir()

    try:
        from trade_integrations.hub_capture.registry import build_capture_stats
        from trade_integrations.hub_capture.rollup import capture_coverage_stats

        capture_stats = build_capture_stats(sym)
        capture_coverage = capture_coverage_stats(entity_id=sym)
    except Exception as exc:
        capture_stats = {"error": str(exc)}
        capture_coverage = {}

    sample_tickers = [sym, "BANKNIFTY"]
    constituents = load_nifty50_constituents()
    for row in constituents[:5]:
        symbol = (
            str(row.get("symbol") or "")
            if isinstance(row, dict)
            else str(getattr(row, "symbol", "") or "")
        ).strip().upper()
        if symbol and symbol not in sample_tickers:
            sample_tickers.append(symbol)

    pause = pipeline_pause_status(ticker=sym)
    pipeline_status: dict[str, Any] = {}
    try:
        from trade_integrations.dataflows.news_hub_bridge import hub_news_pipeline_status

        pipeline_status = hub_news_pipeline_status(ticker=sym)
    except Exception as exc:
        pipeline_status = {"error": str(exc)}

    migration_state: dict[str, Any] = {}
    try:
        from trade_integrations.hub_storage.news_migrations import (
            load_migration_state,
            needs_news_migration,
        )

        migration_state = {
            "needed": needs_news_migration(ticker=sym),
            "state": load_migration_state(),
        }
    except Exception as exc:
        migration_state = {"error": str(exc)}

    gates = _build_hub_gates(migration_state=migration_state)
    migration_gate_active = not gates.get("hub_ready", True)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "entity_id": sym,
        "hub_dir": str(hub),
        "paths": _hub_paths(),
        "gates": gates,
        "news_staging": {
            "entity_pipeline_enabled": is_entity_pipeline_enabled(),
            "pipeline_paused": bool(pause.get("pipeline_paused")) or migration_gate_active,
            "pause_reason": (
                "news_events_migration_required"
                if migration_gate_active
                else str(pause.get("pause_reason") or "")
            ),
            "user_message": (
                str((gates.get("blocking") or [{}])[0].get("user_message") or "")
                if migration_gate_active
                else str(pause.get("user_message") or "")
            ),
            "llm_wiki_ok": bool(pause.get("llm_wiki_ok", True)),
            "llm_wiki_required": bool(pause.get("llm_wiki_required", False)),
            "minimax_configured": bool(pause.get("minimax_configured")),
            **staging_queue_stats(ticker=None),
            "by_ticker": _staging_by_ticker(limit=12),
            "worker_last": load_worker_last_summary(),
        },
        "news_inventory": _recent_news_inventory(ticker=sym, limit=50),
        "news_events_migration": migration_state,
        "news_pipeline": pipeline_status,
        "verified_news": _verified_breakdown(sample_tickers),
        "index_research": _index_research_summary(sym),
        "constituent_cache": _constituent_cache_stats(constituents),
        "capture": {
            "stats": capture_stats,
            "coverage": capture_coverage,
        },
        "factor_coverage": measure_flow_coverage(allow_live_fetch=False),
        "source_availability": list_all_statuses(),
    }
