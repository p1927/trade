"""Bulk OpenAlgo / INDmoney daily OHLCV fetch and multi-tier persistence."""

from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

import pandas as pd

from trade_integrations.hub_storage.parquet_io import concat_dataframes, concat_frames

logger = logging.getLogger(__name__)

_CHUNK_DAYS = 365
_DEFAULT_SLEEP_S = 0.35
_MAX_YEARS = 10
_SOURCE = "openalgo_indmoney"
_INDEX_SYMBOLS = (
    "NIFTY",
    "BANKNIFTY",
    "FINNIFTY",
    "MIDCPNIFTY",
    "INDIAVIX",
    "SENSEX",
    "NIFTYIT",
)
_NIFTYINDICES_CSV: dict[str, str] = {
    "niftynext50_equity_list": "ind_niftynext50list.csv",
    "niftymidcap150_equity_list": "ind_niftymidcap150list.csv",
    "nifty50_equity_list": "ind_nifty50list.csv",
}


def _trade_root() -> Path:
    return Path(__file__).resolve().parents[3]


def openalgo_repo_dir(*, repo_root=None) -> Path:
    from trade_integrations.nse_browser.repository import repo_root as default_repo_root

    root = repo_root or default_repo_root()
    path = root / "historic_data" / "openalgo"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _entity_id(symbol: str) -> str:
    raw = symbol.strip().upper().replace(".NS", "").replace(".BO", "")
    aliases = {"^NSEI": "NIFTY", "NIFTY50": "NIFTY", "^INDIAVIX": "INDIAVIX"}
    return aliases.get(raw, raw)


def symbol_parquet_path(symbol: str, *, repo_root=None) -> Path:
    return openalgo_repo_dir(repo_root=repo_root) / f"{_entity_id(symbol)}_ohlcv_daily.parquet"


def _symbols_from_constituent_csv(path: Path) -> list[str]:
    if not path.is_file():
        return []
    try:
        frame = pd.read_csv(path)
    except Exception as exc:
        logger.debug("constituent csv read failed %s: %s", path, exc)
        return []
    if frame is None or frame.empty:
        return []
    symbol_col = next((c for c in frame.columns if str(c).strip().lower() == "symbol"), None)
    if symbol_col is None:
        symbol_col = frame.columns[2] if len(frame.columns) > 2 else frame.columns[0]
    return [str(v).strip().upper() for v in frame[symbol_col].tolist() if str(v).strip()]


def _hub_nifty100_symbols() -> list[str]:
    """Fallback Nifty 100 universe from ingested fundamentals panel."""
    path = _trade_root() / "reports" / "hub" / "_data" / "fundamentals" / "nifty100" / "symbol_map.json"
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict):
        return []
    return [str(k).strip().upper() for k in payload if str(k).strip()]


def _fetch_niftyindices_symbols(method_name: str) -> list[str]:
    """Load index constituents from local cache or niftyindices.com CSV."""
    csv_name = _NIFTYINDICES_CSV.get(method_name)
    if not csv_name:
        return []

    from trade_integrations.nse_browser.repository import repo_root

    local_candidates = [
        repo_root() / "historic_data" / csv_name,
        _trade_root() / "data" / "nse" / "historic_data" / csv_name,
    ]
    for path in local_candidates:
        symbols = _symbols_from_constituent_csv(path)
        if symbols:
            return symbols

    cache_path = _trade_root() / "reports" / "hub" / "_data" / "cache" / "niftyindices" / csv_name
    if cache_path.is_file():
        symbols = _symbols_from_constituent_csv(cache_path)
        if symbols:
            return symbols

    urls = [
        f"https://www.niftyindices.com/IndexConstituent/{csv_name}",
        f"https://archives.nseindia.com/content/indices/{csv_name}",
    ]
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    for url in urls:
        try:
            from trade_integrations.dataflows.throttled_http import fetch_to_path

            fetch_to_path(url, cache_path, force=False, timeout=8, max_retries=1)
            symbols = _symbols_from_constituent_csv(cache_path)
            if symbols:
                return symbols
        except Exception as exc:
            logger.debug("constituent fetch failed %s: %s", url, exc)

    return []


