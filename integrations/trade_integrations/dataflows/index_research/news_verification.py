"""Verify news claims against factor/market data before approval."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from trade_integrations.context.hub import get_hub_dir
from trade_integrations.dataflows.index_research.news_enrichment import EnrichedNewsItem
from trade_integrations.dataflows.index_research.prediction_miss_analysis import factor_snapshot_at
from trade_integrations.dataflows.index_research.sources.history_loader import load_aligned_factor_history

_VERIFIED_LEDGER = Path("_data") / "news_verified" / "ledger.parquet"

_FACTOR_CLAIM_RULES: dict[str, dict[str, Any]] = {
    "fii_net_5d": {
        "sell_words": ("outflow", "sold", "selling", "sell-off", "selloff", "exit", "pull out"),
        "buy_words": ("inflow", "bought", "buying", "accumulate", "inflow"),
    },
    "dii_net_5d": {
        "sell_words": ("dii sell", "domestic sell", "dii outflow"),
        "buy_words": ("dii buy", "domestic buy", "dii inflow", "absorb"),
    },
    "oil_brent": {
        "rise_words": ("surge", "spike", "rise", "rally", "jump", "higher crude", "oil up"),
        "fall_words": ("fall", "drop", "ease", "lower crude", "oil down"),
    },
    "india_vix": {
        "rise_words": ("vix surge", "fear", "volatility spike", "hedging"),
        "fall_words": ("vix fall", "calm", "volatility ease"),
    },
    "repo_rate": {
        "hike_words": ("rate hike", "hiked", "tighten", "raise repo"),
        "cut_words": ("rate cut", "eased", "lower repo"),
    },
}


@dataclass
class VerifiedClaim:
    claim: str
    factor: str
    verdict: str
    evidence: str = ""
    data_as_of: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class NewsVerification:
    status: str
    verified_at: str
    claims: list[VerifiedClaim] = field(default_factory=list)
    approval_note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "verified_at": self.verified_at,
            "claims": [c.to_dict() for c in self.claims],
            "approval_note": self.approval_note,
        }


def _working_text(item: EnrichedNewsItem) -> str:
    return f"{item.content_summary} {item.raw_headline}".lower()


def _factor_delta(frame: pd.DataFrame, factor: str, day: str, sessions: int = 5) -> tuple[float | None, float | None, str]:
    if frame.empty or "date" not in frame.columns or factor not in frame.columns:
        return None, None, day
    dates = frame["date"].astype(str).str[:10].tolist()
    if day[:10] not in dates:
        return None, None, day
    idx = dates.index(day[:10])
    end_idx = min(len(dates) - 1, idx + sessions)
    t0 = factor_snapshot_at(day, frame, [factor], keys=[factor]).get(factor)
    t1 = factor_snapshot_at(dates[end_idx], frame, [factor], keys=[factor]).get(factor)
    return t0, t1, dates[end_idx]


def _claim_from_factor(factor: str, text: str) -> str | None:
    rules = _FACTOR_CLAIM_RULES.get(factor)
    if not rules:
        return None
    if any(w in text for w in rules.get("sell_words", ()) + rules.get("rise_words", ()) + rules.get("hike_words", ())):
        return f"Narrative implies negative/move-up pressure on {factor}"
    if any(w in text for w in rules.get("buy_words", ()) + rules.get("fall_words", ()) + rules.get("cut_words", ())):
        return f"Narrative implies positive/move-down pressure on {factor}"
    if factor in text or factor.replace("_", " ") in text:
        return f"Narrative references {factor}"
    return None


def _verify_factor_claim(
    factor: str,
    text: str,
    frame: pd.DataFrame,
    publish_day: str,
) -> VerifiedClaim:
    rules = _FACTOR_CLAIM_RULES.get(factor, {})
    t0, t1, as_of = _factor_delta(frame, factor, publish_day)
    claim = _claim_from_factor(factor, text) or f"Check {factor} vs narrative"

    if t0 is None or t1 is None:
        return VerifiedClaim(
            claim=claim,
            factor=factor,
            verdict="unverifiable",
            evidence="No factor history for publish window",
            data_as_of=publish_day[:10],
        )

    delta = t1 - t0
    evidence = f"{factor} {t0:.4g} → {t1:.4g} (Δ {delta:+.4g}) over sessions"

    sell_hit = any(w in text for w in rules.get("sell_words", ()))
    buy_hit = any(w in text for w in rules.get("buy_words", ()))
    rise_hit = any(w in text for w in rules.get("rise_words", ()))
    fall_hit = any(w in text for w in rules.get("fall_words", ()))
    hike_hit = any(w in text for w in rules.get("hike_words", ()))
    cut_hit = any(w in text for w in rules.get("cut_words", ()))

    if factor in {"fii_net_5d", "dii_net_5d"}:
        if sell_hit and delta < 0:
            verdict = "supported"
        elif buy_hit and delta > 0:
            verdict = "supported"
        elif (sell_hit and delta > 0) or (buy_hit and delta < 0):
            verdict = "contradicted"
        else:
            verdict = "unverifiable"
    elif factor in {"oil_brent", "india_vix"}:
        if rise_hit and delta > 0:
            verdict = "supported"
        elif fall_hit and delta < 0:
            verdict = "supported"
        elif (rise_hit and delta <= 0) or (fall_hit and delta >= 0):
            verdict = "contradicted"
        else:
            verdict = "unverifiable"
    elif factor == "repo_rate":
        if hike_hit and delta > 0.01:
            verdict = "supported"
        elif cut_hit and delta < -0.01:
            verdict = "supported"
        elif (hike_hit or cut_hit) and abs(delta) < 0.01:
            verdict = "contradicted"
        else:
            verdict = "unverifiable"
    else:
        verdict = "unverifiable"

    return VerifiedClaim(
        claim=claim,
        factor=factor,
        verdict=verdict,
        evidence=evidence,
        data_as_of=as_of,
    )


def _approval_from_claims(claims: list[VerifiedClaim]) -> NewsVerification:
    now = datetime.now(timezone.utc).isoformat()
    if not claims:
        return NewsVerification(
            status="rejected",
            verified_at=now,
            claims=[],
            approval_note="No verifiable claims extracted from summary.",
        )

    supported = sum(1 for c in claims if c.verdict == "supported")
    contradicted = sum(1 for c in claims if c.verdict == "contradicted")
    unverifiable = sum(1 for c in claims if c.verdict == "unverifiable")

    if contradicted > 0:
        status = "rejected"
        note = f"Rejected: {contradicted} claim(s) contradicted by factor data."
    elif supported >= 1:
        status = "approved" if unverifiable == 0 else "partial"
        note = f"Approved: {supported}/{len(claims)} claims supported by market data."
    elif unverifiable == len(claims):
        status = "partial"
        note = "Partial: narrative not contradicted but no factor confirmation yet."
    else:
        status = "rejected"
        note = "Rejected: no supported claims."

    return NewsVerification(status=status, verified_at=now, claims=claims, approval_note=note)


def verify_enriched_news(
    item: EnrichedNewsItem,
    *,
    publish_day: str | None = None,
    history_days: int = 120,
) -> NewsVerification:
    day = (publish_day or item.published_at or "")[:10]
    if not day:
        day = datetime.now(timezone.utc).date().isoformat()

    frame = load_aligned_factor_history(days=history_days)
    text = _working_text(item)
    factors = list(item.structured_summary.implied_factors or [])
    claims: list[VerifiedClaim] = []
    for factor in factors[:5]:
        claims.append(_verify_factor_claim(factor, text, frame, day))
    return _approval_from_claims(claims)


def is_approved_status(status: str) -> bool:
    return status in {"approved", "partial"}


def verified_ledger_path() -> Path:
    return get_hub_dir() / _VERIFIED_LEDGER


def append_verified_ledger(rows: list[dict[str, Any]]) -> Path | None:
    """Legacy thin ledger — delegates full records to verified_news_store."""
    if not rows:
        return None
    from trade_integrations.hub_storage.verified_news_store import upsert_verified_record

    for row in rows:
        story_id = str(row.get("canonical_story_id") or row.get("id") or "").strip()
        if not story_id:
            continue
        upsert_verified_record(
            {
                "canonical_story_id": story_id,
                "ticker": row.get("ticker") or "NIFTY",
                "title": row.get("title") or "",
                "published_at": row.get("published_at"),
                "verification_status": row.get("verification_status") or "pending",
                "predicted_impact": {"return_pct": row.get("predicted_return_pct")},
                "verification_data_as_of": (row.get("published_at") or "")[:10],
            }
        )
    return verified_ledger_path()


def load_verified_ledger(*, limit: int = 200) -> list[dict[str, Any]]:
    path = verified_ledger_path()
    if not path.is_file():
        return []
    try:
        frame = pd.read_parquet(path).tail(limit)
        return frame.to_dict(orient="records")
    except Exception:
        return []
