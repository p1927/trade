"""Relevance gate for hub news staging refs (NIFTY / factor prediction)."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

_JUNK_PATTERNS = re.compile(
    r"\b("
    r"cricket|ipl|bollywood|celebrity|recipe|horoscope|"
    r"football|soccer|tennis grand slam|beauty pageant|"
    r"kardashian|reality tv|fashion week|wedding|gossip|"
    r"lottery|casino|gaming review|movie review|box office"
    r")\b",
    re.IGNORECASE,
)

_MARKET_SIGNALS = re.compile(
    r"\b("
    r"nifty|sensex|nse|bse|india|indian|market|stock|equity|"
    r"rbi|fii|dii|inflation|gdp|budget|rupee|crude|oil|vix|"
    r"earnings|repo|fed|tariff|geopolit|war|ceasefire|"
    r"bank nifty|midcap|smallcap|ipo|f&o|derivative"
    r")\b",
    re.IGNORECASE,
)

_INDIA_MARKET = re.compile(
    r"\b(nifty|sensex|nse|bse|india|indian market|mumbai|rupee)\b",
    re.IGNORECASE,
)


@dataclass
class RelevanceVerdict:
    relevant: bool
    confidence: float
    reason: str = ""
    factors: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    source: str = "rule"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def relevance_gate_enabled() -> bool:
    try:
        from trade_integrations.hub_storage.news_pipeline_config import load_news_pipeline_config

        cfg = load_news_pipeline_config()
        return bool(getattr(cfg, "relevance_gate_enabled", True))
    except Exception:
        pass
    return os.getenv("HUB_NEWS_RELEVANCE_GATE", "1").strip().lower() in {"1", "true", "yes", "on"}


def relevance_rule_first() -> bool:
    try:
        from trade_integrations.hub_storage.news_pipeline_config import load_news_pipeline_config

        cfg = load_news_pipeline_config()
        return bool(getattr(cfg, "relevance_rule_first", True))
    except Exception:
        pass
    return os.getenv("HUB_NEWS_RELEVANCE_RULE_FIRST", "1").strip().lower() in {"1", "true", "yes", "on"}


def relevance_min_confidence() -> float:
    try:
        from trade_integrations.hub_storage.news_pipeline_config import load_news_pipeline_config

        cfg = load_news_pipeline_config()
        return float(getattr(cfg, "relevance_min_confidence", 0.60))
    except Exception:
        pass
    try:
        return float(os.getenv("HUB_NEWS_RELEVANCE_MIN_CONFIDENCE", "0.60"))
    except ValueError:
        return 0.60


def discard_retention_days() -> int:
    try:
        from trade_integrations.hub_storage.news_pipeline_config import load_news_pipeline_config

        cfg = load_news_pipeline_config()
        return int(getattr(cfg, "discard_retention_days", 30))
    except Exception:
        pass
    try:
        return int(os.getenv("HUB_NEWS_DISCARD_RETENTION_DAYS", "30"))
    except ValueError:
        return 30


def _ref_text(ref: dict[str, Any]) -> str:
    return f"{ref.get('title') or ''} {ref.get('summary') or ''}".strip()


def rule_prefilter(ref: dict[str, Any]) -> RelevanceVerdict | None:
    """Fast pass/fail; None when ambiguous (needs LLM)."""
    text = _ref_text(ref)
    if not text:
        return RelevanceVerdict(
            relevant=False,
            confidence=0.95,
            reason="empty headline",
            source="rule",
        )

    if _JUNK_PATTERNS.search(text) and not _MARKET_SIGNALS.search(text):
        return RelevanceVerdict(
            relevant=False,
            confidence=0.9,
            reason="non-market entertainment content",
            source="rule",
        )

    tags = ref.get("tags") if isinstance(ref.get("tags"), dict) else {}
    topics = list(tags.get("topics") or [])
    factors = list(tags.get("factors") or [])

    if not topics and not factors:
        from trade_integrations.dataflows.index_research.news_tags import build_article_tags

        built = build_article_tags(
            title=str(ref.get("title") or ""),
            summary=str(ref.get("summary") or ""),
            published_at=str(ref.get("published_at") or ""),
        )
        topics = list(built.topics or [])
        factors = list(built.factors or [])
        ref.setdefault("tags", built.to_dict())

    if topics or factors:
        return RelevanceVerdict(
            relevant=True,
            confidence=0.85,
            reason="matched market topics or factors",
            factors=factors[:8],
            topics=topics[:8],
            source="rule",
        )

    if _INDIA_MARKET.search(text) or _MARKET_SIGNALS.search(text):
        return RelevanceVerdict(
            relevant=True,
            confidence=0.72,
            reason="market keyword signal",
            source="rule",
        )

    if re.search(r"\b(us|europe|china|japan|uk)\b", text, re.I) and not _INDIA_MARKET.search(text):
        return RelevanceVerdict(
            relevant=False,
            confidence=0.75,
            reason="foreign headline with no India market link",
            source="rule",
        )

    return None


def _parse_relevance_json(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if not text:
        return {}
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass
    return {}


def llm_relevance_check(ref: dict[str, Any], *, ticker: str = "NIFTY") -> RelevanceVerdict:
    """MiniMax structured relevance verdict for NIFTY prediction."""
    from trade_integrations.dataflows.index_research.factor_catalog import NIFTY_FACTOR_CATALOG
    from trade_integrations.nse_browser.minimax_agent import chat_completions_create, _model

    factor_labels = [
        str(row.get("key") or row.get("label") or "")
        for row in NIFTY_FACTOR_CATALOG[:24]
        if isinstance(row, dict)
    ]
    factor_hint = ", ".join(f for f in factor_labels if f)[:1200]
    title = str(ref.get("title") or "")[:400]
    summary = str(ref.get("summary") or "")[:800]
    prompt = (
        f"You judge whether a news headline is relevant for predicting the {ticker} index "
        "or its macro/flow/technical factors.\n"
        "Relevant: India markets, NIFTY/Sensex, FII/DII flows, RBI/rates, crude/oil, VIX, "
        "USD/INR, earnings affecting index, geopolitics with clear India market impact.\n"
        "Irrelevant: sports, entertainment, unrelated local news, foreign stories with no "
        "India market transmission.\n"
        f"Known factors (non-exhaustive): {factor_hint}\n\n"
        f"Title: {title}\nSummary: {summary}\n\n"
        "Reply JSON only: "
        '{"relevant": true|false, "confidence": 0.0-1.0, "factors": ["..."], '
        '"topics": ["..."], "reason": "one sentence"}'
    )
    try:
        response = chat_completions_create(
            model=_model(),
            messages=[{"role": "user", "content": prompt}],
            max_tokens=300,
            temperature=0.0,
        )
        raw = str(response.choices[0].message.content or "")
        payload = _parse_relevance_json(raw)
        relevant = bool(payload.get("relevant"))
        try:
            confidence = float(payload.get("confidence") or 0.5)
        except (TypeError, ValueError):
            confidence = 0.5
        return RelevanceVerdict(
            relevant=relevant,
            confidence=max(0.0, min(1.0, confidence)),
            reason=str(payload.get("reason") or "llm relevance check"),
            factors=[str(x) for x in (payload.get("factors") or []) if x][:8],
            topics=[str(x) for x in (payload.get("topics") or []) if x][:8],
            source="llm",
        )
    except Exception as exc:
        logger.warning("llm relevance check failed: %s", exc)
        return RelevanceVerdict(
            relevant=True,
            confidence=0.4,
            reason=f"llm unavailable: {exc}",
            source="fallback",
        )


def assess_ref_relevance(ref: dict[str, Any], *, ticker: str = "NIFTY") -> RelevanceVerdict:
    """Rules first when enabled; LLM on ambiguous refs."""
    if not relevance_gate_enabled():
        return RelevanceVerdict(
            relevant=True,
            confidence=1.0,
            reason="relevance gate disabled",
            source="disabled",
        )

    if relevance_rule_first():
        ruled = rule_prefilter(ref)
        if ruled is not None and (ruled.confidence >= 0.8 or ruled.confidence <= 0.2):
            return ruled

    from trade_integrations.hub_storage.news_staging_store import minimax_configured

    if minimax_configured():
        return llm_relevance_check(ref, ticker=ticker)

    ruled = rule_prefilter(ref)
    if ruled is not None:
        return ruled
    return RelevanceVerdict(
        relevant=True,
        confidence=0.5,
        reason="ambiguous without llm",
        source="fallback",
    )
