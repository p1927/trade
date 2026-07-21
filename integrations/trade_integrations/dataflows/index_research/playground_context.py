"""Bundle headlines, events, and ranked factors for the factor impact workbench."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from trade_integrations.context.hub import get_hub_dir
from trade_integrations.dataflows.index_research.causal_attribution import (
    _NEWS_KEYWORDS,
    _FACTOR_CAUSE_COPY,
)
from trade_integrations.dataflows.index_research.cascade.heuristic_rules import (
    HEURISTIC_CASCADE_RULES,
)
from trade_integrations.dataflows.index_research.simulate import macro_factors_from_rows


def _suggested_factors_from_item(item: dict[str, Any], *, title: str) -> list[str]:
    from trade_integrations.dataflows.index_research.news_tags import factors_from_record

    factors = factors_from_record(item)
    if factors:
        return factors[:4]
    hinted = [
        t.get("factor")
        for t in (item.get("tagged_factors") or [])
        if t.get("factor")
    ]
    return hinted or _headline_factor_hints(title)


def _headline_trigger_from_item(item: dict[str, Any], *, source_label: str) -> dict[str, Any] | None:
    title = str(item.get("title") or "").strip()
    if not title:
        return None
    suggested = _suggested_factors_from_item(item, title=title)
    return {
        "title": title[:200],
        "source": str(item.get("source") or source_label)[:80],
        "content_summary": str(item.get("content_summary") or "")[:400],
        "sources": item.get("sources") or [],
        "tags": item.get("tags") or {},
        "verification_status": item.get("verification_status")
        or (item.get("verification") or {}).get("status"),
        "suggested_factors": suggested,
        "primary_factor": suggested[0] if suggested else "index_sentiment",
        "suggested_shock_pct": 5.0,
        "why": _why_for_factor(suggested[0] if suggested else "index_sentiment"),
        "kind": "verified_headline",
    }


def _headline_factor_hints(title: str) -> list[str]:
    lower = title.lower()
    matched: list[str] = []
    for category, keywords in _NEWS_KEYWORDS.items():
        if any(kw in lower for kw in keywords):
            if category == "oil":
                matched.extend(["oil_brent", "oil_wti"])
            elif category == "fii":
                matched.append("fii_net_5d")
            elif category == "rbi":
                matched.append("repo_rate")
            elif category == "us":
                matched.extend(["sp500", "us_10y"])
            elif category == "earnings":
                matched.append("index_sentiment")
            elif category == "war":
                matched.extend(["oil_brent", "india_vix", "gold"])
    out: list[str] = []
    for factor in matched:
        if factor not in out:
            out.append(factor)
    return out[:3] or ["index_sentiment"]


def _why_for_factor(factor: str, direction: str = "up") -> str:
    copy = _FACTOR_CAUSE_COPY.get(factor, {})
    return copy.get(direction) or copy.get("up") or f"May affect Nifty via {factor.replace('_', ' ')}."


def _group_triggers_by_factor(triggers: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for item in triggers:
        keys = list(item.get("suggested_factors") or [])
        primary = item.get("primary_factor")
        if primary and primary not in keys:
            keys.insert(0, str(primary))
        for factor in keys:
            key = str(factor or "").strip()
            if not key:
                continue
            rows = buckets.setdefault(key, [])
            if not any(r.get("title") == item.get("title") for r in rows):
                rows.append(item)
    return buckets


def _cascade_downstream_map() -> dict[str, list[dict[str, Any]]]:
    return {
        primary: [
            {"factor": secondary, "multiplier": mult, "mode": mode}
            for secondary, mult, mode in rules
        ]
        for primary, rules in HEURISTIC_CASCADE_RULES.items()
    }


def _playground_cache_path(ticker: str = "NIFTY") -> Path:
    return get_hub_dir() / ticker.strip().upper() / "index_research" / "playground_context_latest.json"


def doc_as_of_iso(doc: Any) -> str:
    as_of = getattr(doc, "as_of", None)
    if as_of is None and isinstance(doc, dict):
        as_of = doc.get("as_of")
    if hasattr(as_of, "isoformat"):
        return str(as_of.isoformat())
    return str(as_of or "")


def save_playground_context(context: dict[str, Any], *, ticker: str = "NIFTY") -> Path:
    path = _playground_cache_path(ticker)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(context, indent=2, default=str), encoding="utf-8")
    return path


def load_playground_context(ticker: str = "NIFTY") -> dict[str, Any] | None:
    path = _playground_cache_path(ticker)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _news_impact_from_doc(doc: Any) -> dict[str, Any]:
    news_impact = getattr(doc, "news_impact", None)
    if news_impact is None and isinstance(doc, dict):
        news_impact = doc.get("news_impact")
    return news_impact if isinstance(news_impact, dict) else {}


def _headlines_from_doc_news_impact(doc: Any, *, limit: int = 8) -> list[dict[str, Any]]:
    headlines: list[dict[str, Any]] = []
    for item in (_news_impact_from_doc(doc).get("items") or [])[:limit]:
        if not isinstance(item, dict):
            continue
        trigger = _headline_trigger_from_item(item, source_label="verified_news")
        if trigger:
            headlines.append(trigger)
    return headlines


def _headlines_from_live_fetch(ticker: str, *, limit: int = 8) -> list[dict[str, Any]]:
    """Slow path — network/news hub lookups. Use only when ``live_fetch=True``."""
    from trade_integrations.dataflows.index_research.causal_attribution import _fetch_index_headlines
    from trade_integrations.monitor.news_watcher import check_material_news

    today = date.today().isoformat()
    headlines: list[dict[str, Any]] = []

    try:
        from trade_integrations.dataflows import news_hub_bridge

        news_impact = news_hub_bridge.resolve_news_impact(ticker=ticker, limit=limit)
        for item in (news_impact.get("items") or [])[:limit]:
            trigger = _headline_trigger_from_item(item, source_label="verified_news")
            if trigger:
                headlines.append(trigger)

        if not headlines:
            for item in news_hub_bridge.list_headlines_for_date(today, limit=limit):
                trigger = _headline_trigger_from_item(item, source_label="verified_hub")
                if trigger:
                    headlines.append(trigger)

        if not headlines:
            for item in news_hub_bridge.list_recent_headlines(ticker=ticker, limit=limit):
                trigger = _headline_trigger_from_item(item, source_label="verified_hub")
                if trigger:
                    headlines.append(trigger)
    except Exception:
        pass

    if not headlines:
        headlines_raw = _fetch_index_headlines(today, limit=limit)
        for item in headlines_raw:
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            suggested = _headline_factor_hints(title)
            headlines.append(
                {
                    "title": title[:200],
                    "source": str(item.get("source") or "")[:80],
                    "content_summary": str(item.get("summary") or title)[:400],
                    "suggested_factors": suggested,
                    "primary_factor": suggested[0],
                    "suggested_shock_pct": 5.0,
                    "why": _why_for_factor(suggested[0]),
                    "kind": "headline",
                }
            )

    try:
        news_since = datetime.now(timezone.utc)
        material = check_material_news(ticker, news_since)
        for item in material[:6]:
            title = getattr(item, "title", "") or ""
            if not title or any(h["title"] == title for h in headlines):
                continue
            suggested = _headline_factor_hints(title)
            headlines.append(
                {
                    "title": title[:200],
                    "source": "material_news",
                    "suggested_factors": suggested,
                    "primary_factor": suggested[0],
                    "suggested_shock_pct": 6.0,
                    "why": _why_for_factor(suggested[0]),
                    "kind": "material",
                    "keywords": list(getattr(item, "matched_keywords", ()) or ()),
                }
            )
    except Exception:
        pass

    return headlines[:limit]


def build_playground_context(
    doc: Any,
    *,
    ticker: str = "NIFTY",
    live_fetch: bool = False,
) -> dict[str, Any]:
    """Assemble workbench triggers from hub index research artifact."""
    headlines = _headlines_from_doc_news_impact(doc)
    if not headlines and live_fetch:
        headlines = _headlines_from_live_fetch(ticker)

    events: list[dict[str, Any]] = []
    for ev in doc.upcoming_events or []:
        label = str(ev.get("label") or ev.get("event_type") or "event")
        etype = str(ev.get("event_type") or "")
        primary = "index_sentiment"
        shock = 3.0
        if etype in ("monthly_expiry",):
            primary = "india_vix"
            shock = 4.0
        elif etype in ("rbi_policy", "union_budget"):
            primary = "repo_rate"
            shock = 5.0
        elif "results" in etype or "earnings" in etype:
            primary = "index_sentiment"
            shock = 4.0
        events.append(
            {
                "id": f"upcoming|{ev.get('date')}|{label[:40]}",
                "label": label,
                "date": ev.get("date"),
                "days_from_now": ev.get("days_from_now"),
                "event_type": etype,
                "primary_factor": primary,
                "suggested_shock_pct": shock,
                "why": _why_for_factor(primary),
                "kind": "upcoming",
                "probability": None,
            }
        )

    for curve in doc.event_impact_curves or []:
        eid = f"{curve.get('event')}|{curve.get('outcome')}"
        primary = str(curve.get("primary_factor") or "")
        events.append(
            {
                "id": eid,
                "label": f"{curve.get('event')} — {curve.get('outcome')}",
                "primary_factor": primary,
                "suggested_shock_pct": 10.0,
                "factor_shocks": curve.get("factor_shocks"),
                "why": _why_for_factor(primary) if primary else "Coordinated macro shock scenario.",
                "kind": "scenario",
                "probability": curve.get("probability"),
                "event_preset_id": eid,
            }
        )

    contributors = (doc.factor_explanation or {}).get("contributors") or []
    ranked_factors: list[dict[str, Any]] = []
    for row in contributors[:12]:
        factor = str(row.get("factor") or "")
        if not factor:
            continue
        ranked_factors.append(
            {
                "factor": factor,
                "label": row.get("label") or factor,
                "contribution_pct": row.get("contribution_pct"),
                "value": row.get("value"),
                "share_of_macro": row.get("share_of_macro"),
            }
        )

    global_map = macro_factors_from_rows(doc.global_factors or [])
    for row in doc.global_factors or []:
        key = str(row.get("factor") or "")
        if key and key not in {r["factor"] for r in ranked_factors}:
            ranked_factors.append(
                {
                    "factor": key,
                    "label": row.get("label") or key,
                    "value": row.get("value"),
                    "contribution_pct": None,
                }
            )

    cascade_cal = getattr(doc, "cascade_calibration", None) or {}
    cascade_summary = {
        "status": cascade_cal.get("status"),
        "as_of": cascade_cal.get("as_of"),
        "method": cascade_cal.get("method"),
        "regime": cascade_cal.get("regime"),
        "blend_alpha": cascade_cal.get("blend_alpha"),
    }

    all_triggers = headlines[:12] + events[:16]
    factor_news = _group_triggers_by_factor(all_triggers)

    return {
        "ticker": ticker,
        "as_of": doc_as_of_iso(doc),
        "spot": doc.spot,
        "horizon_days": (doc.horizon or {}).get("days"),
        "headlines": headlines[:12],
        "events": events[:16],
        "factor_news": factor_news,
        "cascade_downstream": _cascade_downstream_map(),
        "ranked_factors": ranked_factors[:16],
        "event_impact_curves": doc.event_impact_curves or [],
        "global_factors": global_map,
        "baseline_return_pct": (doc.prediction or {}).get("expected_return_pct"),
        "cascade_calibration": cascade_summary,
    }


def resolve_playground_context(
    doc: Any,
    *,
    ticker: str = "NIFTY",
    refresh: bool = False,
) -> dict[str, Any]:
    """Return cached playground context when ``as_of`` matches, else rebuild and persist."""
    doc_as_of = doc_as_of_iso(doc)[:19]
    if not refresh:
        cached = load_playground_context(ticker)
        if cached and str(cached.get("as_of") or "")[:19] == doc_as_of:
            return cached
    ctx = build_playground_context(doc, ticker=ticker, live_fetch=bool(refresh))
    save_playground_context(ctx, ticker=ticker)
    return ctx
