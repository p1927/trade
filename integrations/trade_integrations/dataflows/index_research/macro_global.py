"""Global macro factor collector for index research."""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from trade_integrations.dataflows.company_research.models import StageResult
from trade_integrations.dataflows.company_research.sources.macro_in import (
    _fetch_nselib_vix,
    _fetch_yfinance_vix,
)
from trade_integrations.dataflows.company_research.sources.resilience import (
    SourceAttempt,
    remediation_for,
    stage_status_from_attempts,
)

from .sources.rbi_cpi import fetch_rbi_cpi_context

logger = logging.getLogger(__name__)

_YFINANCE_FACTORS: dict[str, str] = {
    "oil_brent": "BZ=F",
    "oil_wti": "CL=F",
    "usd_inr": "INR=X",
    "gold": "GC=F",
    "sp500": "^GSPC",
}

_FRED_LATEST_RE = re.compile(r"\*\*Latest:\*\*\s*([\d.]+)")


def _stage_now() -> datetime:
    return datetime.now(timezone.utc)


def _fetch_yfinance_factor(factor: str, symbol: str) -> dict[str, Any] | None:
    import yfinance as yf

    info = yf.Ticker(symbol).info or {}
    price = info.get("regularMarketPrice") or info.get("previousClose")
    if price is None:
        return None
    return {
        "factor": factor,
        "value": float(price),
        "source": "yfinance",
        "metadata": {"symbol": symbol},
    }


def _fetch_us_10y() -> dict[str, Any] | None:
    today = datetime.now(timezone.utc).date().isoformat()

    try:
        from tradingagents.dataflows.interface import get_fred_macro_data

        excerpt = get_fred_macro_data("DGS10", today, look_back_days=30)
        if excerpt and "unavailable" not in excerpt.lower()[:120]:
            match = _FRED_LATEST_RE.search(excerpt)
            if match:
                return {
                    "factor": "us_10y",
                    "value": float(match.group(1)),
                    "source": "fred_tradingagents",
                    "metadata": {"series": "DGS10"},
                }
    except Exception as exc:
        logger.debug("tradingagents FRED us_10y failed: %s", exc)

    api_key = os.getenv("FRED_API_KEY", "").strip()
    if not api_key:
        return None

    try:
        import requests

        end = today
        start = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=30)).strftime("%Y-%m-%d")
        response = requests.get(
            "https://api.stlouisfed.org/fred/series/observations",
            params={
                "series_id": "DGS10",
                "api_key": api_key,
                "file_type": "json",
                "observation_start": start,
                "observation_end": end,
                "sort_order": "desc",
                "limit": 5,
            },
            timeout=15,
        )
        response.raise_for_status()
        observations = response.json().get("observations", [])
        for obs in observations:
            raw = obs.get("value")
            if raw not in (".", None, ""):
                return {
                    "factor": "us_10y",
                    "value": float(raw),
                    "source": "fred_direct",
                    "metadata": {"series": "DGS10", "date": obs.get("date")},
                }
    except Exception as exc:
        logger.debug("direct FRED us_10y failed: %s", exc)

    return None


def _fetch_india_vix() -> dict[str, Any] | None:
    for fetcher, source_name in (
        (_fetch_nselib_vix, "nselib"),
        (_fetch_yfinance_vix, "yfinance"),
    ):
        try:
            payload = fetcher()
        except Exception as exc:
            logger.debug("%s india_vix failed: %s", source_name, exc)
            continue
        if payload and payload.get("india_vix") is not None:
            return {
                "factor": "india_vix",
                "value": float(payload["india_vix"]),
                "source": payload.get("source", source_name),
                "metadata": {k: v for k, v in payload.items() if k not in ("india_vix", "source")},
            }
    return None


def _fii_net_column(frame) -> str | None:
    for column in frame.columns:
        label = str(column).lower()
        if "fii" in label and "net" in label:
            return column
    return None


def _dii_net_column(frame) -> str | None:
    for column in frame.columns:
        label = str(column).lower()
        if "dii" in label and "net" in label:
            return column
    return None


