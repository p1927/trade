"""Enrich existing factor snapshots with missing prediction inputs."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from trade_integrations.dataflows.index_research.constituents import load_nifty50_constituents
from trade_integrations.dataflows.index_research.factor_store import (
    get_factor_data_dir,
    upsert_daily_factors,
)
from trade_integrations.dataflows.index_research.sources.history_loader import load_nifty_history
from trade_integrations.dataflows.index_research.sources.nse_flow_derivatives_backfill import (
    backfill_nse_fao_to_cache,
    build_rolling_sum_series,
    fetch_mrchartist_flow_frame,
    flow_backfill_summary,
    merge_flow_derivatives_frame,
    upsert_flow_cash_cache,
)
from trade_integrations.dataflows.index_research.sources.rbi_repo_schedule import repo_rate_on

logger = logging.getLogger(__name__)

_SENTIMENT_SCALE = 10.0

_PHASE_I_DERIVED_SOURCE = "backfill_phase_i_derived"


def _persist_phase_i_derived_factors(*, days: int = 365) -> dict[str, int | str]:
    """Upsert Phase I derived columns from aligned wide history."""
    try:
        from trade_integrations.dataflows.index_research.sources.history_loader import (
            load_aligned_factor_history,
        )

        frame = load_aligned_factor_history(days=days)
        if frame.empty:
            return {"days": 0, "reason": "empty_history"}

        days_written = 0
        derived_keys = (
            "nifty_earnings_yield",
            "nifty_dividend_yield",
            "nifty_book_to_market",
            "nifty_pb_zscore_5y",
            "equity_risk_premium",
            "india_vix_velocity_3d",
            "usd_inr_momentum_5d",
            "us_10y_velocity_3d",
            "fii_net_5d_momentum",
            "india_term_spread",
        )
        for _, row in frame.iterrows():
            day = str(row["date"])
            rows: list[dict] = []
            for key in derived_keys:
                if key not in row.index:
                    continue
                val = row.get(key)
                if val is None or (isinstance(val, float) and np.isnan(val)):
                    continue
                rows.append(
                    {
                        "factor": key,
                        "value": float(val),
                        "source": _PHASE_I_DERIVED_SOURCE,
                    }
                )
            if rows:
                upsert_daily_factors(day, rows)
                days_written += 1
        return {"days": days_written, "status": "ok"}
    except Exception as exc:
        logger.warning("phase I derived persist failed: %s", exc)
        return {"days": 0, "status": "error", "error": str(exc)}


def _prepare_nse_repository_layers(
    *,
    allow_live_fetch: bool = True,
    enrich_days: int = 365,
) -> dict[str, int | dict[str, int]]:
    """Sync git-tracked data/nse seeds (FII/DII, SEBI monthly, sector CSVs) into hub."""
    try:
        from trade_integrations.nse_browser.repository import (
            ingest_repository_to_hub,
            sync_all_repo_seed_layers,
        )

        seed_counts = sync_all_repo_seed_layers(
            allow_live_fetch=allow_live_fetch,
            enrich_days=enrich_days,
            explicit=True,
        )
        hub_counts = ingest_repository_to_hub(allow_live_fetch=False, enrich_days=enrich_days, explicit=True)
        return {"seed": seed_counts, "hub": hub_counts}
    except Exception as exc:
        logger.warning("NSE repository sync failed: %s", exc)
        return {"seed": {}, "hub": {}, "error": str(exc)}


def fetch_fii_history_frame() -> pd.DataFrame:
    """Load daily FII/DII/PCR history (real rows only, no seeded continuity)."""
    return fetch_mrchartist_flow_frame(include_seeded=False)


def fetch_fii_daily_net_series(
    start: str,
    end: str,
    *,
    allow_live_fetch: bool = True,
) -> pd.Series:
    """Best-effort FII daily net (₹ crore) from merged public history."""
    frame = merge_flow_derivatives_frame(start, end, allow_live_fetch=allow_live_fetch)
    if frame.empty or "fii_net" not in frame.columns:
        return pd.Series(dtype=float)
    series = pd.Series(frame["fii_net"].astype(float).values, index=frame["date"].astype(str))
    return series.sort_index()


def fetch_dii_daily_net_series(
    start: str,
    end: str,
    *,
    allow_live_fetch: bool = True,
) -> pd.Series:
    """Best-effort DII daily net (₹ crore) from merged public history."""
    frame = merge_flow_derivatives_frame(start, end, allow_live_fetch=allow_live_fetch)
    if frame.empty or "dii_net" not in frame.columns:
        return pd.Series(dtype=float)
    series = pd.Series(frame["dii_net"].astype(float).values, index=frame["date"].astype(str))
    return series.sort_index()


def build_fii_net_5d_series(
    trading_dates: list[str],
    start: str,
    end: str,
    *,
    allow_live_fetch: bool = True,
) -> pd.Series:
    """Rolling 5-session FII net sum aligned to Nifty trading dates."""
    frame = merge_flow_derivatives_frame(start, end, allow_live_fetch=allow_live_fetch)
    return build_rolling_sum_series(frame, "fii_net", trading_dates, window=5)


def build_dii_net_5d_series(
    trading_dates: list[str],
    start: str,
    end: str,
    *,
    allow_live_fetch: bool = True,
) -> pd.Series:
    """Rolling 5-session DII net sum aligned to Nifty trading dates."""
    frame = merge_flow_derivatives_frame(start, end, allow_live_fetch=allow_live_fetch)
    return build_rolling_sum_series(frame, "dii_net", trading_dates, window=5)


def build_nifty_pe_proxy_series(nifty: pd.DataFrame) -> pd.Series:
    """Scale trailing PE by close ratio anchored at PE resolution date (no future-close leak)."""
    from trade_integrations.dataflows.index_research.sources.nifty_pe_fetch import (
        resolve_nifty_trailing_pe,
    )

    if nifty.empty or "close" not in nifty.columns:
        return pd.Series(dtype=float)

    resolved = resolve_nifty_trailing_pe()
    current_pe = resolved.get("value") if resolved else None
    if current_pe is None:
        return pd.Series(dtype=float)

    dates = nifty["date"].astype(str).str[:10]
    anchor_date = str(
        (resolved or {}).get("as_of")
        or (resolved or {}).get("metadata", {}).get("as_of")
        or dates.iloc[-1]
    )[:10]
    anchor_mask = dates <= anchor_date
    if not anchor_mask.any():
        return pd.Series(dtype=float)

    anchor_idx = anchor_mask[anchor_mask].index[-1]
    anchor_close = float(nifty["close"].iloc[anchor_idx])
    if anchor_close <= 0:
        return pd.Series(dtype=float)

    closes = nifty["close"].astype(float)
    pe_series = pd.Series(float("nan"), index=nifty.index, dtype=float)
    pe_series.loc[anchor_mask] = float(current_pe) * (closes.loc[anchor_mask] / anchor_close)
    return pd.Series(pe_series.values, index=dates)


def _yfinance_symbol(symbol: str) -> str:
    sym = symbol.strip().upper()
    if sym.endswith(".NS") or sym.endswith(".BO"):
        return sym
    return f"{sym}.NS"


def build_constituent_momentum_series(
    trading_dates: list[str],
    *,
    start: str,
    end: str,
) -> pd.Series:
    """Weighted 7d Nifty 50 momentum (%) per trading day via yfinance."""
    from trade_integrations.dataflows import source_availability

    try:
        import yfinance as yf
    except ImportError:
        return pd.Series(dtype=float)

    if not source_availability.should_attempt("yfinance", "history"):
        return pd.Series(dtype=float)

    constituents = load_nifty50_constituents()
    if not constituents:
        return pd.Series(dtype=float)

    start_d = date.fromisoformat(start[:10]) - timedelta(days=14)
    end_d = date.fromisoformat(end[:10]) + timedelta(days=2)
    date_index = pd.to_datetime(trading_dates)

    symbol_returns: dict[str, pd.Series] = {}
    for row in constituents:
        yf_sym = _yfinance_symbol(row.symbol)
        try:
            hist = yf.Ticker(yf_sym).history(
                start=start_d.isoformat(),
                end=end_d.isoformat(),
                auto_adjust=True,
            )
        except Exception as exc:
            logger.debug("momentum history failed for %s: %s", row.symbol, exc)
            continue
        if hist is None or hist.empty or len(hist) < 8:
            continue
        close_col = "Close" if "Close" in hist.columns else "close"
        closes = hist[close_col].astype(float)
        closes.index = pd.to_datetime(closes.index).tz_localize(None)
        ret_7d = (closes / closes.shift(7) - 1.0) * 100.0
        symbol_returns[row.symbol.upper()] = ret_7d

    if not symbol_returns:
        return pd.Series(dtype=float)

    out: dict[str, float] = {}
    for day in trading_dates:
        ts = pd.Timestamp(day)
        weighted = 0.0
        total_weight = 0.0
        for constituent in constituents:
            series = symbol_returns.get(constituent.symbol.upper())
            if series is None:
                continue
            eligible = series.index[series.index <= ts]
            if len(eligible) == 0:
                continue
            value = series.loc[eligible[-1]]
            if value is None or (isinstance(value, float) and np.isnan(value)):
                continue
            weighted += constituent.weight * float(value)
            total_weight += constituent.weight
        if total_weight > 0:
            out[day] = weighted / total_weight
    return pd.Series(out)


def _sentiment_proxy_from_momentum(momentum_pct: float) -> float:
    return float(np.clip(momentum_pct / _SENTIMENT_SCALE, -1.0, 1.0))


def build_institutional_joint_series(
    trading_dates: list[str],
    start: str,
    end: str,
    *,
    allow_live_fetch: bool = True,
) -> tuple[pd.Series, pd.Series]:
    """Rolling institutional_net_5d and dii_absorption_ratio aligned to Nifty dates."""
    fii_5d = build_fii_net_5d_series(trading_dates, start, end, allow_live_fetch=allow_live_fetch)
    dii_5d = build_dii_net_5d_series(trading_dates, start, end, allow_live_fetch=allow_live_fetch)
    inst = fii_5d.add(dii_5d, fill_value=np.nan)
    ratio = pd.Series(dtype=float)
    if not fii_5d.empty and not dii_5d.empty:
        aligned = pd.DataFrame({"fii": fii_5d, "dii": dii_5d}).dropna()
        if not aligned.empty:
            denom = aligned["fii"].abs().clip(lower=50.0)
            ratio_vals = aligned["dii"] / denom
            ratio = pd.Series(ratio_vals.values, index=aligned.index.astype(str))
    return inst, ratio


def purge_anomalous_factor_snapshots() -> list[str]:
    """Remove invalid daily factor files (None.csv, null stems)."""
    out_dir = get_factor_data_dir()
    removed: list[str] = []
    if not out_dir.is_dir():
        return removed
    bad = frozenset({"None", "none", "null", "NaT"})
    for path in list(out_dir.iterdir()):
        if path.suffix not in {".csv", ".parquet"}:
            continue
        if path.stem in bad or len(path.stem) != 10 or path.stem[4] != "-":
            try:
                path.unlink()
                removed.append(path.name)
            except OSError:
                pass
    return removed


def sync_flow_factors_from_merge(
    *,
    days: int | None = None,
    start: str | None = None,
    end: str | None = None,
    allow_live_fetch: bool = False,
) -> dict[str, int | str]:
    """Upsert nifty_pcr and fii_fut_long_short_ratio from merged flow frame."""
    if start is None or end is None:
        window = days if days is not None else 365
        nifty = load_nifty_history(days=window)
        if nifty.empty:
            return {"status": "error", "reason": "no_nifty_history", "days_upserted": 0}
        trading_dates = nifty["date"].astype(str).tolist()
        start = trading_dates[0]
        end = trading_dates[-1]
    else:
        nifty = load_nifty_history(days=0)
        if nifty.empty:
            return {"status": "error", "reason": "no_nifty_history", "days_upserted": 0}
        trading_dates = (
            nifty["date"]
            .astype(str)
            .str[:10]
            .loc[lambda s: (s >= start[:10]) & (s <= end[:10])]
            .tolist()
        )

    flow_frame = merge_flow_derivatives_frame(start, end, allow_live_fetch=allow_live_fetch)
    if flow_frame.empty:
        return {"status": "skipped", "reason": "empty_merge", "days_upserted": 0}

    days_upserted = 0
    for day in trading_dates:
        flow_day = flow_frame[flow_frame["date"].astype(str).str[:10] == day[:10]]
        if flow_day.empty:
            continue
        flow_row = flow_day.iloc[0]
        rows: list[dict] = []
        pcr = flow_row.get("nifty_pcr")
        if pcr is not None and not pd.isna(pcr) and float(pcr) > 0:
            rows.append(
                {
                    "factor": "nifty_pcr",
                    "value": float(pcr),
                    "source": "sync_flow_merge",
                }
            )
        fut_ratio = flow_row.get("fii_fut_long_short_ratio")
        if fut_ratio is not None and not pd.isna(fut_ratio) and float(fut_ratio) > 0:
            rows.append(
                {
                    "factor": "fii_fut_long_short_ratio",
                    "value": float(fut_ratio),
                    "source": "sync_flow_merge",
                }
            )
        if rows:
            upsert_daily_factors(day[:10], rows)
            days_upserted += 1

    return {"status": "ok", "days_upserted": days_upserted, "start": start[:10], "end": end[:10]}


def enrich_factor_history(*, days: int = 365, allow_live_fetch: bool = True) -> dict[str, int | str]:
    """Merge missing factors (repo_rate, FII, PE, momentum, sentiment proxies) into daily store."""
    repo_sync = _prepare_nse_repository_layers(allow_live_fetch=allow_live_fetch, enrich_days=days)
    removed = purge_anomalous_factor_snapshots()
    nifty = load_nifty_history(days=days)
    if nifty.empty:
        return {"days_enriched": 0, "reason": "no_nifty_history"}

    trading_dates = nifty["date"].astype(str).tolist()
    start = trading_dates[0]
    end = trading_dates[-1]

    fao_backfill: dict[str, int | str] = {"status": "skipped", "reason": "cached_only"}
    if allow_live_fetch:
        fao_backfill = backfill_nse_fao_to_cache(trading_dates, sleep_s=0.25)
    flow_frame = merge_flow_derivatives_frame(start, end, allow_live_fetch=allow_live_fetch)
    if not flow_frame.empty:
        cash_rows = flow_frame.to_dict("records")
        upsert_flow_cash_cache(cash_rows)

    fii_5d = build_fii_net_5d_series(trading_dates, start, end, allow_live_fetch=allow_live_fetch)
    dii_5d = build_dii_net_5d_series(trading_dates, start, end, allow_live_fetch=allow_live_fetch)
    inst_5d, absorption = build_institutional_joint_series(
        trading_dates,
        start,
        end,
        allow_live_fetch=allow_live_fetch,
    )
    pe_series = build_nifty_pe_proxy_series(nifty)
    momentum = build_constituent_momentum_series(trading_dates, start=start, end=end)
    flow_summary = flow_backfill_summary(days=days, allow_live_fetch=allow_live_fetch)

    sector_factors: dict[str, pd.Series] = {}
    try:
        from trade_integrations.dataflows.index_research.sources.sector_index_factors import (
            build_monthly_equity_flow_series,
            build_sector_price_factor_series,
        )
        from trade_integrations.nse_browser.repository import (
            load_repo_dataset,
            load_sector_indices_frame,
        )

        sector_frame = load_sector_indices_frame(start, end)
        sector_factors = build_sector_price_factor_series(sector_frame, trading_dates)
        mf_monthly = load_repo_dataset("mf_sebi")
        fii_monthly = load_repo_dataset("fii_sebi")
        sector_factors["mf_equity_net_monthly_cr"] = build_monthly_equity_flow_series(
            mf_monthly,
            trading_dates,
            factor_name="mf_equity_net_monthly_cr",
        )
        sector_factors["fii_equity_net_monthly_cr"] = build_monthly_equity_flow_series(
            fii_monthly,
            trading_dates,
            factor_name="fii_equity_net_monthly_cr",
        )
    except Exception as exc:
        logger.warning("sector / monthly flow factors failed: %s", exc)

    headline_flags_by_day: dict[str, dict[str, float]] = {}
    try:
        from trade_integrations.dataflows.index_research.sources.headline_event_flags import (
            build_headline_flag_series,
        )

        headline_flags_by_day = build_headline_flag_series(trading_dates)
    except Exception as exc:
        logger.warning("headline event flags failed: %s", exc)

    days_enriched = 0
    for _, row in nifty.iterrows():
        day = str(row["date"])
        rows: list[dict] = [
            {
                "factor": "repo_rate",
                "value": repo_rate_on(day),
                "source": "backfill_rbi_schedule",
            }
        ]
        try:
            from trade_integrations.dataflows.index_research.sources.india_rates import (
                india_rate_factor_rows,
            )

            rows.extend(india_rate_factor_rows(repo_rate=repo_rate_on(day)))
        except Exception as exc:
            logger.debug("india rate rows skipped for %s: %s", day, exc)

        if day in fii_5d and not pd.isna(fii_5d[day]):
            rows.append(
                {
                    "factor": "fii_net_5d",
                    "value": float(fii_5d[day]),
                    "source": "backfill_fii_history",
                }
            )

        if day in dii_5d and not pd.isna(dii_5d[day]):
            rows.append(
                {
                    "factor": "dii_net_5d",
                    "value": float(dii_5d[day]),
                    "source": "backfill_dii_history",
                }
            )

        if day in inst_5d.index and not pd.isna(inst_5d[day]):
            rows.append(
                {
                    "factor": "institutional_net_5d",
                    "value": float(inst_5d[day]),
                    "source": "backfill_joint_flows",
                }
            )

        if day in absorption.index and not pd.isna(absorption[day]):
            rows.append(
                {
                    "factor": "dii_absorption_ratio",
                    "value": float(absorption[day]),
                    "source": "backfill_joint_flows",
                }
            )

        if not flow_frame.empty:
            flow_day = flow_frame[flow_frame["date"] == day]
            if not flow_day.empty:
                flow_row = flow_day.iloc[0]
                pcr = flow_row.get("nifty_pcr")
                if pcr is not None and not pd.isna(pcr) and float(pcr) > 0:
                    rows.append(
                        {
                            "factor": "nifty_pcr",
                            "value": float(pcr),
                            "source": "backfill_fii_history",
                        }
                    )
                fut_ratio = flow_row.get("fii_fut_long_short_ratio")
                if fut_ratio is not None and not pd.isna(fut_ratio) and float(fut_ratio) > 0:
                    rows.append(
                        {
                            "factor": "fii_fut_long_short_ratio",
                            "value": float(fut_ratio),
                            "source": "backfill_fii_derivatives",
                        }
                    )
                sentiment = flow_row.get("fii_sentiment_score")
                if sentiment is not None and not pd.isna(sentiment):
                    normalized = float(np.clip((float(sentiment) - 50.0) / 50.0, -1.0, 1.0))
                    rows.extend(
                        [
                            {
                                "factor": "index_sentiment",
                                "value": normalized,
                                "source": "backfill_fii_sentiment",
                            },
                            {
                                "factor": "sector_breadth_mean_sentiment",
                                "value": normalized,
                                "source": "backfill_fii_sentiment",
                            },
                        ]
                    )

        if day in pe_series.index and not pd.isna(pe_series[day]):
            rows.append(
                {
                    "factor": "nifty_pe",
                    "value": float(pe_series[day]),
                    "source": "backfill_pe_proxy",
                }
            )

        if day in momentum.index and not pd.isna(momentum[day]):
            mom = float(momentum[day])
            rows.append(
                {
                    "factor": "constituent_momentum_7d",
                    "value": mom,
                    "source": "backfill_constituent_momentum",
                }
            )
            if not any(r["factor"] == "index_sentiment" for r in rows):
                sentiment = _sentiment_proxy_from_momentum(mom)
                rows.extend(
                    [
                        {
                            "factor": "index_sentiment",
                            "value": sentiment,
                            "source": "backfill_momentum_proxy",
                        },
                        {
                            "factor": "sector_breadth_mean_sentiment",
                            "value": sentiment,
                            "source": "backfill_momentum_proxy",
                        },
                    ]
                )

        for factor_name, series in sector_factors.items():
            if series.empty or day not in series.index:
                continue
            val = series[day]
            if val is None or (isinstance(val, float) and pd.isna(val)):
                continue
            rows.append(
                {
                    "factor": factor_name,
                    "value": float(val),
                    "source": "backfill_sector_indices",
                }
            )

        day_flags = headline_flags_by_day.get(day) or {}
        for flag_name, flag_val in day_flags.items():
            rows.append(
                {
                    "factor": flag_name,
                    "value": float(flag_val),
                    "source": "backfill_headline_flags",
                }
            )

        upsert_daily_factors(day, rows)
        days_enriched += 1

    news_backfill: dict[str, Any] = {}
    try:
        from trade_integrations.dataflows.index_research.news_event_features import (
            backfill_news_event_features,
        )

        news_backfill = backfill_news_event_features(trading_dates=trading_dates, ticker="NIFTY")
    except Exception as exc:
        logger.warning("news event features backfill failed: %s", exc)
        news_backfill = {"status": "error", "error": str(exc)}

    alpha_zoo_backfill: dict[str, Any] = {}
    try:
        from trade_integrations.dataflows.index_research.alpha_bridge.backfill import (
            backfill_alpha_zoo_history,
        )

        alpha_zoo_backfill = backfill_alpha_zoo_history(days=days)
    except Exception as exc:
        logger.warning("alpha zoo backfill failed: %s", exc)
        alpha_zoo_backfill = {"status": "error", "error": str(exc)}

    phase_i_persist = _persist_phase_i_derived_factors(days=days)

    return {
        "days_enriched": days_enriched,
        "start": start,
        "end": end,
        "removed_anomalous_files": removed,
        "repo_sync": repo_sync,
        "fao_backfill": fao_backfill,
        "flow_summary": flow_summary,
        "fii_days": int(fii_5d.notna().sum()) if not fii_5d.empty else 0,
        "dii_days": int(dii_5d.notna().sum()) if not dii_5d.empty else 0,
        "pcr_days": int(flow_frame["nifty_pcr"].notna().sum()) if "nifty_pcr" in flow_frame.columns else 0,
        "momentum_days": int(momentum.notna().sum()) if not momentum.empty else 0,
        "sector_breadth_days": int(sector_factors.get("sector_breadth_price_7d", pd.Series()).notna().sum()),
        "news_event_features": news_backfill,
        "alpha_zoo_backfill": alpha_zoo_backfill,
        "phase_i_persist": phase_i_persist,
    }


def list_factor_snapshot_dates() -> list[str]:
    """Return sorted YYYY-MM-DD stems for existing daily factor files."""
    out_dir = get_factor_data_dir()
    if not out_dir.is_dir():
        return []
    dates: list[str] = []
    for path in out_dir.iterdir():
        if path.suffix in {".parquet", ".csv"} and len(path.stem) == 10 and path.stem[4] == "-":
            dates.append(path.stem)
    return sorted(dates)
