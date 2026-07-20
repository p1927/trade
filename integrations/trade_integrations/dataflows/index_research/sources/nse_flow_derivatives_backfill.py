"""Historical FII/DII cash flows and derivatives positioning for factor backfill."""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta
from io import StringIO
from pathlib import Path

import pandas as pd

from trade_integrations.context.hub import get_hub_dir

logger = logging.getLogger(__name__)

_FLOW_CACHE_FILENAME = "flow_cash_daily.parquet"
_FAO_ARCHIVE_BASES = (
    "https://nsearchives.nseindia.com/content/nsccl/fao_participant_oi_{date}.csv",
    "https://nsearchives.nseindia.com/content/nsccl/fao_participant_oi_{date}_b.csv",
    "https://archives.nseindia.com/content/nsccl/fao_participant_oi_{date}.csv",
)

_FII_LIVE_URL = "https://fii-diidata.mrchartist.com/api/data"
_FII_HISTORY_URL = "https://fii-diidata.mrchartist.com/api/history-full"
_SEEDED_SOURCES = frozenset({"seeded", "estimate", "estimated", "synthetic"})

_FLOW_COLS = ("fii_net", "dii_net")
_DERIV_COLS = (
    "nifty_pcr",
    "fii_sentiment_score",
    "fii_idx_fut_long",
    "fii_idx_fut_short",
    "fii_idx_put_oi",
    "fii_idx_call_oi",
    "fii_fut_long_short_ratio",
)


def _parse_api_date(raw: str) -> str | None:
    for fmt in ("%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(raw).strip()[:11], fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _float_or_none(raw) -> float | None:
    if raw is None:
        return None
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None
    if pd.isna(val):
        return None
    return val


def _is_seeded_row(item: dict) -> bool:
    source = str(item.get("_source") or item.get("source") or "").strip().lower()
    if source in _SEEDED_SOURCES:
        return True
    if source and source != "fetch-pipeline":
        return "seed" in source or "estimat" in source
    return False


def fetch_mrchartist_flow_frame(
    *,
    include_seeded: bool = False,
    allow_live_fetch: bool = True,
    local_path: Path | None = None,
) -> pd.DataFrame:
    """Load FII/DII/PCR/F&O OI from local history-full JSON or Mr. Chartist API."""
    if local_path is None:
        try:
            from trade_integrations.nse_browser.repository import repo_root
            from trade_integrations.nse_browser.parsers.historic_data import (
                local_mrchartist_history_path,
                parse_mrchartist_history_json,
            )

            candidate = local_mrchartist_history_path(repo_root())
            if candidate.is_file():
                local_frame = parse_mrchartist_history_json(candidate)
                if not local_frame.empty:
                    return local_frame
        except Exception:
            pass
    elif local_path.is_file():
        try:
            from trade_integrations.nse_browser.parsers.historic_data import parse_mrchartist_history_json

            local_frame = parse_mrchartist_history_json(local_path)
            if not local_frame.empty:
                return local_frame
        except Exception:
            pass

    if not allow_live_fetch:
        return pd.DataFrame()
    try:
        import requests

        response = requests.get(_FII_HISTORY_URL, timeout=45)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        logger.debug("Mr. Chartist history-full unavailable: %s", exc)
        return pd.DataFrame()

    if not isinstance(payload, list) or not payload:
        return pd.DataFrame()

    rows: list[dict] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        if not include_seeded and _is_seeded_row(item):
            continue
        day = _parse_api_date(str(item.get("d") or item.get("date") or ""))
        if not day:
            continue
        row: dict = {"date": day, "source": str(item.get("_source") or "mrchartist")}
        for src, dest in (
            ("fn", "fii_net"),
            ("fii_net", "fii_net"),
            ("dn", "dii_net"),
            ("dii_net", "dii_net"),
            ("pcr", "nifty_pcr"),
            ("sentiment_score", "fii_sentiment_score"),
            ("fii_idx_fut_long", "fii_idx_fut_long"),
            ("fii_idx_fut_short", "fii_idx_fut_short"),
            ("fii_idx_opt_put_short", "fii_idx_put_oi"),
            ("fii_idx_opt_call_short", "fii_idx_call_oi"),
        ):
            val = _float_or_none(item.get(src))
            if val is not None:
                row[dest] = val
        rows.append(row)

    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows).sort_values("date").drop_duplicates("date", keep="last")
    long_oi = frame.get("fii_idx_fut_long")
    short_oi = frame.get("fii_idx_fut_short")
    if long_oi is not None and short_oi is not None:
        frame["fii_fut_long_short_ratio"] = long_oi / short_oi.replace(0, pd.NA)
    if "fii_idx_put_oi" in frame.columns and "fii_idx_call_oi" in frame.columns:
        frame["nifty_pcr"] = frame["nifty_pcr"].combine_first(
            frame["fii_idx_put_oi"] / frame["fii_idx_call_oi"].replace(0, pd.NA)
        )
    return frame


