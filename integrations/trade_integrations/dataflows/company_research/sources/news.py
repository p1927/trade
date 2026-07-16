"""Company and peer news via the trade-stack news aggregator."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from ..config import get_research_config
from ..market import NormalizedTicker
from ..models import StageResult
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
        peer_yf = peer_sym if "." in peer_sym else f"{peer_sym}.NS"
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

    return StageResult(
        stage="news",
        status=status if blocks else "skipped",
        vendor="trade_integrations.news_aggregator",
        fetched_at=_stage_now(),
        data={
            "blocks": blocks,
            "markdown": combined_md,
            "headline_count": sum(len(b.get("headlines") or []) for b in blocks),
            "source_attempts": [a.to_dict() for a in attempts],
        },
    )
