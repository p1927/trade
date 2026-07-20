"""Direct HTTP fetch via curl_cffi + nselib before launching browser."""

from __future__ import annotations

import json
import logging
from typing import Any

import pandas as pd

from trade_integrations.nse_browser.http_bridge import HttpBridge
from trade_integrations.nse_browser.nse_urls import FII_DII_API_CSV, FII_DII_API_JSON, FII_DII_REPORT_PAGE, NSE_HOME
from trade_integrations.nse_browser.parsers.fii_dii import parse_fii_dii_csv, parse_fii_dii_json

logger = logging.getLogger(__name__)


def fetch_fii_dii_react_csv(cookies: list[dict[str, Any]] | None = None) -> pd.DataFrame:
    """Fetch today's FII/DII CSV via fiidiiTradeReact?csv=true."""
    return fetch_fii_dii_csv_url(FII_DII_API_CSV, cookies)


def fetch_fii_dii_json(cookies: list[dict[str, Any]] | None = None) -> pd.DataFrame:
    """Fetch today's FII/DII from NSE fiidiiTradeReact JSON."""
    bridge = HttpBridge(cookies)
    status, text = bridge.get_text(FII_DII_API_JSON, referer=FII_DII_REPORT_PAGE)
    if status != 200:
        return pd.DataFrame()
    return parse_fii_dii_json(text)


def fetch_fii_dii_csv_url(url: str, cookies: list[dict[str, Any]] | None = None) -> pd.DataFrame:
    bridge = HttpBridge(cookies)
    status, text = bridge.get_text(url, referer=FII_DII_REPORT_PAGE)
    if status != 200 or not text.strip():
        return pd.DataFrame()
    if text.lstrip().startswith("{"):
        return parse_fii_dii_json(text)
    return parse_fii_dii_csv(text, variant="combined" if "bse" in url.lower() else "nse_only")


def bootstrap_nse_session_cookies() -> list[dict[str, Any]]:
    """Warm NSE homepage via HTTP to seed cookies (fast path before nodriver)."""
    bridge = HttpBridge([])
    status, _ = bridge.get_text(NSE_HOME)
    if status != 200:
        logger.debug("NSE homepage warm returned %s", status)
    return bridge.cookies


def fetch_nselib_fpi_latest() -> pd.DataFrame:
    from trade_integrations.dataflows import source_availability

    capability = "nsdl_fpi_latest"
    if not source_availability.should_attempt("nselib", capability):
        return pd.DataFrame()

    try:
        from nselib import cash_market
    except ImportError as exc:
        source_availability.record_failure("nselib", capability, exc)
        return pd.DataFrame()
    try:
        raw = cash_market.nsdl_fpi_latest_investment_activity()
    except Exception as exc:
        source_availability.record_failure("nselib", capability, exc)
        logger.debug("nselib nsdl_fpi_latest failed: %s", exc)
        return pd.DataFrame()
    if raw is None or raw.empty:
        source_availability.record_failure("nselib", capability, "empty nsdl_fpi_latest frame")
        return pd.DataFrame()
    from trade_integrations.nse_browser.parsers.fpi import parse_fpi_investment_table

    source_availability.record_success("nselib", capability)
    return parse_fpi_investment_table(raw, source="nselib")


def rows_from_agent_table(agent_rows: list[dict[str, Any]]) -> pd.DataFrame:
    """Normalize MiniMax-extracted table rows into fii_dii daily schema."""
    if not agent_rows:
        return pd.DataFrame()
    out: list[dict[str, Any]] = []
    for row in agent_rows:
        if not isinstance(row, dict):
            continue
        day = str(row.get("date") or row.get("Date") or row.get("reporting_date") or "")[:10]
        if not day:
            continue
        entry: dict[str, Any] = {"date": day, "source": "minimax_agent"}
        cat = str(row.get("category") or row.get("Category") or "").upper()
        for src, dest in (
            ("net", "fii_net"),
            ("net_value", "fii_net"),
            ("Net Value", "fii_net"),
            ("fii_net", "fii_net"),
            ("dii_net", "dii_net"),
        ):
            if src in row and row[src] is not None:
                try:
                    val = float(row[src])
                except (TypeError, ValueError):
                    continue
                if "FII" in cat or "FPI" in cat:
                    entry["fii_net"] = val
                elif "DII" in cat:
                    entry["dii_net"] = val
                elif dest == "fii_net" and "fii_net" not in entry:
                    entry["fii_net"] = val
                elif dest == "dii_net" and "dii_net" not in entry:
                    entry["dii_net"] = val
        if "fii_net" in entry or "dii_net" in entry:
            out.append(entry)
    if not out:
        return pd.DataFrame()
    return pd.DataFrame(out)