def fetch_mrchartist_latest_session(*, allow_live_fetch: bool = True) -> pd.DataFrame:
    """Latest FII/DII + derivatives from Mr. Chartist (NSE-sourced, post-close)."""
    if not allow_live_fetch:
        return pd.DataFrame()
    try:
        import requests

        response = requests.get(_FII_LIVE_URL, timeout=20)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        logger.debug("Mr. Chartist /api/data unavailable: %s", exc)
        return pd.DataFrame()

    if not isinstance(payload, dict):
        return pd.DataFrame()

    day = _parse_api_date(str(payload.get("d") or payload.get("date") or ""))
    if not day:
        day = datetime.now().date().isoformat()

    row: dict = {"date": day, "source": str(payload.get("_source") or "mrchartist_live")}
    for src, dest in (
        ("fn", "fii_net"),
        ("fii_net", "fii_net"),
        ("dn", "dii_net"),
        ("dii_net", "dii_net"),
        ("pcr", "nifty_pcr"),
        ("sentiment_score", "fii_sentiment_score"),
        ("fii_idx_fut_long", "fii_idx_fut_long"),
        ("fii_idx_fut_short", "fii_idx_fut_short"),
        ("fii_idx_opt_put_short", "fii_idx_put_oi"),
        ("fii_idx_opt_call_short", "fii_idx_call_oi"),
    ):
        val = _float_or_none(payload.get(src))
        if val is not None:
            row[dest] = val
    if row.get("fii_idx_fut_long") is not None and row.get("fii_idx_fut_short"):
        row["fii_fut_long_short_ratio"] = row["fii_idx_fut_long"] / max(row["fii_idx_fut_short"], 1e-9)
    return pd.DataFrame([row])


def _fii_net_column(frame: pd.DataFrame) -> str | None:
    for column in frame.columns:
        label = str(column).lower()
        if "fii" in label and "net" in label:
            return column
    return None


def _dii_net_column(frame: pd.DataFrame) -> str | None:
    for column in frame.columns:
        label = str(column).lower()
        if "dii" in label and "net" in label:
            return column
    return None


def _date_column(frame: pd.DataFrame) -> str | None:
    for column in frame.columns:
        label = str(column).lower()
        if "date" in label or label in {"tradedate", "trade date"}:
            return column
    return None


def fetch_nselib_fii_dii_frame(
    start: str,
    end: str,
    *,
    allow_live_fetch: bool = True,
) -> pd.DataFrame:
    """Fetch latest FII/DII cash from NSE public API (same-day; historical via Mr. Chartist)."""
    if not allow_live_fetch:
        return pd.DataFrame()
    try:
        import requests
    except ImportError:
        return pd.DataFrame()

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
            "Referer": "https://www.nseindia.com/reports/fii-dii",
        }
    )
    try:
        session.get("https://www.nseindia.com", timeout=15)
        response = session.get("https://www.nseindia.com/api/fiidiiTradeReact", timeout=15)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        logger.debug("NSE fiidiiTradeReact unavailable: %s", exc)
        return pd.DataFrame()

    if not isinstance(payload, list):
        return pd.DataFrame()

    rows: list[dict] = []
    day = end[:10]
    fii_net = None
    dii_net = None
    for item in payload:
        if not isinstance(item, dict):
            continue
        cat = str(item.get("category") or "").upper()
        net = _float_or_none(item.get("netValue"))
        raw_date = str(item.get("date") or "")
        parsed = _parse_api_date(raw_date)
        if parsed:
            day = parsed
        if cat == "FII" and net is not None:
            fii_net = net
        if cat == "DII" and net is not None:
            dii_net = net
    if fii_net is None and dii_net is None:
        return pd.DataFrame()
    row: dict = {"date": day, "source": "nse_fiidii_react"}
    if fii_net is not None:
        row["fii_net"] = fii_net
    if dii_net is not None:
        row["dii_net"] = dii_net
    rows.append(row)
    frame = pd.DataFrame(rows)
    return frame[(frame["date"] >= start[:10]) & (frame["date"] <= end[:10])]


