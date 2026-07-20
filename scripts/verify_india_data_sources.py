#!/usr/bin/env python3
"""Live verification of India company_research data sources and Tapetide fallbacks."""

from __future__ import annotations

import json
import os
import sys
import traceback
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tradingagents"))

import trade_integrations  # noqa: F401

from trade_integrations.clients import tapetide as tt
from trade_integrations.dataflows.company_research.market import Market, normalize_ticker
from trade_integrations.dataflows.company_research.source_registry import list_india_company_data_sources
from trade_integrations.dataflows.company_research.sources.bse_india import fetch_bse_calendar_events
from trade_integrations.dataflows.company_research.sources.calendar_in import fetch_calendar_in
from trade_integrations.dataflows.company_research.sources.fundamentals_in import fetch_fundamentals_in
from trade_integrations.dataflows.company_research.sources.identity_in import fetch_identity_in
from trade_integrations.dataflows.company_research.sources.peers_in import fetch_peers_in
from trade_integrations.dataflows.company_research.sources.screener_in import fetch_screener_peers
from trade_integrations.dataflows.index_research.factor_catalog import list_factor_catalog


SYMBOLS = ("RELIANCE", "TCS")


@dataclass
class Check:
    name: str
    status: str  # pass | fail | warn | skip
    detail: str = ""
    data: dict = field(default_factory=dict)


def _attempts_summary(stage_result) -> list[dict]:
    return (stage_result.data or {}).get("source_attempts") or []


def check_registry() -> Check:
    reg = list_india_company_data_sources()
    cat = list_factor_catalog()
    ok = (
        len(reg["sources"]) >= 8
        and "screener_in" in reg["stage_source_order"]["peers"]
        and "india_data_sources" in cat
        and "nselib" in reg.get("excluded_from_pipeline", [])
        and "moneycontrol_rss" in reg.get("excluded_from_pipeline", [])
    )
    return Check(
        "source_registry + factor_catalog",
        "pass" if ok else "fail",
        f"{len(reg['sources'])} sources; active={len(reg.get('active_sources', []))}",
        {
            "peer_order": reg["stage_source_order"]["peers"],
            "excluded": reg.get("excluded_from_pipeline"),
        },
    )


def check_openalgo(symbol: str) -> Check:
    try:
        from trade_integrations.openalgo.market_data import fetch_quote_raw

        quote = fetch_quote_raw(symbol)
        ltp = quote.get("ltp") if quote else None
        if ltp is None:
            keys = list(quote.keys()) if quote else []
            return Check("openalgo_quotes", "fail", "no ltp in response", {"keys": keys})
        return Check(
            "openalgo_quotes",
            "pass",
            f"LTP={ltp}",
            {"source": quote.get("source")},
        )
    except Exception as exc:
        return Check("openalgo_quotes", "fail", str(exc)[:200])


def check_yfinance(symbol: str) -> Check:
    try:
        import yfinance as yf

        info = yf.Ticker(f"{symbol}.NS").info or {}
        cal = yf.Ticker(f"{symbol}.NS").calendar
        sector = info.get("sector")
        earnings = cal.get("Earnings Date") if isinstance(cal, dict) else None
        if not sector:
            if "rate limit" in str(info).lower() or "too many" in str(info).lower():
                return Check("yfinance", "warn", "Yahoo rate limited (retry later)")
            return Check("yfinance", "warn", "no sector", {})
        return Check(
            "yfinance",
            "pass",
            f"sector={sector}",
            {"industry": info.get("industry"), "earnings": str(earnings)[:80]},
        )
    except Exception as exc:
        text = str(exc)
        if "rate limit" in text.lower() or "too many" in text.lower():
            return Check("yfinance", "warn", "Yahoo rate limited (retry later)")
        return Check("yfinance", "fail", text[:200])


def check_dalal_bse(symbol: str) -> Check:
    try:
        import dalal
        from trade_integrations.dataflows.company_research.sources.resilience import resolve_bse_scrip_code

        scrip = resolve_bse_scrip_code(symbol)
        if not scrip:
            return Check("dalal_bse", "fail", "no BSE scrip")
        meta = dalal.meta(scrip) or {}
        return Check(
            "dalal_bse",
            "pass",
            f"scrip={scrip} sector={meta.get('Sector') or meta.get('sector')}",
            {"pe": meta.get("PE") or meta.get("pe")},
        )
    except Exception as exc:
        return Check("dalal_bse", "fail", str(exc)[:200])


def check_dalal_nse(symbol: str) -> Check:
    try:
        import dalal

        dalal.quote(symbol, exchange="NSE")
        return Check("dalal_nse", "pass", "NSE quote OK (unexpected if Akamai blocks)")
    except Exception as exc:
        text = str(exc)
        if "403" in text or "Access Denied" in text:
            return Check("dalal_nse", "pass", "403 as expected — use BSE/yfinance instead")
        return Check("dalal_nse", "warn", text[:200])


