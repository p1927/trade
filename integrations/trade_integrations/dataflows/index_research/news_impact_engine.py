"""News → Nifty impact pipeline (enriched + verified headlines only)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trade_integrations.context.hub import get_hub_dir
from trade_integrations.dataflows.index_research.horizon import resolve_horizon
from trade_integrations.dataflows.index_research.horizon_dates import resolve_maturity_trading_date
from trade_integrations.dataflows.index_research.news_collect import collect_headlines_for_day
from trade_integrations.dataflows.index_research.news_enrichment import enrich_headline
from trade_integrations.dataflows.index_research.news_verification import (
    append_verified_ledger,
    is_approved_status,
    verify_enriched_news,
)
from trade_integrations.dataflows.index_research.playground_context import _headline_factor_hints
from trade_integrations.dataflows.index_research.prediction_miss_analysis import factor_snapshot_at
from trade_integrations.dataflows.index_research.simulate import simulate_index_prediction
from trade_integrations.dataflows.index_research.sources.history_loader import load_aligned_factor_history
from trade_integrations.research.debate_synthesis import extract_structured_debate

_IMPACT_LEDGER = Path("_data") / "news_impact" / "ledger.parquet"


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


def process_headline_row(
    row: dict[str, Any],
    *,
    spot: float,
    macro_factors: dict[str, float],
    horizon_days: int,
    trading_dates: list[str],
) -> dict[str, Any] | None:
    enriched = enrich_headline(
        headline_id=str(row.get("id") or ""),
        title=str(row.get("title") or ""),
        summary=str(row.get("summary") or ""),
        url=str(row.get("url") or ""),
        source=str(row.get("source") or ""),
        published_at=str(row.get("published_at") or ""),
    )
    publish_day = (enriched.published_at or "")[:10] or datetime.now(timezone.utc).date().isoformat()
    verification = verify_enriched_news(enriched, publish_day=publish_day)
    if not is_approved_status(verification.status):
        return None

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

    item = {
        "id": enriched.id,
        "published_at": enriched.published_at or f"{publish_day}T09:00:00+00:00",
        "title": enriched.title,
        "raw_headline": enriched.raw_headline,
        "url": enriched.url,
        "source": enriched.source,
        "content_summary": enriched.content_summary,
        "structured_summary": {
            "facts": enriched.structured_summary.facts,
            "entities": enriched.structured_summary.entities,
            "implied_factors": enriched.structured_summary.implied_factors,
        },
        "verification": verification.to_dict(),
        "status": "live",
        "tagged_factors": tagged,
        "horizon_trading_days": horizon_days,
        "maturity_date": maturity,
        "predicted": predicted,
        "actual": None,
        "timeline": _build_timeline(spot, float(predicted.get("return_pct") or 0.0), horizon_days),
        "confidence_note": "Model-attributed estimate; verified against factor data where possible.",
    }
    return item


def build_news_impact_snapshot(
    *,
    ticker: str = "NIFTY",
    horizon_days: int = 14,
    spot: float | None = None,
    macro_factors: dict[str, float] | None = None,
    day: str | None = None,
    headline_limit: int = 12,
) -> dict[str, Any]:
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
            if not matches.empty:
                spot = float(matches.iloc[-1].get("close") or 0)
            else:
                spot = float(frame.iloc[-1].get("close") or 0)
        else:
            spot = 0.0

    rows = collect_headlines_for_day(today, ticker=ticker, limit=headline_limit)
    items: list[dict[str, Any]] = []
    ledger_rows: list[dict[str, Any]] = []

    for row in rows:
        item = process_headline_row(
            row,
            spot=float(spot or 0),
            macro_factors={k: float(v) for k, v in (macro_factors or {}).items() if v is not None},
            horizon_days=horizon.days,
            trading_dates=trading_dates,
        )
        if item:
            items.append(item)
            ledger_rows.append(
                {
                    "id": item["id"],
                    "published_at": item["published_at"],
                    "title": item["title"],
                    "verification_status": item["verification"]["status"],
                    "predicted_return_pct": (item.get("predicted") or {}).get("return_pct"),
                    "as_of": datetime.now(timezone.utc).isoformat(),
                }
            )

    append_verified_ledger(ledger_rows)

    live = sum(1 for i in items if i.get("status") == "live")
    return {
        "status": "ok",
        "as_of": datetime.now(timezone.utc).isoformat(),
        "ticker": ticker,
        "horizon_days": horizon.days,
        "spot": spot,
        "debate_summary": _debate_summary(ticker),
        "items": items,
        "summary": {
            "live_count": live,
            "pending_count": 0,
            "reconciled_count": 0,
            "approved_count": sum(
                1 for i in items if i.get("verification", {}).get("status") == "approved"
            ),
            "partial_count": sum(
                1 for i in items if i.get("verification", {}).get("status") == "partial"
            ),
            "rejected_skipped": max(0, len(rows) - len(items)),
        },
    }


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