def _fetch_flow_net_5d(*, factor: str, net_col_finder) -> dict[str, Any] | None:
    try:
        from nselib import capital_market
    except ImportError:
        return None

    fetcher = getattr(capital_market, "fii_dii_trading_activity", None)
    if fetcher is None:
        return None

    end = datetime.now().date()
    start = end - timedelta(days=10)
    frame = fetcher(
        from_date=start.strftime("%d-%m-%Y"),
        to_date=end.strftime("%d-%m-%Y"),
    )
    if frame is None or getattr(frame, "empty", True):
        return None

    net_col = net_col_finder(frame)
    if net_col is None:
        return None

    tail = frame.tail(5)
    values = []
    for raw in tail[net_col]:
        try:
            values.append(float(raw))
        except (TypeError, ValueError):
            continue
    if not values:
        return None

    return {
        "factor": factor,
        "value": sum(values),
        "source": "nselib",
        "metadata": {"rows": len(values), "column": str(net_col)},
    }


def _fetch_fii_net_5d() -> dict[str, Any] | None:
    return _fetch_flow_net_5d(factor="fii_net_5d", net_col_finder=_fii_net_column)


def _fetch_dii_net_5d() -> dict[str, Any] | None:
    return _fetch_flow_net_5d(factor="dii_net_5d", net_col_finder=_dii_net_column)


def _fetch_nifty_pe() -> dict[str, Any] | None:
    import yfinance as yf

    info = yf.Ticker("^NSEI").info or {}
    pe = info.get("trailingPE")
    if pe is None:
        return None
    return {
        "factor": "nifty_pe",
        "value": float(pe),
        "source": "yfinance",
        "metadata": {"symbol": "^NSEI", "field": "trailingPE"},
    }


def _fetch_nifty_pcr() -> dict[str, Any] | None:
    try:
        from trade_integrations.dataflows.openalgo import fetch_option_chain
    except ImportError:
        return None

    try:
        chain = fetch_option_chain("NIFTY", "NFO", strike_count=10)
    except Exception as exc:
        logger.debug("OpenAlgo NIFTY PCR fetch failed: %s", exc)
        return None

    pcr = chain.get("pcr")
    if pcr is None:
        return None
    return {
        "factor": "nifty_pcr",
        "value": float(pcr),
        "source": chain.get("source") or "openalgo",
        "metadata": {
            "expiry_date": chain.get("expiry_date"),
            "total_call_oi": chain.get("total_call_oi"),
            "total_put_oi": chain.get("total_put_oi"),
        },
    }


def _fetch_nifty_technical_factors() -> dict[str, Any] | None:
    from trade_integrations.dataflows.index_research.sources.history_loader import (
        load_nifty_history,
    )
    from trade_integrations.dataflows.index_research.technical_features import (
        technical_factor_rows,
    )

    history = load_nifty_history(days=90)
    rows = technical_factor_rows(history)
    if not rows:
        return None
    return {"rows": rows}


def _fetch_calendar_factors() -> dict[str, Any] | None:
    from datetime import date

    from trade_integrations.dataflows.index_research.calendar_features import (
        calendar_factor_rows,
    )

    rows = calendar_factor_rows(date.today())
    return {"rows": rows}


def _fetch_rbi_factors() -> dict[str, Any] | None:
    context = fetch_rbi_cpi_context()
    rows: list[dict[str, Any]] = []
    source = context.get("source", "rbi")

    if context.get("repo_rate") is not None:
        rows.append(
            {
                "factor": "repo_rate",
                "value": float(context["repo_rate"]),
                "source": source,
                "metadata": {"rbi_events": context.get("rbi_events", [])},
            }
        )
    if context.get("cpi_yoy_proxy") is not None:
        rows.append(
            {
                "factor": "cpi_yoy_proxy",
                "value": float(context["cpi_yoy_proxy"]),
                "source": source,
            }
        )

    if not rows:
        return None
    return {"rows": rows, "context": context}


def _fetch_index_sentiment(constituent_sentiments: list[float] | None) -> dict[str, Any] | None:
    if not constituent_sentiments:
        return None
    scores = [float(s) for s in constituent_sentiments]
    return {
        "factor": "index_sentiment",
        "value": sum(scores) / len(scores),
        "source": "constituent_finbert",
        "metadata": {"count": len(scores)},
    }


def _attempt_from_fetch(name: str, fetcher) -> SourceAttempt:
    try:
        payload = fetcher()
    except Exception as exc:
        return SourceAttempt(name=name, status="error", error=str(exc))
    if not payload:
        return SourceAttempt(
            name=name,
            status="error",
            error="no data",
            remediation=remediation_for("no_data"),
        )
    return SourceAttempt(name=name, status="ok", data=payload)


