"""News → Nifty impact pipeline (hub SSOT, cache-first verification)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trade_integrations.context.hub import get_hub_dir
from trade_integrations.dataflows.index_research.horizon import resolve_horizon
from trade_integrations.dataflows.index_research.horizon_dates import resolve_maturity_trading_date
from trade_integrations.dataflows.index_research.news_collect import collect_headlines_for_day
from trade_integrations.dataflows.index_research.news_dedup import (
    merge_raw_headlines,
    sources_changed,
    story_key_from_row,
)
from trade_integrations.dataflows.index_research.news_enrichment import enrich_headline
from trade_integrations.dataflows.index_research.news_verification import (
    is_approved_status,
    verify_enriched_news,
)
from trade_integrations.dataflows.index_research.playground_context import _headline_factor_hints
from trade_integrations.dataflows.index_research.prediction_miss_analysis import factor_snapshot_at
from trade_integrations.dataflows.index_research.simulate import simulate_index_prediction
from trade_integrations.dataflows.index_research.sources.history_loader import load_aligned_factor_history
from trade_integrations.hub_storage.verified_news_store import (
    append_impact_ledger_row,
    build_snapshot_from_hub,
    get_verified_record,
    list_verified_records,
    upsert_verified_record,
)
from trade_integrations.research.debate_synthesis import extract_structured_debate


def _impact_snapshot_path(ticker: str = "NIFTY") -> Path:
    return get_hub_dir() / ticker.strip().upper() / "index_research" / "news_impact_latest.json"


def _tag_factors(title: str, summary: str, implied: list[str]) -> list[dict[str, Any]]:
    hints = _headline_factor_hints(title) or _headline_factor_hints(summary)
    factors = []
    for f in (implied or []) + hints:
        if f and f not in factors:
            factors.append(f)
    return [{"factor": f, "confidence": 0.75, "method": "verified_summary"} for f in factors[:4]]


def _predict_impact(
    *,
    spot: float,
    macro_factors: dict[str, float],
    primary_factor: str,
    horizon_days: int,
) -> dict[str, Any]:
    if spot <= 0 or not primary_factor:
        return {"return_pct": 0.0, "nifty_points": 0.0, "model": "ridge_shock_v1"}
    try:
        result = simulate_index_prediction(
            macro_factors=macro_factors,
            spot=spot,
            bottom_up_return_pct=0.0,
            horizon_days=horizon_days,
            primary_factor=primary_factor,
            primary_shock_pct=8.0,
            cascade=True,
            india_vix=macro_factors.get("india_vix"),
        )
        baseline = float(result.get("baseline_return_pct") or 0.0)
        scenario = float(result.get("expected_return_pct") or 0.0)
        delta = scenario - baseline
        return {
            "return_pct": round(delta, 4),
            "nifty_points": round(spot * delta / 100.0, 2),
            "factor_contributions": [
                {
                    "factor": primary_factor,
                    "return_pct": round(delta, 4),
                    "nifty_points": round(spot * delta / 100.0, 2),
                }
            ],
            "model": "ridge_shock_v1",
        }
    except Exception:
        return {"return_pct": 0.0, "nifty_points": 0.0, "model": "ridge_shock_v1"}


def _build_timeline(spot: float, predicted_return_pct: float, horizon_days: int) -> list[dict[str, Any]]:
    if spot <= 0 or horizon_days < 1:
        return []
    target = spot * (1.0 + predicted_return_pct / 100.0)
    return [
        {"day": 0, "label": "News", "nifty_level": round(spot, 2)},
        {
            "day": horizon_days,
            "label": f"Maturity (+{horizon_days} sessions)",
            "nifty_level": round(target, 2),
        },
    ]


def _debate_summary(ticker: str) -> dict[str, Any] | None:
    try:
        from trade_integrations.context.hub import load_agent_debate_json

        raw = load_agent_debate_json(ticker)
        struct = extract_structured_debate(raw)
        if not struct:
            return None
        return {
            "view": struct.get("view"),
            "confidence": struct.get("direction_confidence"),
            "as_of": (raw or {}).get("as_of"),
            "excerpt": (str((raw or {}).get("final_trade_decision") or "")[:400]),
        }
    except Exception:
        return None


def needs_reverify(cached: dict[str, Any], incoming: dict[str, Any], *, publish_day: str) -> bool:
    if not cached:
        return True
    if sources_changed(cached, incoming):
        return True
    data_as_of = str(cached.get("verification_data_as_of") or "")[:10]
    if not data_as_of or data_as_of < publish_day[:10]:
        return True
    if cached.get("verification_status") == "pending":
        return True
    return False


def _hub_record_from_processing(
    *,
    row: dict[str, Any],
    enriched,
    verification,
    tagged: list[dict[str, Any]],
    predicted: dict[str, Any],
    maturity: str | None,
    horizon_days: int,
    ticker: str,
    publish_day: str,
) -> dict[str, Any]:
    story_id = story_key_from_row(row)
    return {
        "canonical_story_id": story_id,
        "ticker": ticker,
        "title": enriched.title,
        "content_summary": enriched.content_summary,
        "structured_summary": {
            "facts": enriched.structured_summary.facts,
            "entities": enriched.structured_summary.entities,
            "implied_factors": enriched.structured_summary.implied_factors,
        },
        "sources": row.get("sources") or [],
        "published_at": enriched.published_at or f"{publish_day}T09:00:00+00:00",
        "verification_status": verification.status,
        "verification": verification.to_dict(),
        "verification_data_as_of": publish_day[:10],
        "predicted_impact": predicted,
        "tagged_factors": tagged,
        "maturity_date": maturity,
        "horizon_trading_days": horizon_days,
    }


def process_and_upsert_headline(
    row: dict[str, Any],
    *,
    spot: float,
    macro_factors: dict[str, float],
    horizon_days: int,
    trading_dates: list[str],
    ticker: str = "NIFTY",
    force_reverify: bool = False,
) -> dict[str, Any] | None:
    story_id = story_key_from_row(row)
    if not story_id:
        return None

    publish_day = (str(row.get("published_at") or "")[:10]) or datetime.now(timezone.utc).date().isoformat()
    cached = get_verified_record(story_id)

    if cached and not force_reverify and not needs_reverify(cached, row, publish_day=publish_day):
        return cached

    enriched = enrich_headline(
        headline_id=story_id,
        title=str(row.get("title") or ""),
        summary=str(row.get("summary") or ""),
        url=str(row.get("url") or ""),
        source=str(row.get("source") or ""),
        published_at=str(row.get("published_at") or ""),
    )
    verification = verify_enriched_news(enriched, publish_day=publish_day)

    tagged = _tag_factors(
        enriched.title,
        enriched.content_summary,
        enriched.structured_summary.implied_factors,
    )
    primary = tagged[0]["factor"] if tagged else "index_sentiment"
    predicted = _predict_impact(
        spot=spot,
        macro_factors=macro_factors,
        primary_factor=primary,
        horizon_days=horizon_days,
    )
    maturity = resolve_maturity_trading_date(publish_day, horizon_days, trading_dates)

    record = _hub_record_from_processing(
        row=row,
        enriched=enriched,
        verification=verification,
        tagged=tagged,
        predicted=predicted,
        maturity=maturity,
        horizon_days=horizon_days,
        ticker=ticker,
        publish_day=publish_day,
    )
    upsert_verified_record(record)

    if is_approved_status(verification.status):
        append_impact_ledger_row(
            {
                "canonical_story_id": story_id,
                "published_at": record["published_at"],
                "maturity_date": maturity,
                "predicted_return_pct": predicted.get("return_pct"),
                "predicted_nifty_points": predicted.get("nifty_points"),
                "verification_status": verification.status,
                "as_of": datetime.now(timezone.utc).isoformat(),
            }
        )

    item = get_verified_record(story_id) or record
    if is_approved_status(verification.status):
        item["timeline"] = _build_timeline(spot, float(predicted.get("return_pct") or 0.0), horizon_days)
        item["status"] = "live"
        return item
    return None


def ingest_headlines_for_day(
    *,
    ticker: str = "NIFTY",
    horizon_days: int = 14,
    spot: float | None = None,
    macro_factors: dict[str, float] | None = None,
    day: str | None = None,
    headline_limit: int = 12,
    force_reverify: bool = False,
) -> dict[str, int]:
    """Ingest raw headlines, verify cache misses only, upsert hub. Returns counters."""
    horizon = resolve_horizon(horizon_days)
    frame = load_aligned_factor_history(days=120)
    trading_dates = frame["date"].astype(str).str[:10].tolist() if not frame.empty else []
    today = (day or datetime.now(timezone.utc).date().isoformat())[:10]

    if macro_factors is None:
        feature_cols = [c for c in frame.columns if c not in {"date", "close"}]
        macro_factors = factor_snapshot_at(today, frame, feature_cols) if not frame.empty else {}

    if spot is None:
        if not frame.empty:
            matches = frame[frame["date"].astype(str).str[:10] == today]
            spot = float(matches.iloc[-1].get("close") or 0) if not matches.empty else float(frame.iloc[-1].get("close") or 0)
        else:
            spot = 0.0

    rows = merge_raw_headlines(collect_headlines_for_day(today, ticker=ticker, limit=headline_limit))
    stats = {"ingested": len(rows), "cache_hits": 0, "verified": 0, "rejected": 0, "approved_ui": 0}

    macro_clean = {k: float(v) for k, v in (macro_factors or {}).items() if v is not None}
    for row in rows:
        story_id = story_key_from_row(row)
        cached = get_verified_record(story_id)
        publish_day = (str(row.get("published_at") or "")[:10]) or today
        if cached and not force_reverify and not needs_reverify(cached, row, publish_day=publish_day):
            stats["cache_hits"] += 1
            continue

        stats["verified"] += 1
        item = process_and_upsert_headline(
            row,
            spot=float(spot or 0),
            macro_factors=macro_clean,
            horizon_days=horizon.days,
            trading_dates=trading_dates,
            ticker=ticker,
            force_reverify=force_reverify,
        )
        if item:
            stats["approved_ui"] += 1
        else:
            rec = get_verified_record(story_id)
            if rec and rec.get("verification_status") == "rejected":
                stats["rejected"] += 1

    return stats


def build_news_impact_snapshot(
    *,
    ticker: str = "NIFTY",
    horizon_days: int = 14,
    spot: float | None = None,
    macro_factors: dict[str, float] | None = None,
    day: str | None = None,
    headline_limit: int = 12,
    refresh_ingest: bool = True,
    force_reverify: bool = False,
    include_rejected: bool = False,
) -> dict[str, Any]:
    """Build snapshot: optionally ingest new headlines, then read from hub SSOT."""
    if refresh_ingest:
        ingest_headlines_for_day(
            ticker=ticker,
            horizon_days=horizon_days,
            spot=spot,
            macro_factors=macro_factors,
            day=day,
            headline_limit=headline_limit,
            force_reverify=force_reverify,
        )

    report = build_snapshot_from_hub(
        ticker=ticker,
        horizon_days=horizon_days,
        spot=spot,
        include_rejected=include_rejected,
        limit=headline_limit,
    )
    report["debate_summary"] = _debate_summary(ticker)
    return report


def save_news_impact_snapshot(report: dict[str, Any], *, ticker: str = "NIFTY") -> Path:
    path = _impact_snapshot_path(ticker)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return path


def load_news_impact_snapshot(ticker: str = "NIFTY") -> dict[str, Any] | None:
    path = _impact_snapshot_path(ticker)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def list_approved_for_date(day: str, *, ticker: str = "NIFTY", limit: int = 12) -> list[dict[str, Any]]:
    records = list_verified_records(
        status=["approved", "partial"],
        limit=max(limit * 5, 50),
        ticker=ticker,
    )
    target = day[:10]
    return [
        r for r in records if str(r.get("published_at") or "")[:10] == target
    ][:limit]


def reconcile_matured_impacts(*, as_of: str | None = None, ticker: str = "NIFTY") -> dict[str, Any]:
    """Fill actual_impact for stories past maturity_date using Nifty close history."""
    from trade_integrations.hub_storage.verified_news_store import list_pending_maturity

    today = (as_of or datetime.now(timezone.utc).date().isoformat())[:10]
    pending = list_pending_maturity(today)
    frame = load_aligned_factor_history(days=400)
    if frame.empty or "close" not in frame.columns:
        return {"status": "error", "message": "no history", "reconciled": 0}

    dates = frame["date"].astype(str).str[:10].tolist()
    close_by_date = {
        d: float(row["close"])
        for d, row in zip(dates, frame.to_dict(orient="records"))
        if row.get("close") is not None
    }

    reconciled = 0
    for record in pending:
        if str(record.get("ticker") or "NIFTY").upper() != ticker.upper():
            continue
        story_id = str(record.get("canonical_story_id") or "")
        pub = str(record.get("published_at") or "")[:10]
        maturity = str(record.get("maturity_date") or "")[:10]
        if not story_id or pub not in close_by_date or maturity not in close_by_date:
            continue
        spot0 = close_by_date[pub]
        spot1 = close_by_date[maturity]
        if spot0 <= 0:
            continue
        ret_pct = (spot1 - spot0) / spot0 * 100.0
        actual = {
            "return_pct": round(ret_pct, 4),
            "nifty_points": round(spot1 - spot0, 2),
            "spot_at_publish": round(spot0, 2),
            "spot_at_maturity": round(spot1, 2),
            "reconciled_at": datetime.now(timezone.utc).isoformat(),
        }
        record["actual_impact"] = actual
        upsert_verified_record(record)
        append_impact_ledger_row(
            {
                "canonical_story_id": story_id,
                "published_at": record.get("published_at"),
                "maturity_date": maturity,
                "predicted_return_pct": (record.get("predicted_impact") or {}).get("return_pct"),
                "predicted_nifty_points": (record.get("predicted_impact") or {}).get("nifty_points"),
                "actual_return_pct": actual["return_pct"],
                "actual_nifty_points": actual["nifty_points"],
                "reconciled_at": actual["reconciled_at"],
            }
        )
        reconciled += 1

    return {"status": "ok", "reconciled": reconciled, "as_of": today}
