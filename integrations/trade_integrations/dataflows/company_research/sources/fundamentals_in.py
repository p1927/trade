"""India market fundamentals — dalal BSE, yfinance, Tapetide, nselib."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from ..market import NormalizedTicker
from ..models import StageResult
from .resilience import (
    SourceAttempt,
    remediation_for,
    resolve_bse_scrip_code,
    run_sources,
    stage_status_from_attempts,
)

logger = logging.getLogger(__name__)


def _stage_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dalal_fundamentals(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize dalal fundamentals table into key metrics."""
    metrics: dict[str, Any] = {"quarters": {}, "rows": []}
    period_cols = {
        "v1": raw.get("col2") or "Q1",
        "v2": raw.get("col3") or "Q2",
        "v3": raw.get("col4") or "FY",
    }
    for row in raw.get("resultinCr") or []:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "").strip()
        if not title:
            continue
        entry = {
            "metric": title,
            period_cols["v1"]: row.get("v1"),
            period_cols["v2"]: row.get("v2"),
            period_cols["v3"]: row.get("v3"),
        }
        metrics["rows"].append(entry)
        key = title.lower().replace(" ", "_").replace("%", "pct")
        metrics["quarters"][key] = {
            period_cols["v1"]: row.get("v1"),
            period_cols["v2"]: row.get("v2"),
            period_cols["v3"]: row.get("v3"),
        }
    return metrics


def _fetch_dalal_bse(normalized: NormalizedTicker) -> dict[str, Any] | None:
    import dalal  # type: ignore[import-untyped]

    scrip = resolve_bse_scrip_code(normalized.base_symbol)
    if not scrip:
        return None
    meta = dalal.meta(scrip) or {}
    fundamentals_raw = dalal.fundamentals(scrip) or {}
    parsed = _parse_dalal_fundamentals(fundamentals_raw) if fundamentals_raw else {}
    ratios = {
        "pe_ratio": meta.get("PE") or meta.get("ConPE"),
        "pb_ratio": meta.get("PB"),
        "roe_pct": meta.get("ROE"),
        "opm_pct": meta.get("OPM"),
        "npm_pct": meta.get("NPM"),
        "eps": meta.get("EPS"),
        "face_value": meta.get("FaceVal"),
    }
    return {
        "source": "dalal_bse",
        "bse_scrip_code": scrip,
        "sector": meta.get("Sector") or "",
        "industry": meta.get("IndustryNew") or meta.get("Industry") or "",
        "ratios": {k: v for k, v in ratios.items() if v not in (None, "", "-")},
        "financials": parsed,
    }


def _fetch_yfinance(normalized: NormalizedTicker) -> dict[str, Any] | None:
    import yfinance as yf

    info = yf.Ticker(normalized.yfinance_symbol).info or {}
    if not info:
        return None
    return {
        "source": "yfinance",
        "market_cap": info.get("marketCap"),
        "pe_ratio": info.get("trailingPE") or info.get("forwardPE"),
        "pb_ratio": info.get("priceToBook"),
        "roe_pct": info.get("returnOnEquity"),
        "profit_margin_pct": info.get("profitMargins"),
        "operating_margin_pct": info.get("operatingMargins"),
        "revenue": info.get("totalRevenue"),
        "ebitda": info.get("ebitda"),
        "eps": info.get("trailingEps"),
        "dividend_yield_pct": info.get("dividendYield"),
        "currency": info.get("currency") or "INR",
    }


def _fetch_tapetide(symbol: str) -> dict[str, Any] | None:
    from trade_integrations.clients.tapetide import get_company_profile

    profile = get_company_profile(symbol, include_peers=False)
    fundamentals = profile.get("fundamentals") or profile.get("key_ratios") or {}
    if not fundamentals:
        return None
    return {
        "source": "tapetide",
        "ratios": fundamentals,
    }


def _fetch_nselib_financials(normalized: NormalizedTicker) -> dict[str, Any] | None:
    from datetime import timedelta

    from nselib import capital_market

    end = datetime.now().date()
    start = end - timedelta(days=730)
    frame = capital_market.financial_results_for_equity(
        from_date=start.strftime("%d-%m-%Y"),
        to_date=end.strftime("%d-%m-%Y"),
        fin_period="Quarterly",
    )
    if frame is None or frame.empty or "symbol" not in frame.columns:
        return None
    subset = frame[frame["symbol"].astype(str).str.upper() == normalized.base_symbol]
    if subset.empty:
        return None
    rows = subset.head(4).to_dict(orient="records")
    return {"source": "nselib", "quarterly_results": rows}


_RATIO_KEYS = frozenset(
    {
        "pe_ratio",
        "pb_ratio",
        "roe_pct",
        "opm_pct",
        "npm_pct",
        "eps",
        "profit_margin_pct",
        "operating_margin_pct",
        "dividend_yield_pct",
        "face_value",
    }
)


def _merge_fundamentals(attempts: list[SourceAttempt]) -> dict[str, Any]:
    merged: dict[str, Any] = {"sources": {}}
    for attempt in attempts:
        if attempt.status != "ok" or not attempt.data:
            continue
        merged["sources"][attempt.name] = attempt.data
        for key, value in attempt.data.items():
            if key in ("source", "financials", "quarterly_results", "ratios"):
                if key == "ratios" and isinstance(value, dict):
                    merged.setdefault("ratios", {}).update(value)
                elif key == "financials" and value:
                    merged["financials"] = value
                elif key == "quarterly_results" and value:
                    merged["quarterly_results"] = value
                else:
                    if value not in (None, "", [], {}):
                        merged[key] = value
            elif key in _RATIO_KEYS:
                merged.setdefault("ratios", {})[key] = value
            elif value not in (None, "", [], {}):
                merged.setdefault(key, value)
    if merged.get("sources"):
        merged["primary_source"] = next(iter(merged["sources"]))
    return merged


def fetch_fundamentals_in(normalized: NormalizedTicker) -> StageResult:
    """Collect latest fundamental metrics for an India equity."""
    fetchers: list[tuple[str, Any]] = [
        ("yfinance", lambda: _fetch_yfinance(normalized)),
    ]
    if resolve_bse_scrip_code(normalized.base_symbol):
        fetchers.insert(0, ("dalal_bse", lambda: _fetch_dalal_bse(normalized)))
    else:
        pass

    try:
        import nselib  # noqa: F401

        fetchers.append(("nselib", lambda: _fetch_nselib_financials(normalized)))
    except ImportError:
        pass

    from trade_integrations.clients.tapetide import is_configured as tapetide_configured

    if tapetide_configured():
        fetchers.insert(1 if fetchers and fetchers[0][0] == "dalal_bse" else 0, (
            "tapetide",
            lambda: _fetch_tapetide(normalized.base_symbol),
        ))

    attempts = run_sources(fetchers)
    merged = _merge_fundamentals(attempts)
    has_output = bool(merged.get("ratios") or merged.get("financials") or merged.get("quarterly_results"))
    status = stage_status_from_attempts(attempts, has_output=has_output)

    if not resolve_bse_scrip_code(normalized.base_symbol):
        attempts.append(
            SourceAttempt(
                name="dalal_bse",
                status="skipped",
                error="bse_code_missing",
                remediation=remediation_for("bse_code_missing"),
            )
        )

    return StageResult(
        stage="fundamentals",
        status=status,
        vendor=merged.get("primary_source") or "fundamentals_in",
        fetched_at=_stage_now(),
        data={**merged, "source_attempts": [a.to_dict() for a in attempts]},
        errors=[f"{a.name}: {a.error}" for a in attempts if a.status != "ok" and a.error],
    )
