"""Company and peer news via the trade-stack news aggregator."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from ..config import get_research_config
from ..market import NormalizedTicker, normalize_ticker
from ..models import StageResult
from ..fetch_policy import is_nifty50_batch
from .resilience import SourceAttempt, classify_error, remediation_for, stage_status_from_attempts

logger = logging.getLogger(__name__)

_HEADLINE_RE = re.compile(r"^[-*]\s+\**(.+?)\**(?:\s+\(|$)", re.MULTILINE)


def _stage_now() -> datetime:
    return datetime.now(timezone.utc)


def _extract_headlines(markdown: str, *, limit: int = 15) -> list[dict[str, str]]:
    headlines: list[dict[str, str]] = []
    for line in markdown.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("###"):
            title = line.lstrip("#").strip()
            if title and "news, from" not in title.lower():
                headlines.append({"title": title[:500]})
        elif line.startswith(("-", "*")):
            text = line.lstrip("-* ").strip()
            if text and not text.startswith("#"):
                headlines.append({"title": text[:500]})
        if len(headlines) >= limit:
            break
    return headlines


def _fetch_ticker_news(
    ticker: str,
    *,
    lookback_days: int,
    label: str,
) -> dict[str, Any] | None:
    from trade_integrations.dataflows.news_aggregator import get_news_aggregated

    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=lookback_days)
    markdown = get_news_aggregated(
        ticker,
        start.strftime("%Y-%m-%d"),
        end.strftime("%Y-%m-%d"),
    )
    if not markdown or "No news" in markdown[:200]:
        return None
    return {
        "ticker": ticker,
        "label": label,
        "markdown": markdown,
        "headlines": _extract_headlines(markdown),
        "source": "news_aggregator",
    }


def fetch_news(
    normalized: NormalizedTicker,
    *,
    peers: list[dict[str, Any]] | None = None,
    lookback_days: int | None = None,
) -> StageResult:
    """Fetch company news and optional top-peer headlines."""
    config = get_research_config()
    days = lookback_days if lookback_days is not None else config.news_lookback_days
    attempts: list[SourceAttempt] = []
    blocks: list[dict[str, Any]] = []

    # Nifty 50 batch: SearXNG only, company ticker — no peer fan-out, no tiered APIs.
    if is_nifty50_batch():
        try:
            from trade_integrations.dataflows.searxng_news import get_news_searxng

            end = datetime.now(timezone.utc).date()
            start = end - timedelta(days=days)
            markdown = get_news_searxng(
                normalized.base_symbol,
                start.strftime("%Y-%m-%d"),
                end.strftime("%Y-%m-%d"),
            )
            if markdown and "No news" not in markdown[:200]:
                company = {
                    "ticker": normalized.base_symbol,
                    "label": normalized.display_symbol,
                    "markdown": markdown,
                    "headlines": _extract_headlines(markdown),
                    "source": "searxng",
                }
                blocks.append(company)
                attempts.append(SourceAttempt(name="company", status="ok", data=company))
            else:
                attempts.append(
                    SourceAttempt(
                        name="company",
                        status="skipped",
                        error="no_data",
                        remediation="SearXNG returned no headlines for this symbol.",
                    )
                )
        except Exception as exc:
            attempts.append(
                SourceAttempt(
                    name="company",
                    status="error",
                    error=str(exc),
                    remediation=remediation_for(classify_error(exc)),
                )
            )

        status = stage_status_from_attempts(attempts, has_output=bool(blocks))
        combined_md = blocks[0]["markdown"] if blocks else ""
        return StageResult(
            stage="news",
            status=status if blocks else "skipped",
            vendor="searxng",
            fetched_at=_stage_now(),
            data={
                "blocks": blocks,
                "markdown": combined_md,
                "headline_count": sum(len(b.get("headlines") or []) for b in blocks),
                "batch_mode": "nifty50",
                "source_attempts": [a.to_dict() for a in attempts],
            },
        )

    try:
        company = _fetch_ticker_news(
            normalized.yfinance_symbol,
            lookback_days=days,
            label=normalized.display_symbol,
        )
        if company:
            blocks.append(company)
            attempts.append(SourceAttempt(name="company", status="ok", data=company))
        else:
            attempts.append(
                SourceAttempt(
                    name="company",
                    status="error",
                    error="no_data",
                    remediation=remediation_for("no_data"),
                )
            )
    except Exception as exc:
        attempts.append(
            SourceAttempt(
                name="company",
                status="error",
                error=str(exc),
                remediation=remediation_for(classify_error(exc)),
            )
        )

    peer_symbols = [
        p.get("symbol")
        for p in (peers or [])
        if p.get("symbol") and p.get("symbol") != normalized.base_symbol
    ][:3]

    for peer_sym in peer_symbols:
        peer_yf = normalize_ticker(str(peer_sym), market=normalized.market).yfinance_symbol
        try:
            peer_block = _fetch_ticker_news(peer_yf, lookback_days=days, label=peer_sym)
            if peer_block:
                blocks.append(peer_block)
                attempts.append(SourceAttempt(name=f"peer:{peer_sym}", status="ok", data=peer_block))
        except Exception as exc:
            attempts.append(
                SourceAttempt(
                    name=f"peer:{peer_sym}",
                    status="error",
                    error=str(exc),
                    remediation=remediation_for(classify_error(exc)),
                )
            )

    status = stage_status_from_attempts(attempts, has_output=bool(blocks))
    if blocks and status == "error":
        status = "partial"

    combined_md = "\n\n".join(
        f"### {b['label']} ({b['ticker']})\n{b['markdown']}" for b in blocks
    )

    # Aggregator path already ingests via news_hub_bridge; record stage metadata.
    try:
        from trade_integrations.dataflows.news_hub_bridge import query_verified_news

        hub_sym = normalized.base_symbol
        recent = query_verified_news(ticker=hub_sym, limit=5)
        hub_count = len(recent)
    except Exception:
        hub_count = 0

    return StageResult(
        stage="news",
        status=status if blocks else "skipped",
        vendor="trade_integrations.news_aggregator",
        fetched_at=_stage_now(),
        data={
            "blocks": blocks,
            "markdown": combined_md,
            "headline_count": sum(len(b.get("headlines") or []) for b in blocks),
            "hub_verified_recent": hub_count,
            "source_attempts": [a.to_dict() for a in attempts],
        },
    )
