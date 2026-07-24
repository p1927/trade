"""Horizon-dated SearXNG query packs for external prediction discovery."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from trade_integrations.dataflows.index_research.external_predictions.domain_utils import (
    primary_domain,
)
from trade_integrations.dataflows.index_research.external_predictions.models import (
    ExternalPredictionSource,
)
from trade_integrations.dataflows.index_research.horizon_dates import (
    resolve_maturity_trading_date,
    trading_dates_from_frame,
)

logger = logging.getLogger(__name__)

_DEFAULT_TEMPLATES: tuple[str, ...] = (
    '"{source_name}" Nifty 50 target forecast {today} {horizon_end}',
    '"{source_name}" Nifty 50 outlook {month_year} India',
    'Nifty 50 index target {horizon_end} "{source_name}"',
    '"{source_name}" Nifty 50 target {month_year}',
)


def load_nifty_trading_dates() -> list[str]:
    try:
        from trade_integrations.dataflows.index_research.history_ingest import load_history_dataset

        frame = load_history_dataset("nifty_ohlcv_daily")
        dates = trading_dates_from_frame(frame)
        if not dates:
            logger.warning("nifty_ohlcv_daily loaded but no trading dates found")
        return dates
    except Exception as exc:
        logger.warning("failed to load NIFTY trading calendar for horizon queries: %s", exc)
        return []


def _calendar_horizon_end(today_dt: date, horizon_trading_days: int) -> str:
    """Approximate maturity when the trading calendar is unavailable (~5/7 ratio)."""
    calendar_days = max(int(horizon_trading_days), int(round(horizon_trading_days * 7 / 5)))
    return (today_dt + timedelta(days=calendar_days)).isoformat()


def build_horizon_context(
    *,
    horizon_days: int,
    as_of_date: str | None = None,
    trading_dates: list[str] | None = None,
) -> dict[str, str]:
    from trade_integrations.dataflows.company_research.market import india_trading_date_iso

    today = str(as_of_date or india_trading_date_iso())[:10]
    dates = trading_dates if trading_dates is not None else load_nifty_trading_dates()
    today_dt = date.fromisoformat(today)

    horizon_end = resolve_maturity_trading_date(today, horizon_days, dates)
    horizon_end_approx = False
    if not horizon_end:
        horizon_end = _calendar_horizon_end(today_dt, horizon_days)
        horizon_end_approx = True
        if not dates:
            logger.warning(
                "NIFTY trading calendar empty — using approximate horizon_end %s for %sd",
                horizon_end,
                horizon_days,
            )

    weekday = today_dt.weekday()
    week_start = (today_dt - timedelta(days=weekday)).isoformat()
    week_end = (today_dt + timedelta(days=max(0, 4 - weekday))).isoformat()

    return {
        "today": today,
        "horizon_end": horizon_end,
        "horizon_end_approx": "1" if horizon_end_approx else "0",
        "week_start": week_start,
        "week_end": week_end,
        "month": today_dt.strftime("%B"),
        "month_year": today_dt.strftime("%B %Y"),
        "year": str(today_dt.year),
        "horizon": str(int(horizon_days)),
        "today_minus_14d": (today_dt - timedelta(days=14)).isoformat(),
    }


def expand_query_template(template: str, *, context: dict[str, str], source: ExternalPredictionSource) -> str:
    ctx: dict[str, Any] = {
        **context,
        "source_name": source.display_name,
        "primary_domain": primary_domain(source),
    }
    out = str(template or "")
    for key, value in ctx.items():
        out = out.replace("{" + key + "}", str(value))
    return " ".join(out.split())


def build_domain_tier_queries(
    source: ExternalPredictionSource,
    *,
    context: dict[str, str],
) -> list[str]:
    """Open attribution queries (company + dates); domain filter applied post-search."""
    name = source.display_name
    queries: list[str] = [
        expand_query_template(
            f'"{name}" Nifty 50 outlook {{month_year}} India brokerage',
            context=context,
            source=source,
        ),
        expand_query_template(
            f'Nifty 50 index target {{horizon_end}} "{name}"',
            context=context,
            source=source,
        ),
    ]
    return list(dict.fromkeys(q for q in queries if q.strip()))


def build_fallback_queries(
    source: ExternalPredictionSource,
    *,
    horizon_days: int,
    as_of_date: str | None = None,
    trading_dates: list[str] | None = None,
) -> list[str]:
    """Broader queries used only when primary discovery returns no ranked hits."""
    context = build_horizon_context(
        horizon_days=horizon_days,
        as_of_date=as_of_date,
        trading_dates=trading_dates,
    )
    name = source.display_name
    domain = primary_domain(source)
    month_year = context.get("month_year") or ""
    if source.kind == "media" and domain:
        queries = [
            expand_query_template(
                "Nifty 50 target outlook site:{primary_domain}",
                context=context,
                source=source,
            ),
            expand_query_template(
                "Nifty 50 forecast {month_year} site:{primary_domain}",
                context=context,
                source=source,
            ),
        ]
    elif source.kind in {"broker", "global_bank"}:
        queries = [
            expand_query_template(
                f'"{name}" Nifty 50 view India markets {{month_year}}',
                context=context,
                source=source,
            ),
            expand_query_template(
                f'"{name}" Nifty 50 target India {month_year}',
                context=context,
                source=source,
            ),
        ]
    else:
        queries = [
            expand_query_template(
                f'"{name}" Nifty 50 outlook India {{month_year}}',
                context=context,
                source=source,
            ),
        ]
    return list(dict.fromkeys(q for q in queries if q.strip()))


def build_horizon_queries(
    source: ExternalPredictionSource,
    *,
    horizon_days: int,
    as_of_date: str | None = None,
    trading_dates: list[str] | None = None,
) -> list[str]:
    context = build_horizon_context(
        horizon_days=horizon_days,
        as_of_date=as_of_date,
        trading_dates=trading_dates,
    )
    templates = list(source.search_queries or []) or list(_DEFAULT_TEMPLATES)
    expanded = [
        expand_query_template(template, context=context, source=source)
        for template in templates
    ]
    tiered = build_domain_tier_queries(source, context=context)
    combined = list(dict.fromkeys([q for q in expanded + tiered if q.strip()]))
    return combined[:5]
