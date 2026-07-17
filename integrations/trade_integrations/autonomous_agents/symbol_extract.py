"""Extract India/US symbols from orchestrator chat (no Vibe dependency)."""

from __future__ import annotations

import re

from trade_integrations.autonomous_agents.market import symbol_execution_market
from trade_integrations.dataflows.company_research.market import india_index_tickers

_IN_INDICES = frozenset(
    {
        "NIFTY",
        "NIFTY50",
        "BANKNIFTY",
        "FINNIFTY",
        "MIDCPNIFTY",
        "SENSEX",
        "^NSEI",
        "^BSESN",
    }
)

_STOPWORDS = frozenset(
    {
        "AI",
        "API",
        "ATM",
        "BUY",
        "CE",
        "CEO",
        "CFO",
        "ETF",
        "FNO",
        "FO",
        "GST",
        "HOLD",
        "IV",
        "LTP",
        "MIS",
        "OI",
        "PE",
        "PCR",
        "POT",
        "ROI",
        "SELL",
        "STT",
        "USD",
        "THE",
        "AND",
        "FOR",
        "NOT",
        "YOU",
        "ALL",
        "MAX",
        "MIN",
        "PAPER",
        "TRADE",
        "LOSS",
        "AGENT",
        "SWING",
        "WATCH",
        "EVERY",
        "BUDGET",
        "CREATE",
        "BUILD",
        "START",
        "MAKE",
        "AUTONOMOUS",
        "INTRADAY",
    }
)

_TICKER_RE = re.compile(r"\b([A-Z][A-Z0-9&.-]{1,14})\b")

_COMMON_US_SYMBOLS = frozenset(
    {
        "AAPL",
        "AMD",
        "AMZN",
        "GOOG",
        "GOOGL",
        "INTC",
        "META",
        "MSFT",
        "NVDA",
        "QQQ",
        "SPY",
        "TSLA",
    }
)

_FOR_SYMBOL_RE = re.compile(r"\b(?:for|trade|on|symbol)\s+([A-Z]{2,12})\b", re.I)


def _filter_india_listed(candidates: list[str]) -> list[str]:
    try:
        from trade_integrations.dataflows.company_research.india_symbols import (
            is_india_listed_symbol,
        )

        return [c for c in candidates if is_india_listed_symbol(c)]
    except Exception:
        return candidates


def extract_primary_ticker(text: str) -> str | None:
    """Return the first plausible India index or equity ticker in the message."""
    if not text:
        return None
    upper = text.upper()
    for index in sorted(_IN_INDICES, key=len, reverse=True):
        if re.search(rf"\b{re.escape(index)}\b", upper):
            return index.replace("^", "") if index.startswith("^") else index

    candidates: list[str] = []
    for match in _TICKER_RE.finditer(upper):
        token = match.group(1).rstrip(".")
        if token.endswith(".NS") or token.endswith(".BO"):
            candidates.append(token.split(".")[0])
            continue
        if token in _STOPWORDS or token in _IN_INDICES:
            continue
        if len(token) < 2 or len(token) > 12 or token.isdigit():
            continue
        candidates.append(token)

    if not candidates:
        return None

    listed = _filter_india_listed(candidates)
    return listed[0] if listed else candidates[0]


def extract_orchestrator_symbols(text: str) -> list[str]:
    """Collect symbols mentioned in orchestrator user/assistant text."""
    upper = (text or "").upper()
    found: list[str] = []
    seen: set[str] = set()

    for sym in sorted(india_index_tickers(), key=len, reverse=True):
        base = sym.lstrip("^")
        if re.search(rf"\b{re.escape(base)}\b", upper):
            canonical = "NIFTY" if base in {"NIFTY", "NIFTY50", "^NSEI"} else base
            if canonical not in seen:
                seen.add(canonical)
                found.append(canonical)

    for sym in _COMMON_US_SYMBOLS:
        if re.search(rf"\b{sym}\b", upper) and sym not in seen:
            seen.add(sym)
            found.append(sym)

    primary = extract_primary_ticker(text)
    if primary and primary not in seen:
        seen.add(primary)
        found.append(primary)

    for match in _FOR_SYMBOL_RE.finditer(upper):
        token = match.group(1).upper()
        if token in seen:
            continue
        market = symbol_execution_market(token)
        if market in {"US", "IN"}:
            seen.add(token)
            found.append(token)

    return found
