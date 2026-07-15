"""Headline sentiment — SentimentPulse HTTP when configured, else keyword heuristic."""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

from ..models import StageResult

logger = logging.getLogger(__name__)

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


def _fetch_sentiment_pulse(headlines: list[str]) -> dict[str, Any] | None:
    url = os.getenv("SENTIMENT_PULSE_URL", "").strip().rstrip("/")
    if not url:
        return None
    try:
        import requests

        scores = []
        for headline in headlines[:15]:
            response = requests.post(
                f"{url}/classify",
                json={"text": headline},
                timeout=15,
            )
            if response.ok:
                scores.append(response.json())
        if not scores:
            return None
        return {"scores": scores, "source": "sentiment_pulse"}
    except Exception as exc:
        logger.info("SentimentPulse HTTP failed: %s", exc)
        return None


def fetch_sentiment(
    *,
    headlines: list[str],
    text: str | None = None,
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

    remote = _fetch_sentiment_pulse([str(h) for h in items])
    if remote:
        scores = remote.get("scores") or []
        vendor = "sentiment_pulse"
    else:
        scores = [_classify_text(str(h)) for h in items[:15]]
        vendor = "keyword_heuristic"

    labels = [s.get("label", "neutral") for s in scores if isinstance(s, dict)]
    total = max(len(labels), 1)
    summary = {
        "positive_pct": round(100 * sum(1 for l in labels if l == "positive") / total, 1),
        "negative_pct": round(100 * sum(1 for l in labels if l == "negative") / total, 1),
        "neutral_pct": round(100 * sum(1 for l in labels if l == "neutral") / total, 1),
        "count": len(labels),
    }

    return StageResult(
        stage="sentiment",
        status="ok" if labels else "partial",
        vendor=vendor,
        fetched_at=_stage_now(),
        data={"scores": scores, "summary": summary},
    )
