"""Unified market resolution for autonomous agent creation."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Literal

from trade_integrations.dataflows.company_research.india_symbols import (
    india_index_tickers,
    is_india_listed_symbol,
)
from trade_integrations.dataflows.company_research.market import Market, detect_market
from trade_integrations.dataflows.company_research.us_symbols import is_us_known_symbol

MarketCode = Literal["IN", "US"]
Confidence = Literal["explicit", "registry", "hint", "default"]

_INDEX_ALIASES: dict[str, str] = {
    "NIFTY50": "NIFTY",
    "^NSEI": "NIFTY",
    "^BSESN": "SENSEX",
}

_INDEX_ETF_PROXIES: dict[str, str] = {
    "NIFTYBEES": "NIFTY",
    "SETFNIF50": "NIFTY",
    "SETFNIFTY": "NIFTY",
    "JUNIORBEES": "NIFTY",
}

_INDEX_NAMES = frozenset({"NIFTY", "NIFTY50", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX"})

_IN_HINT_RE = re.compile(
    r"\b(india|indian|nse|bse|nifty|banknifty|₹|inr|openalgo|nautilus)\b",
    re.I,
)
_US_HINT_RE = re.compile(r"\b(us|usa|alpaca|nasdaq|nyse|america|usd)\b|\$", re.I)


@dataclass(frozen=True)
class SymbolCanonicalization:
    canonical_symbol: str
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class MarketResolution:
    market: MarketCode
    canonical_symbol: str
    openalgo_exchange: str | None
    confidence: Confidence
    warnings: tuple[str, ...] = field(default_factory=tuple)


def canonicalize_autonomous_symbol(symbol: str, *, user_text: str = "") -> SymbolCanonicalization:
    raw = symbol.strip().upper()
    if not raw:
        return SymbolCanonicalization("NIFTY")

    if raw in _INDEX_ALIASES:
        return SymbolCanonicalization(_INDEX_ALIASES[raw])

    if user_text and raw in _INDEX_ETF_PROXIES:
        upper_text = user_text.upper()
        for index_name in sorted(_INDEX_NAMES, key=len, reverse=True):
            if re.search(rf"\b{re.escape(index_name)}\b", upper_text):
                proxy_target = _INDEX_ETF_PROXIES[raw]
                if index_name == proxy_target or index_name in {"NIFTY", "NIFTY50"}:
                    return SymbolCanonicalization(
                        index_name if index_name != "NIFTY50" else "NIFTY",
                        warnings=(
                            f"Replaced ETF proxy {raw} with index {index_name} from user message.",
                        ),
                    )

    return SymbolCanonicalization(raw)


def _hint_market(user_text: str) -> MarketCode | None:
    text = user_text or ""
    has_in = bool(_IN_HINT_RE.search(text))
    has_us = bool(_US_HINT_RE.search(text))
    if has_in and not has_us:
        return "IN"
    if has_us and not has_in:
        return "US"
    return None


def _openalgo_exchange_for(symbol: str, market: MarketCode) -> str | None:
    if market != "IN":
        return None
    try:
        from trade_integrations.dataflows.openalgo import resolve_openalgo_symbol

        _, exchange = resolve_openalgo_symbol(symbol)
        return exchange or "NSE"
    except Exception:
        return "NSE"


def _validate_explicit_market(symbol: str, market: MarketCode) -> str | None:
    """Return error message when explicit market contradicts symbol registry."""
    try:
        registry_market = detect_market(symbol)
    except Exception:
        registry_market = None
    if registry_market == Market.IN and market == "US":
        return f"Symbol {symbol} is India-listed; execution_market US is invalid."
    if registry_market == Market.US and is_us_known_symbol(symbol) and market == "IN":
        return f"Symbol {symbol} is US-listed; execution_market IN is invalid."
    return None


def resolve_execution_market(
    symbol: str,
    *,
    user_text: str = "",
    market_hint: str | None = None,
    session_config: dict | None = None,
) -> MarketResolution:
    canon = canonicalize_autonomous_symbol(symbol, user_text=user_text)
    sym = canon.canonical_symbol
    warnings = list(canon.warnings)

    explicit_hint = str(market_hint or "").strip().upper()
    if not explicit_hint and session_config:
        explicit_hint = str(session_config.get("execution_market") or "").strip().upper()
    if explicit_hint in {"IN", "US"}:
        err = _validate_explicit_market(sym, explicit_hint)  # type: ignore[arg-type]
        if err:
            warnings.append(err)
        return MarketResolution(
            market=explicit_hint,  # type: ignore[arg-type]
            canonical_symbol=sym,
            openalgo_exchange=_openalgo_exchange_for(sym, explicit_hint),  # type: ignore[arg-type]
            confidence="explicit",
            warnings=tuple(warnings),
        )

    try:
        detected = detect_market(sym)
        if detected == Market.IN:
            return MarketResolution(
                market="IN",
                canonical_symbol=sym,
                openalgo_exchange=_openalgo_exchange_for(sym, "IN"),
                confidence="registry",
                warnings=tuple(warnings),
            )
        if detected == Market.US and is_us_known_symbol(sym):
            return MarketResolution(
                market="US",
                canonical_symbol=sym,
                openalgo_exchange=None,
                confidence="registry",
                warnings=tuple(warnings),
            )
    except Exception:
        pass

    if is_india_listed_symbol(sym):
        return MarketResolution(
            market="IN",
            canonical_symbol=sym,
            openalgo_exchange=_openalgo_exchange_for(sym, "IN"),
            confidence="registry",
            warnings=tuple(warnings),
        )

    if is_us_known_symbol(sym):
        return MarketResolution(
            market="US",
            canonical_symbol=sym,
            openalgo_exchange=None,
            confidence="registry",
            warnings=tuple(warnings),
        )

    hinted = _hint_market(user_text)
    if hinted:
        return MarketResolution(
            market=hinted,
            canonical_symbol=sym,
            openalgo_exchange=_openalgo_exchange_for(sym, hinted),
            confidence="hint",
            warnings=tuple(warnings),
        )

    default = (os.getenv("TRADINGAGENTS_RESEARCH_MARKET_DEFAULT", "IN") or "IN").upper()
    market: MarketCode = "IN" if default == "IN" else "US"
    return MarketResolution(
        market=market,
        canonical_symbol=sym,
        openalgo_exchange=_openalgo_exchange_for(sym, market),
        confidence="default",
        warnings=tuple(warnings),
    )


def resolve_proposal_symbols(
    symbols: list[str],
    *,
    user_text: str = "",
    market_hint: str | None = None,
    session_config: dict | None = None,
) -> tuple[list[str], MarketResolution, tuple[str, ...]]:
    """Canonicalize symbol list and resolve market from primary symbol."""
    if not symbols:
        symbols = ["NIFTY"]
    canonicalized: list[str] = []
    all_warnings: list[str] = []
    for raw in symbols:
        canon = canonicalize_autonomous_symbol(str(raw), user_text=user_text)
        if canon.canonical_symbol not in canonicalized:
            canonicalized.append(canon.canonical_symbol)
        all_warnings.extend(canon.warnings)

    primary = canonicalized[0]
    resolution = resolve_execution_market(
        primary,
        user_text=user_text,
        market_hint=market_hint,
        session_config=session_config,
    )
    all_warnings.extend(resolution.warnings)
    return canonicalized, resolution, tuple(all_warnings)


def index_symbols() -> frozenset[str]:
    return india_index_tickers()