def _row_dict(frame: pd.DataFrame, day: str) -> dict:
    hits = frame[frame["date"].astype(str) == day[:10]]
    if hits.empty:
        return {}
    return hits.iloc[-1].to_dict()


def get_flow_cash_cache_path() -> Path:
    """Persistent merged FII/DII cash + derivatives rows (real sources only)."""
    return get_hub_dir() / "_data/index_factors" / _FLOW_CACHE_FILENAME


def load_flow_cash_cache() -> pd.DataFrame:
    """Load cached daily flow rows keyed by ``date``."""
    path = get_flow_cash_cache_path()
    csv_path = path.with_suffix(".csv")
    if path.is_file():
        try:
            frame = pd.read_parquet(path)
        except Exception:
            frame = pd.read_csv(csv_path) if csv_path.is_file() else pd.DataFrame()
    elif csv_path.is_file():
        frame = pd.read_csv(csv_path)
    else:
        return pd.DataFrame()
    if frame.empty or "date" not in frame.columns:
        return pd.DataFrame()
    out = frame.copy()
    out["date"] = out["date"].astype(str).str[:10]
    return out.sort_values("date").drop_duplicates("date", keep="last")


def upsert_flow_cash_cache(rows: list[dict]) -> int:
    """Merge new daily flow rows into the hub cache (by date, patch non-null fields)."""
    if not rows:
        return 0
    existing = load_flow_cash_cache()
    incoming = pd.DataFrame(rows)
    incoming["date"] = incoming["date"].astype(str).str[:10]
    if existing.empty:
        merged = incoming
    else:
        left = existing.copy()
        left["date"] = left["date"].astype(str).str[:10]
        right = incoming.set_index("date")
        merged = left.set_index("date")
        for day, row in right.iterrows():
            if day not in merged.index:
                merged.loc[day] = row
                continue
            for col, val in row.items():
                if pd.notna(val):
                    merged.at[day, col] = val
        merged = merged.reset_index()
    merged = merged.sort_values("date").drop_duplicates("date", keep="last")
    path = get_flow_cash_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        merged.to_parquet(path, index=False)
    except ImportError:
        merged.to_csv(path.with_suffix(".csv"), index=False)
        return len(incoming)
    merged.to_csv(path.with_suffix(".csv"), index=False)
    return len(incoming)


def _nse_session():
    try:
        import requests
    except ImportError:
        return None
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0",
            "Accept": "*/*",
            "Referer": "https://www.nseindia.com/reports/fii-dii",
        }
    )
    try:
        session.get("https://www.nseindia.com", timeout=15)
    except Exception as exc:
        logger.debug("NSE session bootstrap failed: %s", exc)
        return None
    return session