def _nselib_equity_symbols(method_name: str) -> list[str]:
    try:
        from nselib import capital_market
    except ImportError:
        logger.debug("nselib unavailable for %s", method_name)
        return _fetch_niftyindices_symbols(method_name)
    fn = getattr(capital_market, method_name, None)
    if fn is None:
        return _fetch_niftyindices_symbols(method_name)
    try:
        frame = fn()
    except Exception as exc:
        logger.debug("nselib %s failed: %s", method_name, exc)
        return _fetch_niftyindices_symbols(method_name)
    if frame is None or getattr(frame, "empty", True):
        return _fetch_niftyindices_symbols(method_name)
    symbol_col = next((c for c in frame.columns if str(c).strip().lower() == "symbol"), None)
    if symbol_col is None:
        symbol_col = frame.columns[0]
    return [str(v).strip().upper() for v in frame[symbol_col].tolist() if str(v).strip()]


def _dedupe_symbols(*groups: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for group in groups:
        for sym in group:
            raw = sym.strip().upper()
            if raw and raw not in seen:
                seen.add(raw)
                out.append(raw)
    return out


_SYMBOL_UNIVERSE_CACHE: dict[str, list[str]] = {}


def resolve_symbol_universe(bundle: str = "nifty50") -> list[str]:
    """Return deduplicated symbols for a named bundle."""
    bundle = bundle.strip().lower()
    cached = _SYMBOL_UNIVERSE_CACHE.get(bundle)
    if cached is not None:
        return cached

    indices = list(_INDEX_SYMBOLS)
    result: list[str]

    if bundle in ("indices", "index", "indices_extended"):
        result = indices
    elif bundle in ("nifty50", "n50", "default"):
        from trade_integrations.dataflows.index_research.constituents import load_nifty50_constituents

        equities = [row.symbol.strip().upper() for row in load_nifty50_constituents()]
        result = _dedupe_symbols(["NIFTY", "INDIAVIX", "BANKNIFTY"], equities)
    elif bundle in ("niftynext50", "next50"):
        from trade_integrations.dataflows.index_research.constituents import load_nifty50_constituents

        n50 = {row.symbol.strip().upper() for row in load_nifty50_constituents()}
        hub100 = _hub_nifty100_symbols()
        if hub100:
            diff = [s for s in hub100 if s not in n50]
            if diff:
                result = diff
            else:
                result = _nselib_equity_symbols("niftynext50_equity_list")
        else:
            result = _nselib_equity_symbols("niftynext50_equity_list")
    elif bundle in ("nifty100", "n100"):
        hub100 = _hub_nifty100_symbols()
        if hub100:
            result = _dedupe_symbols(["NIFTY", "INDIAVIX", "BANKNIFTY"], hub100)
        else:
            from trade_integrations.dataflows.index_research.constituents import load_nifty50_constituents

            n50 = [row.symbol.strip().upper() for row in load_nifty50_constituents()]
            next50 = _nselib_equity_symbols("niftynext50_equity_list")
            result = _dedupe_symbols(["NIFTY", "INDIAVIX", "BANKNIFTY"], n50, next50)
    elif bundle in ("niftymidcap150", "midcap150"):
        result = _nselib_equity_symbols("niftymidcap150_equity_list")
    elif bundle in ("all", "full"):
        from trade_integrations.dataflows.index_research.constituents import load_nifty50_constituents

        n50 = [row.symbol.strip().upper() for row in load_nifty50_constituents()]
        next50 = _nselib_equity_symbols("niftynext50_equity_list")
        mid150 = _nselib_equity_symbols("niftymidcap150_equity_list")
        result = _dedupe_symbols(indices, n50, next50, mid150)
    else:
        raise ValueError(f"unknown symbol bundle: {bundle}")

    _SYMBOL_UNIVERSE_CACHE[bundle] = result
    return result


def _read_parquet(path: Path) -> pd.DataFrame:
    if not path.is_file():
        return pd.DataFrame()
    try:
        return pd.read_parquet(path)
    except Exception:
        csv = path.with_suffix(".csv")
        if csv.is_file():
            return pd.read_csv(csv)
    return pd.DataFrame()


def _normalize_bars(frame: pd.DataFrame, *, symbol: str) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume", "symbol", "source"])

    working = frame.copy()
    if "timestamp" in working.columns and "date" not in working.columns and "Date" not in working.columns:
        working["Date"] = pd.to_datetime(working["timestamp"], unit="s", errors="coerce")

    from trade_integrations.dataflows.openalgo import to_index_research_frame

    normalized = to_index_research_frame(working)
    if normalized.empty:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume", "symbol", "source"])

    out = normalized.copy()
    out["date"] = out["date"].astype(str).str[:10]
    out["symbol"] = _entity_id(symbol)
    out["source"] = _SOURCE
    cols = ["date", "open", "high", "low", "close", "volume", "symbol", "source"]
    for col in cols:
        if col not in out.columns:
            out[col] = None
    return out[cols].dropna(subset=["date", "close"]).drop_duplicates("date", keep="last").sort_values("date")


def load_repo_bars(symbol: str, *, repo_root=None) -> pd.DataFrame:
    """Repo parquet only (used for gap detection — hub cache must not mask stale repo files)."""
    repo_path = symbol_parquet_path(symbol, repo_root=repo_root)
    return _normalize_bars(_read_parquet(repo_path), symbol=symbol)


def load_existing_bars(symbol: str, *, repo_root=None) -> pd.DataFrame:
    """Merge repo parquet and hub capture cache for one symbol."""
    repo_path = symbol_parquet_path(symbol, repo_root=repo_root)
    repo_frame = _normalize_bars(_read_parquet(repo_path), symbol=symbol)

    try:
        from trade_integrations.hub_capture.ohlcv_cache import read_cached_bars

        end = date.today().isoformat()
        start = (date.today() - timedelta(days=_MAX_YEARS * 366)).isoformat()
        cached, _ = read_cached_bars(symbol, start, end)
        cache_frame = _normalize_bars(cached, symbol=symbol)
    except Exception as exc:
        logger.debug("hub cache read failed for %s: %s", symbol, exc)
        cache_frame = pd.DataFrame()

    if repo_frame.empty:
        return cache_frame
    if cache_frame.empty:
        return repo_frame
    merged = concat_dataframes(repo_frame, cache_frame)
    return merged.drop_duplicates("date", keep="last").sort_values("date").reset_index(drop=True)


def _coverage_bounds(frame: pd.DataFrame) -> tuple[str | None, str | None]:
    if frame.empty or "date" not in frame.columns:
        return None, None
    dates = frame["date"].astype(str).str[:10]
    return str(dates.min()), str(dates.max())


def iter_date_chunks(start: date, end: date, *, chunk_days: int = _CHUNK_DAYS) -> Iterator[tuple[str, str]]:
    current = start
    while current <= end:
        chunk_end = min(current + timedelta(days=chunk_days - 1), end)
        yield current.isoformat(), chunk_end.isoformat()
        current = chunk_end + timedelta(days=1)


def _chunks_to_fetch(
    start: str,
    end: str,
    existing_min: str | None,
    existing_max: str | None,
    *,
    force: bool,
) -> list[tuple[str, str]]:
    start_d = date.fromisoformat(start[:10])
    end_d = date.fromisoformat(end[:10])
    chunks = list(iter_date_chunks(start_d, end_d))
    if force or existing_min is None or existing_max is None:
        return chunks
    return [(cs, ce) for cs, ce in chunks if ce < existing_min or cs > existing_max]


def _write_repo_parquet(symbol: str, frame: pd.DataFrame, *, repo_root=None) -> dict[str, Any]:
    path = symbol_parquet_path(symbol, repo_root=repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = _normalize_bars(frame, symbol=symbol)
    if normalized.empty:
        return {"status": "empty", "path": str(path)}

    existing = _normalize_bars(_read_parquet(path), symbol=symbol)
    if existing.empty:
        merged = normalized
    else:
        merged = concat_dataframes(existing, normalized)
        merged = merged.drop_duplicates("date", keep="last").sort_values("date")

    try:
        merged.to_parquet(path, index=False)
    except ImportError:
        merged.to_csv(path.with_suffix(".csv"), index=False)

    dmin, dmax = _coverage_bounds(merged)
    return {
        "status": "ok",
        "path": str(path),
        "rows": int(len(merged)),
        "start": dmin,
        "end": dmax,
    }


def _resolve_exchange(symbol: str) -> tuple[str, str]:
    from trade_integrations.openalgo.symbols import resolve_openalgo_symbol

    return resolve_openalgo_symbol(symbol)


def _fetch_history_rest(symbol: str, start_date: str, end_date: str, *, interval: str = "D") -> pd.DataFrame:
    from trade_integrations.openalgo.market_data import fetch_history_raw

    return fetch_history_raw(symbol, start_date, end_date, interval=interval)


def _fetch_history_subprocess(symbol: str, start_date: str, end_date: str, *, interval: str = "D") -> pd.DataFrame:
    """Fetch via OpenAlgo history_service in a subprocess (survives dead HTTP server)."""
    import json
    import os
    import subprocess

    oa_symbol, exchange = _resolve_exchange(symbol)
    openalgo_dir = _trade_root() / "openalgo"
    trade_env = _trade_root() / ".env"
    api_key = os.getenv("OPENALGO_API_KEY", "")
    if not api_key and trade_env.is_file():
        for line in trade_env.read_text(encoding="utf-8").splitlines():
            if line.startswith("OPENALGO_API_KEY="):
                api_key = line.split("=", 1)[1].strip().strip("'\"")
                break
    if not api_key:
        raise RuntimeError("OPENALGO_API_KEY not configured")

    script = f"""
import json, os
from dotenv import load_dotenv
load_dotenv({repr(str(trade_env))})
from services.history_service import get_history
ok, resp, code = get_history(
    {oa_symbol!r}, {exchange!r}, {interval!r}, {start_date!r}, {end_date!r},
    api_key={api_key!r},
)
print(json.dumps({{"ok": ok, "code": code, "data": resp.get("data") or [], "message": resp.get("message")}}))
"""
    proc = subprocess.run(
        ["uv", "run", "python", "-c", script],
        cwd=str(openalgo_dir),
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "subprocess history failed").strip()[:500])

    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    if not payload.get("ok"):
        raise RuntimeError(payload.get("message") or f"history HTTP {payload.get('code')}")

    rows = payload.get("data") or []
    if not rows:
        return pd.DataFrame()
    return _normalize_bars(pd.DataFrame(rows), symbol=symbol)


def fetch_broker_history(symbol: str, start_date: str, end_date: str, *, interval: str = "D") -> pd.DataFrame:
    """REST first; fall back to OpenAlgo subprocess when the HTTP tier is down."""
    try:
        return _normalize_bars(_fetch_history_rest(symbol, start_date, end_date, interval=interval), symbol=symbol)
    except Exception as rest_exc:
        logger.debug("OpenAlgo REST history failed for %s: %s", symbol, rest_exc)
        return _fetch_history_subprocess(symbol, start_date, end_date, interval=interval)


def _write_hub_cache(symbol: str, frame: pd.DataFrame) -> dict[str, Any]:
    from trade_integrations.hub_capture.ohlcv_cache import write_cached_bars

    normalized = _normalize_bars(frame, symbol=symbol)
    if normalized.empty:
        return {"status": "empty"}
    return write_cached_bars(
        symbol,
        normalized[["date", "open", "high", "low", "close", "volume"]],
        source=_SOURCE,
        vendor="openalgo",
    )


def fetch_symbol_history(
    symbol: str,
    start_date: str,
    end_date: str,
    *,
    interval: str = "D",
    sleep_s: float = _DEFAULT_SLEEP_S,
    force: bool = False,
    repo_root=None,
) -> dict[str, Any]:
    """Fetch missing OpenAlgo history and persist to repo + hub cache."""
    from trade_integrations.openalgo.market_data import openalgo_configured

    stats: dict[str, Any] = {
        "symbol": _entity_id(symbol),
        "start_date": start_date,
        "end_date": end_date,
        "chunks_fetched": 0,
        "chunks_failed": 0,
        "rows_fetched": 0,
        "status": "ok",
    }

    if not openalgo_configured():
        stats["status"] = "skipped"
        stats["reason"] = "openalgo_not_configured"
        return stats

    from trade_integrations.openalgo.symbols import _INDMONEY_UNAVAILABLE

    if _entity_id(symbol) in _INDMONEY_UNAVAILABLE:
        stats["status"] = "skipped"
        stats["reason"] = "indmoney_symbol_unavailable"
        return stats

    existing = load_repo_bars(symbol, repo_root=repo_root)
    existing_min, existing_max = _coverage_bounds(existing)
    stats["existing_rows"] = int(len(existing))
    stats["existing_range"] = [existing_min, existing_max]
    merged = load_existing_bars(symbol, repo_root=repo_root)
    stats["merged_rows"] = int(len(merged))
    stats["merged_range"] = list(_coverage_bounds(merged))

    if (
        not force
        and existing_min
        and existing_max
        and existing_min <= start_date[:10]
        and existing_max >= end_date[:10]
        and len(existing) >= max(200, (date.fromisoformat(end_date[:10]) - date.fromisoformat(start_date[:10])).days // 3)
    ):
        stats["status"] = "cached"
        stats["reason"] = "coverage_complete"
        return stats

    gaps: list[tuple[str, str]] = []
    if force or not existing_min or not existing_max:
        gaps.append((start_date[:10], end_date[:10]))
    else:
        if existing_min > start_date[:10]:
            gap_end = (date.fromisoformat(existing_min) - timedelta(days=1)).isoformat()
            gaps.append((start_date[:10], gap_end))
        if existing_max < end_date[:10]:
            gap_start = (date.fromisoformat(existing_max) + timedelta(days=1)).isoformat()
            gaps.append((gap_start, end_date[:10]))

    if not gaps:
        stats["status"] = "cached"
        stats["reason"] = "no_gaps"
        return stats

    collected: list[pd.DataFrame] = []
    errors: list[str] = []
    for gap_start, gap_end in gaps:
        if gap_start > gap_end:
            continue
        try:
            frame = fetch_broker_history(symbol, gap_start, gap_end, interval=interval)
            if not frame.empty:
                collected.append(frame)
                stats["rows_fetched"] += int(len(frame))
            stats["chunks_fetched"] += 1
        except Exception as exc:
            stats["chunks_failed"] += 1
            errors.append(f"{gap_start}..{gap_end}: {exc}")
            logger.warning("OpenAlgo history failed for %s [%s..%s]: %s", symbol, gap_start, gap_end, exc)
        finally:
            time.sleep(sleep_s)

    if errors:
        stats["errors"] = errors
        if not collected:
            stats["status"] = "error"
            stats["error"] = errors[-1]
            return stats
        stats["status"] = "partial"

    if not collected:
        stats["status"] = "empty"
        return stats

    merged_fetch = concat_frames(collected).drop_duplicates("date", keep="last").sort_values("date")
    stats["repo"] = _write_repo_parquet(symbol, merged_fetch, repo_root=repo_root)
    stats["hub"] = _write_hub_cache(symbol, merged_fetch)
    final = load_existing_bars(symbol, repo_root=repo_root)
    dmin, dmax = _coverage_bounds(final)
    stats["final_rows"] = int(len(final))
    stats["final_range"] = [dmin, dmax]
    return stats


def import_historify_duckdb(
    *,
    duckdb_path: Path | None = None,
    interval: str = "D",
    repo_root=None,
) -> dict[str, Any]:
    """Import daily bars from OpenAlgo historify.duckdb into Trade repo + hub cache."""
    db_path = duckdb_path or (_trade_root() / "openalgo" / "db" / "historify.duckdb")
    stats: dict[str, Any] = {"path": str(db_path), "symbols": 0, "rows": 0, "status": "ok"}

    if not db_path.is_file():
        stats["status"] = "skipped"
        stats["reason"] = "missing_duckdb"
        return stats

    try:
        import duckdb
    except ImportError:
        stats["status"] = "skipped"
        stats["reason"] = "duckdb_not_installed"
        return stats

    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        rows = conn.execute(
            """
            SELECT symbol, exchange, timestamp, open, high, low, close, volume
            FROM market_data
            WHERE interval = ?
            ORDER BY symbol, timestamp
            """,
            [interval],
        ).fetchdf()
    finally:
        conn.close()

    if rows is None or rows.empty:
        stats["status"] = "empty"
        return stats

    rows = rows.copy()
    rows["date"] = pd.to_datetime(rows["timestamp"], unit="s", errors="coerce").dt.strftime("%Y-%m-%d")
    rows = rows.dropna(subset=["date", "close"])

    per_symbol: dict[str, Any] = {}
    for sym, group in rows.groupby("symbol"):
        sym_str = str(sym).strip().upper()
        normalized = _normalize_bars(group, symbol=sym_str)
        if normalized.empty:
            continue
        per_symbol[sym_str] = _write_repo_parquet(sym_str, normalized, repo_root=repo_root)
        _write_hub_cache(sym_str, normalized)
        stats["rows"] += int(len(normalized))

    stats["symbols"] = len(per_symbol)
    stats["by_symbol"] = per_symbol
    return stats


def build_constituent_panel(*, repo_root=None, index_slug: str = "nifty50") -> pd.DataFrame:
    """Long-format panel from per-symbol OpenAlgo repo parquets."""
    symbols = resolve_symbol_universe(index_slug)
    frames: list[pd.DataFrame] = []
    for sym in symbols:
        path = symbol_parquet_path(sym, repo_root=repo_root)
        frame = _normalize_bars(_read_parquet(path), symbol=sym)
        if not frame.empty:
            frames.append(frame)
    if not frames:
        return pd.DataFrame(columns=["date", "symbol", "open", "high", "low", "close", "volume", "source"])
    panel = concat_frames(frames)
    return panel.drop_duplicates(["date", "symbol"], keep="last").sort_values(["symbol", "date"]).reset_index(drop=True)


def sync_openalgo_panel_to_cold_tier(*, repo_root=None, index_slug: str = "nifty50") -> dict[str, Any]:
    """Merge OpenAlgo constituent panel into cold-tier history store."""
    from trade_integrations.dataflows.index_research.history_store import load_history_dataset, save_history_dataset

    panel = build_constituent_panel(repo_root=repo_root, index_slug=index_slug)
    if panel.empty:
        return {"status": "skipped", "reason": "empty_panel"}

    dataset = f"{index_slug}_constituent_ohlcv_daily"
    existing = load_history_dataset(dataset)
    if existing.empty:
        merged = panel
    else:
        merged = concat_dataframes(existing, panel)
        merged["date"] = merged["date"].astype(str).str[:10]
        merged = merged.drop_duplicates(["date", "symbol"], keep="last").sort_values(["symbol", "date"])

    return save_history_dataset(dataset, merged)


def persist_openalgo_bulk(
    *,
    bundle: str = "nifty50",
    years: int = _MAX_YEARS,
    end_date: str | None = None,
    interval: str = "D",
    sleep_s: float = _DEFAULT_SLEEP_S,
    force: bool = False,
    import_historify: bool = True,
    sync_cold_tier: bool = True,
    repo_root=None,
    symbols: list[str] | None = None,
) -> dict[str, Any]:
    """Bulk-fetch OpenAlgo daily OHLCV for a symbol universe and persist all tiers."""
    from trade_integrations.stock_simulator.integration import hub_no_learn

    if hub_no_learn():
        return {"status": "skipped", "reason": "hub_no_learn"}
    end = (end_date or date.today().isoformat())[:10]
    start = (date.fromisoformat(end) - timedelta(days=min(max(years, 1), _MAX_YEARS) * 366)).isoformat()
    universe = symbols or resolve_symbol_universe(bundle)

    report: dict[str, Any] = {
        "bundle": bundle,
        "symbols": len(universe),
        "start_date": start,
        "end_date": end,
        "interval": interval,
        "started_at": datetime.utcnow().isoformat() + "Z",
        "historify": {},
        "symbols_stats": {},
        "summary": {"ok": 0, "cached": 0, "error": 0, "empty": 0, "skipped": 0},
    }

    if import_historify:
        report["historify"] = import_historify_duckdb(interval=interval, repo_root=repo_root)

    try:
        import subprocess

        sync = subprocess.run(
            ["uv", "run", "python", "-c", "from utils.broker_env_sync import sync_env_secret_to_auth_db; import json; print(json.dumps(sync_env_secret_to_auth_db()))"],
            cwd=str(_trade_root() / "openalgo"),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        report["token_sync"] = (sync.stdout or sync.stderr or "").strip()[:500]
    except Exception as exc:
        report["token_sync"] = f"skipped: {exc}"

    for sym in universe:
        try:
            result = fetch_symbol_history(
                sym,
                start,
                end,
                interval=interval,
                sleep_s=sleep_s,
                force=force,
                repo_root=repo_root,
            )
        except Exception as exc:
            result = {"symbol": sym, "status": "error", "error": str(exc)}
            logger.exception("bulk fetch failed for %s", sym)

        status = str(result.get("status") or "error")
        bucket = "ok" if status in ("ok", "cached") else status
        if bucket in report["summary"]:
            report["summary"][bucket] += 1
        else:
            report["summary"]["error"] += 1
        report["symbols_stats"][_entity_id(sym)] = result

    if sync_cold_tier:
        slug_map = {
            "nifty50": "nifty50",
            "nifty100": "nifty100",
            "niftynext50": "nifty100",
            "indices": "nifty50",
            "indices_extended": "nifty50",
        }
        report["cold_tier"] = sync_openalgo_panel_to_cold_tier(
            repo_root=repo_root,
            index_slug=slug_map.get(bundle.strip().lower(), "nifty50"),
        )

    report["finished_at"] = datetime.utcnow().isoformat() + "Z"
    manifest_path = openalgo_repo_dir(repo_root=repo_root) / f"bulk_manifest_{bundle}.json"
    manifest_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return report


def persist_all_openalgo_bundles(
    *,
    bundles: list[str] | None = None,
    years: int = _MAX_YEARS,
    end_date: str | None = None,
    sleep_s: float = _DEFAULT_SLEEP_S,
    force: bool = False,
    sync_cold_tier: bool = True,
) -> dict[str, Any]:
    """Run bulk ingest for multiple symbol bundles sequentially."""
    run_bundles = bundles or ["indices_extended", "nifty50", "nifty100"]
    reports: dict[str, Any] = {"bundles": {}, "started_at": datetime.utcnow().isoformat() + "Z"}
    cold_slugs: dict[str, str] = {}
    if "nifty50" in run_bundles:
        cold_slugs["nifty50"] = "nifty50"
    if any(b in run_bundles for b in ("nifty100", "niftynext50")):
        cold_slugs["nifty100"] = "nifty100"

    try:
        import subprocess

        sync = subprocess.run(
            [
                "uv",
                "run",
                "python",
                "-c",
                "from utils.broker_env_sync import sync_env_secret_to_auth_db; import json; print(json.dumps(sync_env_secret_to_auth_db()))",
            ],
            cwd=str(_trade_root() / "openalgo"),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        reports["token_sync"] = (sync.stdout or sync.stderr or "").strip()[:500]
    except Exception as exc:
        reports["token_sync"] = f"skipped: {exc}"

    reports["historify"] = import_historify_duckdb()

    for bundle in run_bundles:
        reports["bundles"][bundle] = persist_openalgo_bulk(
            bundle=bundle,
            years=years,
            end_date=end_date,
            sleep_s=sleep_s,
            force=force,
            import_historify=False,
            sync_cold_tier=False,
        )

    if sync_cold_tier:
        reports["cold_tier"] = {}
        for slug in cold_slugs.values():
            reports["cold_tier"][slug] = sync_openalgo_panel_to_cold_tier(index_slug=slug)

    reports["finished_at"] = datetime.utcnow().isoformat() + "Z"
    out_path = openalgo_repo_dir() / "bulk_manifest_all.json"
    out_path.write_text(json.dumps(reports, indent=2, default=str), encoding="utf-8")
    return reports
