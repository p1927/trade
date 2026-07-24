"""Pipeline step 03 — normalize published_at to IST."""

from __future__ import annotations

import re
import time
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from trade_integrations.dataflows.index_research.hub_news_pipeline.pipeline_context import (
    RefPipelineContext,
    StepResult,
)

STEP_ID = "step_03_datetime_normalize"
_IST = ZoneInfo("Asia/Kolkata")
_MARKET_OPEN = (9, 15)
_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def publish_tz_name() -> str:
    import os

    return os.getenv("HUB_NEWS_PUBLISH_TZ", "Asia/Kolkata").strip() or "Asia/Kolkata"


def _zone() -> ZoneInfo:
    try:
        return ZoneInfo(publish_tz_name())
    except Exception:
        return _IST


def _parse_datetime(raw: str) -> datetime | None:
    text = (raw or "").strip()
    if not text:
        return None
    if _DATE_ONLY.match(text[:10]):
        day = date.fromisoformat(text[:10])
        tz = _zone()
        return datetime(day.year, day.month, day.day, _MARKET_OPEN[0], _MARKET_OPEN[1], tzinfo=tz)
    normalized = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_zone())
    return dt.astimezone(_zone())


def _day_from_dt(dt: datetime) -> str:
    return dt.astimezone(_zone()).date().isoformat()


def resolve_published_at(
    *,
    ref_published_at: str,
    meta_published_at: str = "",
) -> tuple[str, str, bool, str]:
    """Return (published_at_iso, publish_day, date_conflict, timezone_source)."""
    rss_dt = _parse_datetime(ref_published_at)
    meta_dt = _parse_datetime(meta_published_at) if meta_published_at else None

    if meta_dt is not None:
        chosen = meta_dt
        source = "article_meta"
        conflict = False
        if rss_dt is not None and abs((rss_dt.date() - meta_dt.date()).days) > 1:
            conflict = True
            source = "article_meta"
    elif rss_dt is not None:
        chosen = rss_dt
        source = "rss"
        conflict = False
    else:
        now = datetime.now(_zone())
        chosen = now
        source = "inferred_now"
        conflict = False

    iso = chosen.isoformat()
    return iso, _day_from_dt(chosen), conflict, source


def extract_published_meta_from_html(html_text: str) -> str:
    """Best-effort article:published_time / JSON-LD datePublished."""
    if not html_text:
        return ""
    patterns = [
        r'<meta[^>]+property=["\']article:published_time["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']article:published_time',
        r'"datePublished"\s*:\s*"([^"]+)"',
    ]
    for pattern in patterns:
        match = re.search(pattern, html_text, re.I)
        if match:
            return match.group(1).strip()
    return ""


def run_step_03_datetime_normalize(
    ctx: RefPipelineContext,
    *,
    resolve_fn: Any | None = None,
    **_: Any,
) -> tuple[RefPipelineContext, StepResult]:
    started = time.perf_counter()
    if not ctx.should_continue:
        result = StepResult(
            step_id=STEP_ID,
            status="skipped",
            detail={"reason": ctx.discard_reason or "pipeline_stopped"},
        )
        ctx.record_step(result)
        return ctx, result

    meta = str(ctx.ref.get("article_meta_published_at") or "").strip()
    if not meta:
        meta = str(ctx.ref.get("_raw_html_meta_published") or "").strip()

    resolve = resolve_fn or resolve_published_at
    published_at, publish_day, conflict, tz_source = resolve(
        ref_published_at=str(ctx.ref.get("published_at") or ""),
        meta_published_at=meta,
    )

    ctx.published_at = published_at
    ctx.publish_day = publish_day
    ctx.date_conflict = conflict
    ctx.timezone_source = tz_source
    ctx.ref["published_at"] = published_at
    ctx.ref["publish_day"] = publish_day

    duration_ms = (time.perf_counter() - started) * 1000
    result = StepResult(
        step_id=STEP_ID,
        status="ok",
        duration_ms=duration_ms,
        detail={
            "published_at": published_at,
            "publish_day": publish_day,
            "date_conflict": conflict,
            "timezone_source": tz_source,
        },
    )
    ctx.record_step(result)
    return ctx, result
