"""Headline sentiment — SentimentPulse HTTP or in-process FinBERT, else keyword heuristic."""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

from ..models import StageResult

logger = logging.getLogger(__name__)

_FINBERT_MODEL = os.getenv(
    "SENTIMENT_PULSE_MODEL",
    "EomaxlSam/finbert-finetuned-sentimentpulse",
).strip()

_POSITIVE = re.compile(
    r"\b(beat|beats|surge|growth|profit|gain|upgrade|bullish|record high|outperform)\b",
    re.I,
)
_NEGATIVE = re.compile(
    r"\b(miss|misses|fall|drop|loss|downgrade|bearish|fraud|probe|default|cut guidance)\b",
    re.I,
)


def _stage_now() -> datetime:
    return datetime.now(timezone.utc)


def _sentiment_mode() -> str:
    return os.getenv("SENTIMENT_PULSE_MODE", "").strip().lower()


def _classify_text(text: str) -> dict[str, Any]:
    text = text.strip()
    if _POSITIVE.search(text) and not _NEGATIVE.search(text):
        label = "positive"
    elif _NEGATIVE.search(text) and not _POSITIVE.search(text):
        label = "negative"
    elif _POSITIVE.search(text) and _NEGATIVE.search(text):
        label = "neutral"
    else:
        label = "neutral"
    return {"text": text[:300], "label": label, "score": 0.5, "source": "keyword_heuristic"}


@lru_cache(maxsize=1)
def _finbert_pipeline():
    try:
        from transformers import pipeline
    except ImportError as exc:
        raise RuntimeError(
            "transformers not installed for in-process SentimentPulse "
            "(pip install transformers torch)"
        ) from exc
    return pipeline("text-classification", model=_FINBERT_MODEL)


def _classify_inprocess(headlines: list[str]) -> dict[str, Any] | None:
    if _sentiment_mode() != "inprocess":
        return None
    try:
        pipe = _finbert_pipeline()
    except Exception as exc:
        logger.info("SentimentPulse in-process model load failed: %s", exc)
        return None

    scores: list[dict[str, Any]] = []
    for headline in headlines[:15]:
        text = str(headline).strip()
        if not text:
            continue
        try:
            result = pipe(text[:512])[0]
            scores.append(
                {
                    "text": text[:300],
                    "label": result.get("label", "neutral"),
                    "score": round(float(result.get("score", 0.5)), 4),
                    "source": "sentiment_pulse:inprocess",
                }
            )
        except Exception as exc:
            logger.info("SentimentPulse in-process classify failed: %s", exc)
    if not scores:
        return None
    return {"scores": scores, "source": "sentiment_pulse:inprocess", "model": _FINBERT_MODEL}


def _fetch_sentiment_pulse_http(headlines: list[str]) -> dict[str, Any] | None:
    url = os.getenv("SENTIMENT_PULSE_URL", "").strip().rstrip("/")
    if not url:
        return None
    try:
        from trade_integrations.http import post

        scores = []
        for headline in headlines[:15]:
            response = post(
                f"{url}/classify",
                json={"text": headline},
                timeout=15,
            )
            if response.ok:
                body = response.json()
                if isinstance(body, dict):
                    body.setdefault("source", "sentiment_pulse:http")
                scores.append(body)
        if not scores:
            return None
        return {"scores": scores, "source": "sentiment_pulse:http"}
    except Exception as exc:
        logger.info("SentimentPulse HTTP failed: %s", exc)
        return None


def fetch_sentiment(
    *,
    headlines: list[str],
    text: str | None = None,
    capture_entity: str | None = None,
) -> StageResult:
    items = list(headlines)
    if text:
        items.insert(0, text)
    items = [h for h in items if h and str(h).strip()]

    if not items:
        return StageResult(
            stage="sentiment",
            status="skipped",
            vendor="none",
            fetched_at=_stage_now(),
            data={"reason": "no_headlines"},
            errors=["no headlines to score"],
        )

    text_items = [str(h) for h in items]
    remote = _fetch_sentiment_pulse_http(text_items)
    if not remote and _sentiment_mode() == "inprocess":
        remote = _classify_inprocess(text_items)

    if remote:
        scores = remote.get("scores") or []
        vendor = remote.get("source", "sentiment_pulse")
    else:
        scores = [_classify_text(str(h)) for h in items[:15]]
        vendor = "keyword_heuristic"
        if _sentiment_mode() == "inprocess" and scores:
            scores[0] = {
                **scores[0],
                "note": (
                    "SENTIMENT_PULSE_MODE=inprocess but FinBERT unavailable; "
                    "pip install transformers torch"
                ),
            }

    labels = [s.get("label", "neutral") for s in scores if isinstance(s, dict)]
    total = max(len(labels), 1)
    summary = {
        "positive_pct": round(100 * sum(1 for l in labels if l == "positive") / total, 1),
        "negative_pct": round(100 * sum(1 for l in labels if l == "negative") / total, 1),
        "neutral_pct": round(100 * sum(1 for l in labels if l == "neutral") / total, 1),
        "count": len(labels),
    }

    if capture_entity and text_items:
        try:
            from trade_integrations.hub_capture.channel import record_news_headlines

            news_rows = [{"title": str(h)} for h in text_items[:15]]
            record_news_headlines(capture_entity, news_rows, source=str(vendor))
        except Exception:
            logger.debug("hub news channel write-through skipped", exc_info=True)

    return StageResult(
        stage="sentiment",
        status="ok" if labels else "partial",
        vendor=vendor,
        fetched_at=_stage_now(),
        data={"scores": scores, "summary": summary},
    )