def _parse_fao_participant_csv(csv_text: str) -> dict[str, dict[str, float]]:
    """Parse NSE F&O participant OI CSV into FII/DII positioning dicts."""
    if not csv_text or len(csv_text) < 80:
        return {}
    lines = [line for line in csv_text.strip().splitlines() if line.strip()]
    header_idx = None
    for idx, line in enumerate(lines):
        lowered = line.lower()
        if "client type" in lowered and "future index long" in lowered:
            header_idx = idx
            break
    if header_idx is None:
        return {}
    try:
        frame = pd.read_csv(StringIO("\n".join(lines[header_idx:])), skipinitialspace=True)
    except Exception as exc:
        logger.debug("FAO CSV parse failed: %s", exc)
        return {}

    out: dict[str, dict[str, float]] = {}
    for _, row in frame.iterrows():
        client = str(row.get("Client Type") or row.get("Client") or "").strip().upper()
        if not client:
            continue
        key = None
        if "FII" in client or "FOREIGN" in client:
            key = "FII"
        elif "DII" in client or "MUTUAL" in client or "DOMESTIC" in client:
            key = "DII"
        if key is None:
            continue

        def _num(field: str) -> float:
            raw = row.get(field)
            if raw is None or (isinstance(raw, float) and pd.isna(raw)):
                return 0.0
            try:
                return float(str(raw).replace(",", ""))
            except (TypeError, ValueError):
                return 0.0

        out[key] = {
            "fii_idx_fut_long": _num("Future Index Long"),
            "fii_idx_fut_short": _num("Future Index Short"),
            "fii_idx_put_oi": _num("Option Index Put Short"),
            "fii_idx_call_oi": _num("Option Index Call Short"),
        }
    return out


def fetch_nse_fao_participant_oi_for_date(day: str) -> pd.DataFrame:
    """Historical F&O participant OI for one session (NSE archives)."""
    session = _nse_session()
    if session is None:
        return pd.DataFrame()
    try:
        parsed = datetime.strptime(day[:10], "%Y-%m-%d")
    except ValueError:
        return pd.DataFrame()
    date_key = parsed.strftime("%d%m%Y")
    csv_text = None
    for template in _FAO_ARCHIVE_BASES:
        url = template.format(date=date_key)
        try:
            response = session.get(url, timeout=15)
            if response.status_code == 200 and len(response.content) > 100:
                text = response.text
                if "html" not in text[:80].lower():
                    csv_text = text
                    break
        except Exception as exc:
            logger.debug("FAO archive miss %s: %s", url, exc)
    if not csv_text:
        return pd.DataFrame()

    parsed_rows = _parse_fao_participant_csv(csv_text)
    if not parsed_rows:
        return pd.DataFrame()

    row: dict = {"date": day[:10], "source": "nse_fao_archive"}
    fii = parsed_rows.get("FII") or {}
    for key, val in fii.items():
        row[key] = val
    if row.get("fii_idx_fut_long") and row.get("fii_idx_fut_short"):
        row["fii_fut_long_short_ratio"] = float(row["fii_idx_fut_long"]) / max(
            float(row["fii_idx_fut_short"]), 1e-9
        )
    put_oi = row.get("fii_idx_put_oi")
    call_oi = row.get("fii_idx_call_oi")
    if put_oi and call_oi:
        row["nifty_pcr"] = float(put_oi) / max(float(call_oi), 1e-9)
    return pd.DataFrame([row])


def fetch_nse_fao_history_frame(
    trading_dates: list[str],
    *,
    sleep_s: float = 0.35,
    max_days: int | None = None,
) -> pd.DataFrame:
    """Backfill F&O participant OI from NSE archives for trading dates."""
    rows: list[dict] = []
    targets = trading_dates if max_days is None else trading_dates[-max_days:]
    for idx, day in enumerate(targets):
        frame = fetch_nse_fao_participant_oi_for_date(day)
        if not frame.empty:
            rows.append(frame.iloc[0].to_dict())
        if sleep_s > 0 and idx < len(targets) - 1:
            time.sleep(sleep_s)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values("date").drop_duplicates("date", keep="last")


def flow_coverage_gaps_by_month(frame: pd.DataFrame) -> list[dict[str, int | str]]:
    """Monthly gap report for ``fii_net`` / ``dii_net`` in a merged flow frame."""
    if frame.empty or "date" not in frame.columns:
        return []
    work = frame.copy()
    work["date"] = work["date"].astype(str).str[:10]
    work["month"] = work["date"].str.slice(0, 7)
    rows: list[dict[str, int | str]] = []
    for month, group in work.groupby("month", sort=True):
        total = len(group)
        fii_days = int(group["fii_net"].notna().sum()) if "fii_net" in group.columns else 0
        dii_days = int(group["dii_net"].notna().sum()) if "dii_net" in group.columns else 0
        rows.append(
            {
                "month": str(month),
                "trading_days": total,
                "fii_net_days": fii_days,
                "dii_net_days": dii_days,
                "fii_gap_days": total - fii_days,
                "dii_gap_days": total - dii_days,
            }
        )
    return rows