def check_bse_calendar(symbol: str) -> Check:
    start = date.today() - timedelta(days=7)
    end = date.today() + timedelta(days=60)
    events = fetch_bse_calendar_events(symbol, start=start, end=end)
    return Check(
        "bse_calendar",
        "pass" if events else "warn",
        f"{len(events)} events",
        {"sample": events[0] if events else None},
    )


def check_screener_peers(symbol: str) -> Check:
    result = fetch_screener_peers(symbol, max_peers=8)
    if not result or not result.get("peers"):
        return Check("screener_peers", "fail", "no peers returned")
    peers = result["peers"]
    syms = [p["symbol"] for p in peers]
    return Check(
        "screener_peers",
        "pass" if len(peers) >= 3 else "warn",
        f"{len(peers)} peers: {', '.join(syms[:5])}",
        {"primary_source": result.get("primary_source")},
    )


def check_nselib_calendar() -> Check:
    try:
        from nselib import capital_market

        start = date.today().strftime("%d-%m-%Y")
        end = (date.today() + timedelta(days=30)).strftime("%d-%m-%Y")
        frame = capital_market.event_calendar_for_equity(from_date=start, to_date=end)
        rows = 0 if frame is None else len(frame)
        if rows:
            return Check("nselib_calendar", "pass", f"{rows} market-wide rows")
        return Check("nselib_calendar", "warn", "empty (known NSE fragility)")
    except Exception as exc:
        return Check("nselib_calendar", "warn", str(exc)[:120])


def check_tapetide_calendar_clean() -> Check:
    """Tapetide is always attempted; rate-limit text must never land in merged events."""
    if not tt.is_configured():
        return Check("tapetide_calendar_clean", "skip", "TAPETIDE_TOKEN not set")
    cal = fetch_calendar_in(normalize_ticker("RELIANCE", market_hint=Market.IN), lookahead_days=30, lookback_days=7)
    attempts = _attempts_summary(cal)
    names = [a.get("name") for a in attempts]
    bad = any(
        "free tier limit" in str(e.get("description", "")).lower()
        for e in (cal.data.get("events") or [])
    )
    if bad:
        return Check("tapetide_calendar_clean", "fail", "rate-limit garbage in calendar events")
    if "tapetide" not in names:
        return Check("tapetide_calendar_clean", "fail", "tapetide not in source_attempts")
    return Check(
        "tapetide_calendar_clean",
        "pass",
        f"tapetide attempted; status={next(a for a in attempts if a.get('name')=='tapetide').get('status')}",
    )


def check_batch_skips_tiered_apis() -> Check:
    from trade_integrations.dataflows.company_research.fetch_policy import (
        allow_tiered_apis,
        set_nifty50_batch,
    )

    set_nifty50_batch(True)
    try:
        tiered_ok = allow_tiered_apis()
        return Check(
            "batch_skips_tiered_apis",
            "pass" if not tiered_ok else "fail",
            "Nifty 50 batch disables Tapetide and Alpha Vantage",
        )
    finally:
        set_nifty50_batch(False)


def check_raw_source_proof(symbol: str) -> Check:
    """Independent raw-library probe — proves data exists outside our pipeline merge."""
    from datetime import timedelta

    from trade_integrations.dataflows.company_research.sources.bse_india import fetch_bse_calendar_events
    from trade_integrations.dataflows.company_research.sources.screener_in import fetch_screener_peers

    proof: dict = {}
    try:
        import dalal

        from trade_integrations.dataflows.company_research.sources.resilience import resolve_bse_scrip_code

        scrip = resolve_bse_scrip_code(symbol)
        meta = dalal.meta(scrip) if scrip else {}
        fund = dalal.fundamentals(scrip) if scrip else {}
        proof["dalal_meta_sector"] = meta.get("Sector")
        proof["dalal_fund_rows"] = len((fund or {}).get("resultinCr") or [])
        proof["dalal_announcements"] = len(dalal.announcements(scrip, exchange="BSE") or []) if scrip else 0
    except Exception as exc:
        proof["dalal_error"] = str(exc)[:120]

    start = date.today() - timedelta(days=7)
    end = date.today() + timedelta(days=60)
    bse_events = fetch_bse_calendar_events(symbol, start=start, end=end)
    proof["bse_calendar_events"] = len(bse_events)
    if bse_events:
        proof["bse_sample_date"] = bse_events[0].get("date")

    screener = fetch_screener_peers(symbol, max_peers=8) or {}
    proof["screener_peer_count"] = len(screener.get("peers") or [])

    ok = (
        proof.get("dalal_fund_rows", 0) >= 1
        and proof.get("bse_calendar_events", 0) >= 1
        and proof.get("screener_peer_count", 0) >= 3
    )
    return Check(
        f"raw_source_proof_{symbol}",
        "pass" if ok else "fail",
        f"dalal_rows={proof.get('dalal_fund_rows')} bse_ev={proof.get('bse_calendar_events')} screener_peers={proof.get('screener_peer_count')}",
        proof,
    )