def _factor_row_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    factor_key = str(payload.get("factor") or "")
    from trade_integrations.dataflows.index_research.explain import _FACTOR_LABELS

    row: dict[str, Any] = {
        "factor": factor_key,
        "label": _FACTOR_LABELS.get(factor_key) or factor_key.replace("_", " ").title(),
        "value": payload["value"],
        "source": payload.get("source"),
    }
    if payload.get("z_score") is not None:
        row["z_score"] = payload["z_score"]
    if payload.get("metadata"):
        row["metadata"] = payload["metadata"]
    return row


def collect_global_factor_rows(*, constituent_sentiments: list[float] | None = None) -> list[dict]:
    """Return factor rows ready for ``save_daily_factors``."""
    return fetch_global_macro_snapshot(
        constituent_sentiments=constituent_sentiments
    ).data.get("factor_rows", [])


def fetch_global_macro_snapshot(
    *,
    constituent_sentiments: list[float] | None = None,
) -> StageResult:
    """Collect daily global macro factors for index research."""
    attempts: list[SourceAttempt] = []
    factor_rows: list[dict[str, Any]] = []
    factors: dict[str, Any] = {}

    for factor, symbol in _YFINANCE_FACTORS.items():
        attempt = _attempt_from_fetch(factor, lambda f=factor, s=symbol: _fetch_yfinance_factor(f, s))
        attempts.append(attempt)
        if attempt.status == "ok" and attempt.data:
            row = _factor_row_from_payload(attempt.data)
            factor_rows.append(row)
            factors[factor] = attempt.data["value"]

    for name, fetcher in (
        ("us_10y", _fetch_us_10y),
        ("india_vix", _fetch_india_vix),
        ("fii_net_5d", _fetch_fii_net_5d),
        ("dii_net_5d", _fetch_dii_net_5d),
        ("nifty_pe", _fetch_nifty_pe),
        ("nifty_pcr", _fetch_nifty_pcr),
    ):
        attempt = _attempt_from_fetch(name, fetcher)
        attempts.append(attempt)
        if attempt.status == "ok" and attempt.data:
            row = _factor_row_from_payload(attempt.data)
            factor_rows.append(row)
            factors[name] = attempt.data["value"]

    for name, fetcher in (
        ("nifty_technical", _fetch_nifty_technical_factors),
        ("calendar", _fetch_calendar_factors),
    ):
        attempt = _attempt_from_fetch(name, fetcher)
        attempts.append(attempt)
        if attempt.status == "ok" and attempt.data:
            for row in attempt.data.get("rows", []):
                factor_rows.append(row)
                factors[row["factor"]] = row["value"]

    rbi_attempt = _attempt_from_fetch("rbi_cpi", _fetch_rbi_factors)
    attempts.append(rbi_attempt)
    if rbi_attempt.status == "ok" and rbi_attempt.data:
        for row in rbi_attempt.data.get("rows", []):
            factor_rows.append(row)
            factors[row["factor"]] = row["value"]
        context = rbi_attempt.data.get("context") or {}
        rbi_events = context.get("rbi_events") or []
        if rbi_events:
            factors["rbi_events"] = rbi_events

    if constituent_sentiments:
        sentiment_attempt = _attempt_from_fetch(
            "index_sentiment",
            lambda: _fetch_index_sentiment(constituent_sentiments),
        )
        attempts.append(sentiment_attempt)
        if sentiment_attempt.status == "ok" and sentiment_attempt.data:
            row = _factor_row_from_payload(sentiment_attempt.data)
            factor_rows.append(row)
            factors["index_sentiment"] = sentiment_attempt.data["value"]
    else:
        attempts.append(
            SourceAttempt(
                name="index_sentiment",
                status="skipped",
                error="constituent_sentiments not provided",
            )
        )

    has_output = bool(factor_rows)
    status = stage_status_from_attempts(attempts, has_output=has_output)

    return StageResult(
        stage="macro_global",
        status=status,
        vendor="macro_global",
        fetched_at=_stage_now(),
        data={
            "factors": factors,
            "factor_rows": factor_rows,
            "source_attempts": [a.to_dict() for a in attempts],
        },
        errors=[f"{a.name}: {a.error}" for a in attempts if a.status != "ok" and a.error],
    )
