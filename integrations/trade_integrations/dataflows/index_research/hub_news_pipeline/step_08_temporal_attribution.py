"""Step 08 — temporal attribution filters for prediction consumers."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any


def _parse_day(raw: str) -> date | None:
    text = (raw or "").strip()[:10]
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _cause_indicator_lists(record: dict[str, Any]) -> list[list[dict[str, Any]]]:
    sources: list[list[dict[str, Any]]] = []
    enrichment = record.get("article_enrichment")
    if isinstance(enrichment, dict):
        rows = enrichment.get("cause_indicators") or []
        if isinstance(rows, list):
            sources.append([row for row in rows if isinstance(row, dict)])
    em = record.get("event_meta")
    if isinstance(em, dict):
        rows = em.get("cause_indicators") or []
        if isinstance(rows, list):
            sources.append([row for row in rows if isinstance(row, dict)])
    ss = record.get("structured_summary")
    if isinstance(ss, dict):
        inner = ss.get("event_meta") or {}
        if isinstance(inner, dict):
            rows = inner.get("cause_indicators") or []
            if isinstance(rows, list):
                sources.append([row for row in rows if isinstance(row, dict)])
    structured = record.get("structured_enrichment")
    if isinstance(structured, dict):
        rows = structured.get("cause_indicators") or []
        if isinstance(rows, list):
            sources.append([row for row in rows if isinstance(row, dict)])
    pa = record.get("prediction_attribution")
    if isinstance(pa, dict):
        rows = pa.get("cause_indicators") or []
        if isinstance(rows, list):
            sources.append([row for row in rows if isinstance(row, dict)])
    return sources


def has_cause_indicators(record: dict[str, Any]) -> bool:
    return any(rows for rows in _cause_indicator_lists(record))


def strip_article_opinions(record: dict[str, Any]) -> dict[str, Any]:
    """Remove article price predictions from attribution payload; keep audit elsewhere."""

    def _strip_enrichment(blob: dict[str, Any]) -> dict[str, Any]:
        out = dict(blob)
        out.pop("article_opinions", None)
        return out

    out = dict(record)
    enrichment = out.get("article_enrichment")
    if isinstance(enrichment, dict):
        out["article_enrichment"] = _strip_enrichment(enrichment)
    structured = out.get("structured_enrichment")
    if isinstance(structured, dict):
        out["structured_enrichment"] = _strip_enrichment(structured)
    em = out.get("event_meta")
    if isinstance(em, dict):
        out["event_meta"] = _strip_enrichment(em)
    ss = out.get("structured_summary")
    if isinstance(ss, dict):
        ss = dict(ss)
        inner = ss.get("event_meta")
        if isinstance(inner, dict):
            ss["event_meta"] = _strip_enrichment(inner)
        out["structured_summary"] = ss
    pa = out.get("prediction_attribution")
    if isinstance(pa, dict):
        out["prediction_attribution"] = _strip_enrichment(pa)

    refs = out.get("references")
    if isinstance(refs, list):
        cleaned_refs: list[Any] = []
        for ref in refs:
            if isinstance(ref, dict):
                ref_out = dict(ref)
                se = ref_out.get("structured_enrichment")
                if isinstance(se, dict):
                    ref_out["structured_enrichment"] = _strip_enrichment(se)
                ae = ref_out.get("article_enrichment")
                if isinstance(ae, dict):
                    ref_out["article_enrichment"] = _strip_enrichment(ae)
                cleaned_refs.append(ref_out)
            else:
                cleaned_refs.append(ref)
        out["references"] = cleaned_refs

    return out


def future_events_in_horizon(
    events: list[dict[str, Any]],
    *,
    prediction_date: str,
    horizon_days: int = 14,
) -> list[dict[str, Any]]:
    """Keep future events whose expected_date falls within [pred, pred+horizon]."""
    pred = _parse_day(prediction_date)
    if pred is None:
        return []
    end = pred + timedelta(days=max(horizon_days, 0))
    kept: list[dict[str, Any]] = []
    for row in events:
        if not isinstance(row, dict):
            continue
        expected = _parse_day(str(row.get("expected_date") or ""))
        if expected is None:
            continue
        if pred <= expected <= end:
            kept.append(row)
    return kept


def facts_with_as_of(enrichment: dict[str, Any] | None) -> list[dict[str, str]]:
    """Normalize facts to always include as_of."""
    if not isinstance(enrichment, dict):
        return []
    publish_day = str(enrichment.get("publish_day") or enrichment.get("published_at") or "")[:10]
    out: list[dict[str, str]] = []
    for row in enrichment.get("facts") or []:
        if isinstance(row, dict):
            text = str(row.get("text") or "").strip()
            as_of = str(row.get("as_of") or publish_day)[:40]
        else:
            text = str(row or "").strip()
            as_of = publish_day
        if text:
            out.append({"text": text[:400], "as_of": as_of})
    return out


def enriched_prediction_value_score(record: dict[str, Any]) -> float:
    enrichment = record.get("article_enrichment")
    if isinstance(enrichment, dict):
        try:
            return float(enrichment.get("prediction_value_score") or 0.0)
        except (TypeError, ValueError):
            pass
    return 0.0


def _enrichment_from_record(item: dict[str, Any]) -> dict[str, Any]:
    enrichment = item.get("article_enrichment")
    if isinstance(enrichment, dict) and enrichment:
        return dict(enrichment)
    for key in ("event_meta",):
        em = item.get(key)
        if isinstance(em, dict) and (em.get("cause_indicators") or em.get("future_events") or em.get("facts")):
            return {
                "cause_indicators": list(em.get("cause_indicators") or []),
                "future_events": list(em.get("future_events") or []),
                "facts": list(em.get("facts") or []),
                "publish_day": str(item.get("publish_day") or item.get("published_at") or "")[:10],
            }
    ss = item.get("structured_summary")
    if isinstance(ss, dict):
        inner = ss.get("event_meta") or {}
        if isinstance(inner, dict) and (
            inner.get("cause_indicators") or inner.get("future_events") or inner.get("facts")
        ):
            return {
                "cause_indicators": list(inner.get("cause_indicators") or []),
                "future_events": list(inner.get("future_events") or []),
                "facts": list(inner.get("facts") or []),
                "publish_day": str(item.get("publish_day") or item.get("published_at") or "")[:10],
            }
    structured = item.get("structured_enrichment")
    if isinstance(structured, dict):
        return {
            "cause_indicators": list(structured.get("cause_indicators") or []),
            "future_events": list(structured.get("future_events") or []),
            "facts": list(structured.get("facts") or []),
            "publish_day": str(item.get("publish_day") or item.get("published_at") or "")[:10],
        }
    return {}


def enrich_item_for_prediction(
    item: dict[str, Any],
    *,
    prediction_date: str,
    horizon_days: int = 14,
    ticker: str = "NIFTY",
) -> dict[str, Any]:
    """Attach cause/future attribution fields; strip opinions."""
    out = strip_article_opinions(dict(item))
    enrichment = _enrichment_from_record(out)

    causes = list(enrichment.get("cause_indicators") or [])
    future = future_events_in_horizon(
        list(enrichment.get("future_events") or []),
        prediction_date=prediction_date,
        horizon_days=horizon_days,
    )
    facts = facts_with_as_of(enrichment if isinstance(enrichment, dict) else None)

    publish_day = (
        str(enrichment.get("publish_day") or out.get("publish_day") or out.get("published_at") or "")[:10]
        or prediction_date[:10]
    )

    from trade_integrations.dataflows.index_research.news_market_context import (
        get_market_context_as_of,
    )

    out["prediction_attribution"] = {
        "cause_indicators": causes[:20],
        "future_events": future[:20],
        "facts": facts[:24],
        "prediction_date": prediction_date[:10],
        "horizon_days": horizon_days,
        "publish_day": publish_day,
        "market_context_as_of": get_market_context_as_of(publish_day, ticker=ticker),
    }
    return out


def attribution_items_for_prediction(
    items: list[dict[str, Any]],
    *,
    prediction_date: str,
    horizon_days: int = 14,
    ticker: str = "NIFTY",
) -> list[dict[str, Any]]:
    return [
        enrich_item_for_prediction(
            row,
            prediction_date=prediction_date,
            horizon_days=horizon_days,
            ticker=ticker,
        )
        for row in items
        if isinstance(row, dict)
    ]


def prepare_items_for_prediction_attribution(
    items: list[dict[str, Any]],
    *,
    prediction_date: str | None = None,
    horizon_days: int = 14,
    ticker: str = "NIFTY",
) -> list[dict[str, Any]]:
    """Single gate for prediction/analysis reads: visibility filter + temporal attribution."""
    from trade_integrations.dataflows.company_research.market import india_trading_date_iso
    from trade_integrations.dataflows.index_research.news_prediction_visibility import (
        filter_prediction_attribution_items,
    )

    pred = (prediction_date or india_trading_date_iso())[:10]
    filtered = filter_prediction_attribution_items(items)
    return attribution_items_for_prediction(
        filtered,
        prediction_date=pred,
        horizon_days=horizon_days,
        ticker=ticker,
    )


def prediction_date_from_doc(doc: Any | None) -> str:
    """Best-effort prediction as-of day from index doc or live IST."""
    from trade_integrations.dataflows.company_research.market import india_trading_date_iso

    if doc is None:
        return india_trading_date_iso()
    for attr in ("as_of_day", "prediction_date", "trading_day"):
        val = getattr(doc, attr, None)
        if val:
            return str(val)[:10]
    embedded = getattr(doc, "news_impact", None)
    if isinstance(embedded, dict):
        for key in ("prediction_date", "as_of", "as_of_day"):
            val = embedded.get(key)
            if val:
                return str(val)[:10]
    return india_trading_date_iso()