def flow_effective_start(frame: pd.DataFrame) -> str | None:
    """First date with real FII or DII cash net in merged flow frame."""
    if frame.empty:
        return None
    for col in ("fii_net", "dii_net"):
        if col not in frame.columns:
            continue
        hits = frame[frame[col].notna()]
        if not hits.empty:
            return str(hits["date"].astype(str).iloc[0])[:10]
    return None


def load_nse_browser_fii_dii_frame(start: str, end: str) -> pd.DataFrame:
    """Load FII/DII daily rows persisted by the nse_browser module."""
    try:
        from trade_integrations.nse_browser.hub_writer import load_fii_dii_daily
    except ImportError:
        return pd.DataFrame()
    frame = load_fii_dii_daily()
    if frame.empty or "date" not in frame.columns:
        return pd.DataFrame()
    out = frame.copy()
    out["date"] = out["date"].astype(str).str[:10]
    out = out[(out["date"] >= start[:10]) & (out["date"] <= end[:10])]
    if "granularity" in out.columns:
        out = out[out["granularity"].astype(str) != "monthly"]
    if not out.empty and "source" not in out.columns:
        out["source"] = "nse_browser"
    return out.reset_index(drop=True)


def fetch_web_flow_cash_frame(
    start: str,
    end: str,
    *,
    allow_live_fetch: bool = True,
) -> pd.DataFrame:
    """Load FII/DII cash rows from Nifty Invest API cache + saved HTML snapshots."""
    frames: list[pd.DataFrame] = []
    if allow_live_fetch:
        try:
            from trade_integrations.dataflows.index_research.sources.web_flow_fetch import (
                fetch_niftyinvest_flow_frame,
            )

            api_frame = fetch_niftyinvest_flow_frame(start=start, end=end, allow_live_fetch=True)
            if not api_frame.empty:
                frames.append(api_frame)
        except ImportError:
            pass
    try:
        from trade_integrations.nse_browser.missions.web_flow_history import load_web_flow_from_raw_cache

        cached = load_web_flow_from_raw_cache()
        if not cached.empty and "date" in cached.columns:
            out = cached.copy()
            out["date"] = out["date"].astype(str).str[:10]
            out = out[(out["date"] >= start[:10]) & (out["date"] <= end[:10])]
            if "granularity" in out.columns:
                out = out[out["granularity"].astype(str) != "monthly"]
            if not out.empty:
                frames.append(out)
    except ImportError:
        pass
    if not frames:
        return pd.DataFrame()
    combined = pd.concat(frames, ignore_index=True)
    combined = combined.sort_values("date").drop_duplicates("date", keep="last")
    return combined.reset_index(drop=True)


