"""Multi-source fetch helpers with explicit fallback attribution."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Why a source failed and what to do about it.
REMEDIATION_HINTS: dict[str, str] = {
    "nse_403": (
        "NSE blocks direct API/scraper requests (Akamai bot protection). "
        "Run scripts/run_nse_browser_fetch.py --mission fii_dii_history --refresh-cookies, "
        "or use OpenAlgo/nselib/BSE fallbacks."
    ),
    "openalgo_not_configured": (
        "Set OPENALGO_API_KEY in .env after logging into OpenAlgo and generating an API key."
    ),
    "openalgo_unreachable": (
        "Start OpenAlgo (./start.sh or make start) and ensure OPENALGO_HOST points to it."
    ),
    "bse_code_missing": (
        "Add a BSE scrip code mapping via TRADINGAGENTS_BSE_CODE_MAP "
        '(e.g. {"RELIANCE":"500325","TCS":"532540"}) to enable dalal BSE fallbacks.'
    ),
    "not_installed": "Install the optional dependency: pip install -e \".[research]\"",
    "tapetide_not_configured": (
        "Set TAPETIDE_TOKEN from https://tapetide.com/settings/tokens for "
        "identity, calendar, and peer enrichment."
    ),
    "tapetide_rate_limited": (
        "Tapetide free-tier quota exhausted. Unset TAPETIDE_TOKEN, set TAPETIDE_ENABLED=false, "
        "or wait for reset. Peers/calendar fall back to screener.in, BSE, and yfinance."
    ),
    "vendor_rate_limited": (
        "Upstream vendor rate-limited this request (often yfinance). Retry later or rely on "
        "OpenAlgo, dalal BSE, BSE, and screener.in fallbacks."
    ),
    "tapetide_batch_disabled": (
        "Tapetide skipped for Nifty batch (TAPETIDE_BATCH=false). Set TAPETIDE_BATCH=true to include."
    ),
    "no_data": "Source responded but returned no rows for this ticker/date window.",
    "weekend_trade_date": (
        "nselib pe_ratio needs a valid NSE trading date; retry on a market day."
    ),
}


@dataclass
class SourceAttempt:
    """One backend tried for a pipeline stage."""

    name: str
    status: str  # ok | error | skipped
    data: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    remediation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "error": self.error,
            "remediation": self.remediation,
            "has_data": bool(self.data),
        }


def classify_error(exc: Exception | str) -> str:
    text = str(exc).lower()
    if "403" in text or "access denied" in text or "forbidden" in text:
        return "nse_403"
    if "tapetide" in text and ("token" in text or "401" in text or "authentication" in text):
        return "tapetide_not_configured"
    if "free tier limit" in text or "tapetideratelimit" in text.replace(" ", ""):
        return "tapetide_rate_limited"
    if "rate limit" in text or "too many requests" in text:
        return "vendor_rate_limited"
    if "not configured" in text or "openalgo_api_key" in text or (
        "openalgo" in text and ("apikey" in text or "api key" in text)
    ):
        return "openalgo_not_configured"
    if "connection" in text or "refused" in text or "timed out" in text:
        return "openalgo_unreachable"
    if "not installed" in text or "no module named" in text:
        return "not_installed"
    if "no data" in text:
        return "no_data"
    if "trade_date" in text or "data not found" in text:
        return "weekend_trade_date"
    return "unknown"


def remediation_for(code: str) -> str:
    return REMEDIATION_HINTS.get(code, "Check logs and network connectivity for this source.")


def run_sources(
    fetchers: list[tuple[str, Callable[[], dict[str, Any] | None]]],
) -> list[SourceAttempt]:
    """Run every fetcher; never stop at the first success."""
    attempts: list[SourceAttempt] = []
    for name, fetcher in fetchers:
        try:
            payload = fetcher()
        except Exception as exc:
            code = classify_error(exc)
            attempts.append(
                SourceAttempt(
                    name=name,
                    status="error",
                    error=str(exc),
                    remediation=remediation_for(code),
                )
            )
            logger.info("Source %s failed: %s", name, exc)
            continue
        if not payload:
            attempts.append(
                SourceAttempt(
                    name=name,
                    status="error",
                    error="no data",
                    remediation=remediation_for("no_data"),
                )
            )
            continue
        attempts.append(SourceAttempt(name=name, status="ok", data=payload))
    return attempts


def merge_identity_fields(attempts: list[SourceAttempt]) -> dict[str, Any]:
    """Merge identity payloads; later sources fill gaps only."""
    priority = ("openalgo", "tapetide", "yfinance", "dalal_bse", "dalal_nse", "nselib")
    ordered = sorted(
        [a for a in attempts if a.status == "ok" and a.data],
        key=lambda a: priority.index(a.name) if a.name in priority else 99,
    )
    merged: dict[str, Any] = {"sources": {}}
    for attempt in ordered:
        merged["sources"][attempt.name] = attempt.data
        for key, value in attempt.data.items():
            if key == "source":
                continue
            if value in (None, "", [], {}):
                continue
            merged[key] = value
    if ordered:
        merged["primary_source"] = ordered[0].name
    return merged


def stage_status_from_attempts(attempts: list[SourceAttempt], *, has_output: bool) -> str:
    ok_count = sum(1 for a in attempts if a.status == "ok")
    if ok_count and not has_output:
        return "partial"
    if ok_count == len(attempts):
        return "ok"
    if ok_count > 0:
        return "partial"
    return "error"


def load_bse_code_map() -> dict[str, str]:
    """Optional NSE symbol → BSE scrip code map for dalal BSE routes."""
    raw = os.getenv("TRADINGAGENTS_BSE_CODE_MAP", "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return {str(k).upper(): str(v) for k, v in parsed.items()}
        except json.JSONDecodeError:
            logger.warning("Invalid TRADINGAGENTS_BSE_CODE_MAP JSON; ignoring.")
    # Minimal defaults so dalal BSE works for common Nifty names without extra config.
    return {
        "RELIANCE": "500325",
        "TCS": "532540",
        "INFY": "500209",
        "HDFCBANK": "500180",
        "ICICIBANK": "532174",
        "ITC": "500875",
        "SBIN": "500112",
        "BHARTIARTL": "532454",
    }


_bse_scrip_auto_cache: dict[str, str | None] = {}


def _lookup_bse_scrip_via_api(symbol: str) -> str | None:
    symbol_upper = symbol.strip().upper()
    if symbol_upper in _bse_scrip_auto_cache:
        return _bse_scrip_auto_cache[symbol_upper]
    try:
        from bse import BSE
    except ImportError:
        _bse_scrip_auto_cache[symbol_upper] = None
        return None
    scrip: str | None = None
    try:
        with BSE("./") as client:
            scrip = str(client.getScripCode(symbol_upper) or "").strip() or None
    except Exception as exc:
        logger.info("BSE auto scrip lookup failed for %s: %s", symbol_upper, exc)
    _bse_scrip_auto_cache[symbol_upper] = scrip
    return scrip


def resolve_bse_scrip_code(symbol: str, *, auto_lookup: bool = True) -> str | None:
    symbol_upper = symbol.strip().upper()
    mapped = load_bse_code_map().get(symbol_upper)
    if mapped:
        return mapped
    if auto_lookup:
        return _lookup_bse_scrip_via_api(symbol_upper)
    return None
