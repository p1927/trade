"""Build OpenAlgo symtoken rows from HF replay parquet (curated universe)."""

from __future__ import annotations

import hashlib
import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from trade_integrations.stock_simulator.hf_paths import hf_replay_root, options_dir

_MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")

UNDERLYING_META: dict[str, dict[str, Any]] = {
    "NIFTY": {
        "index_exchange": "NSE_INDEX",
        "options_exchange": "NFO",
        "brexchange_index": "NSE",
        "brexchange_options": "NSE",
        "lotsize": 25,
        "tick_size": 0.05,
    },
    "BANKNIFTY": {
        "index_exchange": "NSE_INDEX",
        "options_exchange": "NFO",
        "brexchange_index": "NSE",
        "brexchange_options": "NSE",
        "lotsize": 15,
        "tick_size": 0.05,
    },
    "SENSEX": {
        "index_exchange": "BSE_INDEX",
        "options_exchange": "BFO",
        "brexchange_index": "BSE",
        "brexchange_options": "BSE",
        "lotsize": 10,
        "tick_size": 0.05,
    },
}

_OPENALGO_OPTION_RE = re.compile(
    r"^([A-Z]+)(\d{2}(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\d{2})(\d+(?:\.\d+)?)(CE|PE)$",
    re.IGNORECASE,
)


def load_mc_underlyings() -> list[str]:
    raw = os.getenv("SIM_MC_UNDERLYINGS", "NIFTY,BANKNIFTY,SENSEX").strip()
    out = [u.strip().upper() for u in raw.split(",") if u.strip()]
    return [u for u in out if u in UNDERLYING_META] or ["NIFTY"]


def load_mc_max_expiries() -> int:
    try:
        return max(1, int(os.getenv("SIM_MC_MAX_EXPIRIES", "12") or "12"))
    except ValueError:
        return 12


def format_expiry_openalgo(expiry: date) -> str:
    return f"{expiry.day:02d}{_MONTHS[expiry.month - 1]}{expiry.year % 100:02d}"


def format_expiry_column(expiry: date) -> str:
    return expiry.strftime("%d-%b-%y").upper()


def openalgo_option_symbol(base: str, expiry: date, strike: float, opt_type: str) -> str:
    strike_s = str(int(strike)) if strike == int(strike) else f"{strike:g}"
    return f"{base.upper()}{format_expiry_openalgo(expiry)}{strike_s}{opt_type.upper()}"


def synthetic_token(symbol: str, exchange: str) -> str:
    digest = hashlib.sha256(f"{exchange}:{symbol.upper()}".encode()).hexdigest()[:12]
    return f"SIM{digest}"


def parse_openalgo_option_symbol(symbol: str) -> dict[str, Any] | None:
    match = _OPENALGO_OPTION_RE.match(symbol.strip().upper())
    if not match:
        return None
    base, expiry_code, strike_s, opt_type = match.groups()
    try:
        expiry = datetime.strptime(expiry_code, "%d%b%y").date()
    except ValueError:
        return None
    return {
        "base": base,
        "expiry": expiry,
        "expiry_code": expiry_code.upper(),
        "strike": float(strike_s),
        "option_type": opt_type.upper(),
    }


def build_symtoken_rows(*, data_root: Path, replay_date: str) -> list[dict[str, Any]]:
    """Build symtoken dict rows for configured underlyings at replay_date."""
    underlyings = load_mc_underlyings()
    max_expiries = load_mc_max_expiries()
    replay_day = replay_date[:10]
    rows: list[dict[str, Any]] = []

    for slug in underlyings:
        meta = UNDERLYING_META[slug]
        rows.append(_index_row(slug, meta))

        opt_rows = _option_rows_for_underlying(
            data_root=data_root,
            slug=slug,
            meta=meta,
            replay_day=replay_day,
            max_expiries=max_expiries,
        )
        rows.extend(opt_rows)

    return rows


def mc_cache_fingerprint(*, replay_date: str, underlyings: list[str] | None = None) -> dict[str, Any]:
    return {
        "replay_date": replay_date[:10],
        "underlyings": underlyings or load_mc_underlyings(),
        "max_expiries": load_mc_max_expiries(),
    }


def _index_row(slug: str, meta: dict[str, Any]) -> dict[str, Any]:
    exchange = meta["index_exchange"]
    symbol = slug
    return {
        "symbol": symbol,
        "brsymbol": symbol,
        "name": symbol,
        "exchange": exchange,
        "brexchange": meta["brexchange_index"],
        "token": synthetic_token(symbol, exchange),
        "expiry": "",
        "strike": 0.0,
        "lotsize": meta["lotsize"],
        "instrumenttype": "INDEX",
        "tick_size": meta["tick_size"],
    }


def _parse_expiry_stem(stem: str) -> date | None:
    try:
        return date.fromisoformat(stem[:10])
    except ValueError:
        return None


def _option_rows_for_underlying(
    *,
    data_root: Path,
    slug: str,
    meta: dict[str, Any],
    replay_day: str,
    max_expiries: int,
) -> list[dict[str, Any]]:
    opt_dir = options_dir(data_root, slug)
    if not opt_dir.is_dir():
        return []

    replay_d = date.fromisoformat(replay_day)
    files = sorted(opt_dir.glob("*.parquet"), key=lambda p: p.stem)
    candidates = [p for p in files if (exp := _parse_expiry_stem(p.stem)) is not None and exp >= replay_d]
    if not candidates:
        candidates = files[-max_expiries:]
    else:
        candidates = candidates[:max_expiries]

    exchange = meta["options_exchange"]
    lotsize = meta["lotsize"]
    tick = meta["tick_size"]
    rows: list[dict[str, Any]] = []

    for path in candidates:
        expiry = _parse_expiry_stem(path.stem)
        if expiry is None:
            continue
        legs = _strikes_on_day(path, replay_day)
        if not legs:
            continue
        expiry_col = format_expiry_column(expiry)
        for strike, opt_type in legs:
            sym = openalgo_option_symbol(slug, expiry, strike, opt_type)
            rows.append(
                {
                    "symbol": sym,
                    "brsymbol": sym,
                    "name": slug,
                    "exchange": exchange,
                    "brexchange": meta["brexchange_options"],
                    "token": synthetic_token(sym, exchange),
                    "expiry": expiry_col,
                    "strike": float(strike),
                    "lotsize": lotsize,
                    "instrumenttype": opt_type,
                    "tick_size": tick,
                }
            )
    return rows


def _strikes_on_day(path: Path, replay_day: str) -> set[tuple[float, str]]:
    if not path.is_file():
        return set()
    raw = pd.read_parquet(path, columns=["strike", "option_type", "trading_day"])
    if raw.empty:
        return set()
    day_frame = raw[raw["trading_day"].astype(str) == replay_day]
    if day_frame.empty:
        return set()
    out: set[tuple[float, str]] = set()
    for _, row in day_frame.drop_duplicates(subset=["strike", "option_type"]).iterrows():
        strike = float(row["strike"])
        opt_type = str(row["option_type"]).upper()
        if opt_type in {"CE", "PE"}:
            out.add((strike, opt_type))
    return out