def merge_flow_derivatives_frame(
    start: str,
    end: str,
    *,
    allow_live_fetch: bool = True,
) -> pd.DataFrame:
    """Merge web scrape, Mr. Chartist, nse repo/hub, NSE today, flow cache, and FAO archives."""
    try:
        from trade_integrations.nse_browser.repository import load_nse_repository_fii_dii_frame

        repo_flow = load_nse_repository_fii_dii_frame(start, end)
    except ImportError:
        repo_flow = pd.DataFrame()

    browser_flow = load_nse_browser_fii_dii_frame(start, end)
    web_flow = fetch_web_flow_cash_frame(start, end, allow_live_fetch=allow_live_fetch)
    mr = fetch_mrchartist_flow_frame(include_seeded=False, allow_live_fetch=allow_live_fetch)
    latest = fetch_mrchartist_latest_session(allow_live_fetch=allow_live_fetch)
    nse = fetch_nselib_fii_dii_frame(start, end, allow_live_fetch=allow_live_fetch)
    cache = load_flow_cash_cache()
    fo_bhav = load_fo_bhavcopy_derivatives_frame(start=start, end=end)
    oi_daily = load_nifty_oi_daily_frame(start=start, end=end)
    cold_deriv = pd.DataFrame()
    try:
        from trade_integrations.dataflows.index_research.history_store import load_history_dataset

        cold_deriv = load_history_dataset("flow_derivatives_daily")
        if not cold_deriv.empty:
            cold_deriv = cold_deriv.copy()
            cold_deriv["date"] = cold_deriv["date"].astype(str).str[:10]
            cold_deriv = cold_deriv[(cold_deriv["date"] >= start[:10]) & (cold_deriv["date"] <= end[:10])]
    except Exception:
        cold_deriv = pd.DataFrame()

    # Per-date last wins (listed low → high priority): FO bhavcopy, OI daily, flow cache,
    # web snapshots, Mr. Chartist, NSE today, git repo parquet, nse_browser hub rows, cold-tier deriv.
    frames = [
        f
        for f in (
            fo_bhav,
            oi_daily,
            cache,
            web_flow,
            mr,
            latest,
            nse,
            repo_flow,
            browser_flow,
            cold_deriv,
        )
        if f is not None and not f.empty
    ]
    if not frames:
        return pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True)
    combined = combined.sort_values("date").drop_duplicates("date", keep="last")

    dates = set(combined["date"].astype(str).tolist())
    rows: list[dict] = []
    for day in sorted(dates):
        if day < start[:10] or day > end[:10]:
            continue
        day_rows = combined[combined["date"].astype(str) == day[:10]]
        if day_rows.empty:
            continue
        merged = day_rows.iloc[-1].to_dict()
        merged["date"] = day[:10]
        rows.append(merged)

    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows).sort_values("date").drop_duplicates("date", keep="last")
    if "fii_idx_fut_long" in frame.columns and "fii_idx_fut_short" in frame.columns:
        frame["fii_fut_long_short_ratio"] = frame["fii_fut_long_short_ratio"].combine_first(
            frame["fii_idx_fut_long"] / frame["fii_idx_fut_short"].replace(0, pd.NA)
        )
    return frame.reset_index(drop=True)


def build_rolling_sum_series(
    frame: pd.DataFrame,
    column: str,
    trading_dates: list[str],
    *,
    window: int = 5,
) -> pd.Series:
    """Align a daily column to Nifty trading dates with rolling sum."""
    if frame.empty or column not in frame.columns:
        return pd.Series(dtype=float)

    daily = pd.Series(frame[column].astype(float).values, index=frame["date"].astype(str))
    daily.index = pd.to_datetime(daily.index)
    rolling = daily.sort_index().rolling(window, min_periods=1).sum()
    out: dict[str, float] = {}
    for day in trading_dates:
        ts = pd.Timestamp(day)
        eligible = rolling.index[rolling.index <= ts]
        if len(eligible) == 0:
            continue
        val = rolling.loc[eligible[-1]]
        if not pd.isna(val):
            out[day] = float(val)
    return pd.Series(out)


def backfill_nse_fao_to_cache(
    trading_dates: list[str],
    *,
    sleep_s: float = 0.35,
    max_fetch: int | None = 120,
) -> dict[str, int | str]:
    """Fetch missing F&O archive rows and upsert into flow cache."""
    cache = load_flow_cash_cache()
    cached_dates = set(cache["date"].astype(str).tolist()) if not cache.empty else set()
    missing = [d for d in trading_dates if d not in cached_dates]
    if max_fetch is not None and len(missing) > max_fetch:
        missing = missing[-max_fetch:]
    fetched = fetch_nse_fao_history_frame(missing, sleep_s=sleep_s)
    if fetched.empty:
        return {"status": "ok", "fetched": 0, "cached_total": len(cache)}
    rows = fetched.to_dict("records")
    upsert_flow_cash_cache(rows)
    return {
        "status": "ok",
        "fetched": len(rows),
        "cached_total": len(load_flow_cash_cache()),
    }