def check_source_availability() -> Check:
    """Report in-process circuit breaker state for fragile India data vendors."""
    from trade_integrations.dataflows import source_availability as sa

    vendors = ("nselib", "openalgo", "yfinance", "tapetide")
    all_statuses = sa.list_all_statuses()
    circuits: dict[str, dict[str, str]] = {}
    blocked: list[str] = []
    for vendor in vendors:
        prefix = f"{vendor}:"
        entries = {
            key.removeprefix(prefix): status.value
            for key, status in all_statuses.items()
            if key.startswith(prefix)
        }
        circuits[vendor] = entries or {"_default": sa.SourceStatus.AVAILABLE.value}
        if any(status != sa.SourceStatus.AVAILABLE.value for status in entries.values()):
            blocked.append(vendor)

    if blocked:
        detail = f"open circuits: {', '.join(blocked)}"
        status = "warn"
    else:
        detail = "all circuits available"
    return Check(
        "source_availability",
        status,
        detail,
        {"circuits": circuits},
    )


def check_stage(symbol: str, stage_fn, *, min_peers: int = 0, min_events: int = 0) -> Check:
    try:
        n = normalize_ticker(symbol, market_hint=Market.IN)
        if stage_fn == "identity":
            r = fetch_identity_in(n)
            ok = bool(r.data.get("sector") or r.data.get("industry"))
            detail = f"sector={r.data.get('sector')} vendor={r.vendor}"
        elif stage_fn == "peers":
            ident = fetch_identity_in(n)
            industry = str(ident.data.get("industry") or "")
            r = fetch_peers_in(n, industry_hint=industry)
            peers = r.data.get("peers") or []
            ok = len(peers) >= min_peers or bool(r.data.get("sector_context"))
            detail = f"{len(peers)} peers vendor={r.vendor}"
        elif stage_fn == "calendar":
            r = fetch_calendar_in(n, lookahead_days=60, lookback_days=7)
            events = r.data.get("events") or []
            ok = len(events) >= min_events
            bad = [e for e in events if "free tier" in str(e.get("description", "")).lower()]
            if bad:
                return Check(f"stage_{stage_fn}_{symbol}", "fail", "rate-limit text in events")
            detail = f"{len(events)} events vendor={r.vendor}"
        elif stage_fn == "fundamentals":
            r = fetch_fundamentals_in(n)
            ok = bool(r.data.get("ratios") or r.data.get("quarterly_results") or r.data.get("sources"))
            detail = f"vendor={r.vendor} sources={list((r.data.get('sources') or {}).keys())}"
        else:
            return Check(stage_fn, "skip", "unknown stage")
        attempts = _attempts_summary(r)
        core_errors = list(r.errors or [])
        proof = {
            "stage_status": r.status,
            "core_errors": core_errors,
            "data_ok": ok,
        }
        if stage_fn == "identity":
            proof["sector"] = r.data.get("sector")
            proof["last_price"] = r.data.get("last_price")
        elif stage_fn == "peers":
            proof["peer_count"] = len(r.data.get("peers") or [])
        elif stage_fn == "calendar":
            proof["event_count"] = len(r.data.get("events") or [])
        elif stage_fn == "fundamentals":
            proof["ratio_keys"] = list((r.data.get("ratios") or {}).keys())[:8]
        status = "pass" if ok else "warn"
        if ok and core_errors:
            status = "pass"  # data proven; core_errors are informational (e.g. OpenAlgo down, yfinance ok)
        return Check(
            f"stage_{stage_fn}_{symbol}",
            status,
            detail,
            {"status": r.status, "attempts": attempts, "proof": proof},
        )
    except Exception as exc:
        return Check(f"stage_{stage_fn}_{symbol}", "fail", traceback.format_exc()[-300:])



def main() -> int:
    checks: list[Check] = [
        check_registry(),
        check_tapetide_calendar_clean(),
        check_batch_skips_tiered_apis(),
    ]

    for sym in SYMBOLS:
        checks.append(check_raw_source_proof(sym))
        checks.extend(
            [
                check_openalgo(sym),
                check_yfinance(sym),
                check_dalal_bse(sym),
                check_dalal_nse(sym),
                check_bse_calendar(sym),
                check_screener_peers(sym),
                check_stage(sym, "identity"),
                check_stage(sym, "peers", min_peers=3),
                check_stage(sym, "calendar", min_events=1),
                check_stage(sym, "fundamentals"),
            ]
        )

    checks.append(check_source_availability())

    summary = {"pass": 0, "fail": 0, "warn": 0, "skip": 0}
    rows = []
    for c in checks:
        summary[c.status] = summary.get(c.status, 0) + 1
        rows.append({"name": c.name, "status": c.status, "detail": c.detail, "data": c.data})

    report = {"summary": summary, "checks": rows}
    proof_rows = [
        r for r in rows
        if r["name"].startswith("stage_") and isinstance((r.get("data") or {}).get("proof"), dict)
    ]
    report["proof"] = {
        r["name"]: r["data"]["proof"] for r in proof_rows
    }
    print(json.dumps(report, indent=2, default=str))

    fails = [r for r in rows if r["status"] == "fail"]
    if fails:
        print("\nFAILED:", ", ".join(r["name"] for r in fails), file=sys.stderr)
        return 1
    print(f"\nOK: {summary['pass']} pass, {summary['warn']} warn, {summary['skip']} skip", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