def _fao_backfill_progress_path() -> Path:
    return get_hub_dir() / "_data" / "history" / ".fao_backfill_progress.json"


def _load_fao_backfill_progress() -> set[str]:
    path = _fao_backfill_progress_path()
    if not path.is_file():
        return set()
    try:
        import json

        payload = json.loads(path.read_text(encoding="utf-8"))
        return set(str(d) for d in (payload.get("completed_dates") or []))
    except Exception:
        return set()


def _save_fao_backfill_progress(completed: set[str]) -> None:
    import json

    path = _fao_backfill_progress_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"completed_dates": sorted(completed), "count": len(completed)}, indent=2),
        encoding="utf-8",
    )


def load_fo_bhavcopy_derivatives_frame(*, start: str, end: str) -> pd.DataFrame:
    """Daily PCR / fut ratio from Nifty50 stock F&O bhavcopy CSV in repo."""
    try:
        from trade_integrations.nse_browser.repository import repo_root
        from trade_integrations.nse_browser.parsers.fo_derivatives import parse_nifty50_fo_bhavcopy_csv

        root = repo_root()
        for name in ("nifty50_fo_data_filtered.csv", "nifty50_fo_panel.csv"):
            path = root / name
            if path.is_file():
                frame = parse_nifty50_fo_bhavcopy_csv(path)
                if not frame.empty:
                    out = frame.copy()
                    out["date"] = out["date"].astype(str).str[:10]
                    out = out[(out["date"] >= start[:10]) & (out["date"] <= end[:10])]
                    return out.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    except Exception as exc:
        logger.debug("FO bhavcopy deriv load failed: %s", exc)
    return pd.DataFrame()


def backfill_nse_fao_to_cold_tier(
    *,
    start: str = "2007-01-01",
    end: str | None = None,
    sleep_s: float = 0.35,
    max_fetch: int | None = None,
    resume: bool = True,
    dry_run: bool = False,
) -> dict[str, int | str]:
    """Bulk-fetch NSE FAO participant OI archives into flow_derivatives_daily cold tier."""
    from datetime import datetime, timezone

    from trade_integrations.dataflows.index_research.history_store import (
        load_history_dataset,
        save_history_dataset,
    )
    from trade_integrations.dataflows.index_research.sources.history_loader import load_nifty_history

    end_day = (end or datetime.now(timezone.utc).date().isoformat())[:10]
    nifty = load_nifty_history(days=0)
    if nifty.empty:
        nifty = load_history_dataset("nifty_ohlcv_daily")
    if nifty.empty or "date" not in nifty.columns:
        return {"status": "error", "reason": "no_trading_calendar"}

    trading_dates = (
        nifty["date"]
        .astype(str)
        .str[:10]
        .loc[lambda s: (s >= start[:10]) & (s <= end_day)]
        .tolist()
    )
    existing = load_history_dataset("flow_derivatives_daily")
    have_pcr: set[str] = set()
    if not existing.empty and "nifty_pcr" in existing.columns:
        have_pcr = set(
            existing.loc[existing["nifty_pcr"].notna(), "date"].astype(str).str[:10].tolist()
        )

    completed = _load_fao_backfill_progress() if resume else set()
    missing = [d for d in trading_dates if d not in have_pcr and d not in completed]
    if max_fetch is not None and len(missing) > max_fetch:
        missing = missing[:max_fetch]

    if dry_run:
        return {
            "status": "dry_run",
            "missing": len(missing),
            "trading_days": len(trading_dates),
            "existing_pcr_days": len(have_pcr),
        }

    fetched_rows: list[dict] = []
    for idx, day in enumerate(missing):
        frame = fetch_nse_fao_participant_oi_for_date(day)
        if frame.empty:
            try:
                from trade_integrations.dataflows.index_research.participant_oi_backfill import (
                    fetch_participant_oi_day,
                )

                payload = fetch_participant_oi_day(day)
                if payload:
                    frame = pd.DataFrame([payload])
            except Exception as exc:
                logger.debug("nselib FAO fallback failed %s: %s", day, exc)
        if not frame.empty:
            fetched_rows.append(frame.iloc[0].to_dict())
            completed.add(day[:10])
        if sleep_s > 0 and idx < len(missing) - 1:
            time.sleep(sleep_s)
        if (idx + 1) % 50 == 0:
            _save_fao_backfill_progress(completed)

    _save_fao_backfill_progress(completed)

    if not fetched_rows:
        return {
            "status": "ok",
            "fetched": 0,
            "missing_requested": len(missing),
            "existing_pcr_days": len(have_pcr),
        }

    from trade_integrations.nse_browser.parsers.fii_dii import overlay_derivative_columns

    overlay = pd.DataFrame(fetched_rows)
    merged = overlay_derivative_columns(existing, overlay)
    result = save_history_dataset("flow_derivatives_daily", merged)
    return {
        "status": "ok",
        "fetched": len(fetched_rows),
        "missing_requested": len(missing),
        **result,
    }


def load_nifty_oi_daily_frame(*, start: str, end: str) -> pd.DataFrame:
    """Load historic nifty OI daily (PCR proxy) from repo parquet."""
    try:
        from pathlib import Path

        from trade_integrations.nse_browser.repository import repo_root

        path = repo_root() / "nifty_oi_daily.parquet"
        if path.is_file():
            frame = pd.read_parquet(path)
        else:
            from trade_integrations.nse_browser.repository import load_repo_dataset

            frame = load_repo_dataset("nifty_oi_daily")
    except Exception:
        return pd.DataFrame()
    if frame.empty or "date" not in frame.columns:
        return pd.DataFrame()
    out = frame.copy()
    out["date"] = out["date"].astype(str).str[:10]
    out = out[(out["date"] >= start[:10]) & (out["date"] <= end[:10])]
    if "pcr" in out.columns and "nifty_pcr" not in out.columns:
        out["nifty_pcr"] = pd.to_numeric(out["pcr"], errors="coerce")
    return out.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)


def flow_backfill_summary(*, days: int = 365, allow_live_fetch: bool = False) -> dict[str, int | str]:
    """Dry-run summary of merged flow coverage."""
    from trade_integrations.dataflows.index_research.sources.history_loader import load_nifty_history

    nifty = load_nifty_history(days=days)
    if nifty.empty:
        return {"status": "error", "reason": "no_nifty_history"}
    start = str(nifty["date"].iloc[0])[:10]
    end = str(nifty["date"].iloc[-1])[:10]
    trading_dates = nifty["date"].astype(str).str[:10].tolist()
    frame = merge_flow_derivatives_frame(start, end, allow_live_fetch=allow_live_fetch)
    era_start = flow_effective_start(frame)
    era_dates = [d for d in trading_dates if era_start is None or d >= era_start]
    fii_days = int(frame["fii_net"].notna().sum()) if "fii_net" in frame.columns else 0
    dii_days = int(frame["dii_net"].notna().sum()) if "dii_net" in frame.columns else 0
    era_fii = fii_days
    era_total = len(era_dates) or 1
    return {
        "status": "ok",
        "start": start,
        "end": end,
        "rows": len(frame),
        "fii_net_days": fii_days,
        "dii_net_days": dii_days,
        "pcr_days": int(frame["nifty_pcr"].notna().sum()) if "nifty_pcr" in frame.columns else 0,
        "fut_ratio_days": int(frame["fii_fut_long_short_ratio"].notna().sum())
        if "fii_fut_long_short_ratio" in frame.columns
        else 0,
        "flow_effective_start": era_start,
        "flow_era_trading_days": len(era_dates),
        "fii_net_era_coverage_pct": round(100.0 * era_fii / era_total, 1),
        "monthly_gaps": flow_coverage_gaps_by_month(frame),
        "primary_source": "nse_browser_fii_dii+mrchartist_history_full+nse_fao_archive+flow_cache",
        "fii_cash_limit_note": (
            "Pre-2026-01-14 cash may be backfilled via nse_browser CSV mission; "
            "NSE fiidiiTradeReact remains today-only. Coverage gate uses flow-era "
            "(first real cash row) not pre-source calendar days."
        ),
    }
