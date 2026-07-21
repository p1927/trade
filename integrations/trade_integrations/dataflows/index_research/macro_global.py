"""Global macro factor collector for index research."""

from __future__ import annotations

import logging
import math
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd

from trade_integrations.hub_storage.parquet_io import concat_dataframes, concat_frames

from trade_integrations.dataflows.company_research.models import StageResult
from trade_integrations.dataflows.company_research.sources.macro_in import (
    _fetch_nselib_vix,
    _fetch_yfinance_vix,
)
from trade_integrations.dataflows.company_research.sources.resilience import (
    SourceAttempt,
    remediation_for,
    stage_status_from_attempts,
)

from .sources.rbi_cpi import fetch_rbi_cpi_context

logger = logging.getLogger(__name__)

_macro_fetch_ctx: dict[str, Any] = {}

_YFINANCE_FACTORS: dict[str, str] = {
    "oil_brent": "BZ=F",
    "oil_wti": "CL=F",
    "usd_inr": "INR=X",
    "gold": "GC=F",
    "sp500": "^GSPC",
}

_FRED_LATEST_RE = re.compile(r"\*\*Latest:\*\*\s*([\d.]+)")


def _stage_now() -> datetime:
    return datetime.now(timezone.utc)


def _fetch_yfinance_factor(factor: str, symbol: str) -> dict[str, Any] | None:
    import yfinance as yf

    info = yf.Ticker(symbol).info or {}
    price = info.get("regularMarketPrice") or info.get("previousClose")
    if price is None:
        return None
    return {
        "factor": factor,
        "value": float(price),
        "source": "yfinance",
        "metadata": {"symbol": symbol},
    }


def _fetch_us_10y() -> dict[str, Any] | None:
    today = datetime.now(timezone.utc).date().isoformat()

    try:
        from tradingagents.dataflows.interface import get_fred_macro_data

        excerpt = get_fred_macro_data("DGS10", today, look_back_days=30)
        if excerpt and "unavailable" not in excerpt.lower()[:120]:
            match = _FRED_LATEST_RE.search(excerpt)
            if match:
                return {
                    "factor": "us_10y",
                    "value": float(match.group(1)),
                    "source": "fred_tradingagents",
                    "metadata": {"series": "DGS10"},
                }
    except Exception as exc:
        logger.debug("tradingagents FRED us_10y failed: %s", exc)

    api_key = os.getenv("FRED_API_KEY", "").strip()
    if not api_key:
        return None

    try:
        from trade_integrations.http import get

        end = today
        start = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=30)).strftime("%Y-%m-%d")
        response = get(
            "https://api.stlouisfed.org/fred/series/observations",
            params={
                "series_id": "DGS10",
                "api_key": api_key,
                "file_type": "json",
                "observation_start": start,
                "observation_end": end,
                "sort_order": "desc",
                "limit": 5,
            },
            timeout=15,
        )
        response.raise_for_status()
        observations = response.json().get("observations", [])
        for obs in observations:
            raw = obs.get("value")
            if raw not in (".", None, ""):
                return {
                    "factor": "us_10y",
                    "value": float(raw),
                    "source": "fred_direct",
                    "metadata": {"series": "DGS10", "date": obs.get("date")},
                }
    except Exception as exc:
        logger.debug("direct FRED us_10y failed: %s", exc)

    return None


def _fetch_openalgo_vix() -> dict[str, Any] | None:
    from trade_integrations.dataflows.openalgo import fetch_openalgo_live_snapshot

    snap = fetch_openalgo_live_snapshot("INDIAVIX")
    if not snap or snap.get("ltp") is None:
        return None
    return {
        "india_vix": snap["ltp"],
        "source": snap.get("source") or "openalgo",
        "symbol": "INDIAVIX",
    }


def _fetch_india_vix() -> dict[str, Any] | None:
    for fetcher, source_name in (
        (_fetch_openalgo_vix, "openalgo"),
        (_fetch_nselib_vix, "nselib"),
        (_fetch_yfinance_vix, "yfinance"),
    ):
        try:
            payload = fetcher()
        except Exception as exc:
            logger.debug("%s india_vix failed: %s", source_name, exc)
            continue
        if payload and payload.get("india_vix") is not None:
            return {
                "factor": "india_vix",
                "value": float(payload["india_vix"]),
                "source": payload.get("source", source_name),
                "metadata": {k: v for k, v in payload.items() if k not in ("india_vix", "source")},
            }
    return None


def _fii_net_column(frame) -> str | None:
    for column in frame.columns:
        label = str(column).lower()
        if "fii" in label and "net" in label:
            return column
    return None


def _dii_net_column(frame) -> str | None:
    for column in frame.columns:
        label = str(column).lower()
        if "dii" in label and "net" in label:
            return column
    return None


def _fetch_flow_net_5d(*, factor: str, net_col_finder) -> dict[str, Any] | None:
    from trade_integrations.dataflows import source_availability

    capability = "fii_dii_trading_activity"
    if not source_availability.should_attempt("nselib", capability):
        return None

    try:
        from nselib import capital_market
    except ImportError as exc:
        source_availability.record_failure("nselib", capability, exc)
        return None

    fetcher = getattr(capital_market, "fii_dii_trading_activity", None)
    if fetcher is None:
        source_availability.record_failure("nselib", capability, "missing fii_dii_trading_activity")
        return None

    end = datetime.now().date()
    start = end - timedelta(days=10)
    try:
        frame = fetcher(
            from_date=start.strftime("%d-%m-%Y"),
            to_date=end.strftime("%d-%m-%Y"),
        )
    except Exception as exc:
        source_availability.record_failure("nselib", capability, exc)
        return None
    if frame is None or getattr(frame, "empty", True):
        source_availability.record_failure("nselib", capability, "empty fii_dii_trading_activity frame")
        return None

    net_col = net_col_finder(frame)
    if net_col is None:
        source_availability.record_failure("nselib", capability, "missing net column in fii_dii_trading_activity")
        return None

    tail = frame.tail(5)
    values = []
    for raw in tail[net_col]:
        try:
            values.append(float(raw))
        except (TypeError, ValueError):
            continue
    if not values:
        source_availability.record_failure("nselib", capability, "no numeric net values in fii_dii_trading_activity")
        return None

    source_availability.record_success("nselib", capability)
    return {
        "factor": factor,
        "value": sum(values),
        "source": "nselib",
        "metadata": {"rows": len(values), "column": str(net_col)},
    }


def _fetch_flow_net_5d_from_mrchartist(*, net_key: str, factor: str) -> dict[str, Any] | None:
    """Rolling 5-session sum from Mr. Chartist history + latest session."""
    try:
        from trade_integrations.dataflows.index_research.sources.nse_flow_derivatives_backfill import (
            fetch_mrchartist_flow_frame,
            fetch_mrchartist_latest_session,
        )
    except ImportError:
        return None

    mr = fetch_mrchartist_flow_frame(include_seeded=False)
    latest = fetch_mrchartist_latest_session()
    frames = [f for f in (mr, latest) if f is not None and not f.empty]
    if not frames:
        return None
    frame = concat_frames(frames).sort_values("date").drop_duplicates("date", keep="last")
    col = "fii_net" if net_key == "fii" else "dii_net"
    if col not in frame.columns:
        return None
    tail = frame.tail(5)[col].dropna()
    if tail.empty:
        return None
    return {
        "factor": factor,
        "value": float(tail.sum()),
        "source": "mrchartist",
        "metadata": {"rows": len(tail), "column": col},
    }


def _fetch_institutional_net_5d() -> dict[str, Any] | None:
    fii = _fetch_flow_net_5d_from_mrchartist(net_key="fii", factor="fii_net_5d")
    dii = _fetch_flow_net_5d_from_mrchartist(net_key="dii", factor="dii_net_5d")
    if not fii or not dii:
        return None
    fii_val = float(fii["value"])
    dii_val = float(dii["value"])
    return {
        "factor": "institutional_net_5d",
        "value": fii_val + dii_val,
        "source": "mrchartist_joint",
        "metadata": {"fii_net_5d": fii_val, "dii_net_5d": dii_val},
    }


def _fetch_dii_absorption_ratio() -> dict[str, Any] | None:
    fii = _fetch_flow_net_5d_from_mrchartist(net_key="fii", factor="fii_net_5d")
    dii = _fetch_flow_net_5d_from_mrchartist(net_key="dii", factor="dii_net_5d")
    if not fii or not dii:
        return None
    fii_val = float(fii["value"])
    dii_val = float(dii["value"])
    denom = max(abs(fii_val), 50.0)
    return {
        "factor": "dii_absorption_ratio",
        "value": dii_val / denom,
        "source": "mrchartist_joint",
        "metadata": {"fii_net_5d": fii_val, "dii_net_5d": dii_val},
    }


def _fetch_fii_net_5d() -> dict[str, Any] | None:
    live = _fetch_flow_net_5d_from_mrchartist(net_key="fii", factor="fii_net_5d")
    if live:
        return live
    return _fetch_flow_net_5d(factor="fii_net_5d", net_col_finder=_fii_net_column)


def _fetch_dii_net_5d() -> dict[str, Any] | None:
    live = _fetch_flow_net_5d_from_mrchartist(net_key="dii", factor="dii_net_5d")
    if live:
        return live
    return _fetch_flow_net_5d(factor="dii_net_5d", net_col_finder=_dii_net_column)


def _fetch_nifty_pe() -> dict[str, Any] | None:
    from trade_integrations.dataflows.index_research.sources.nifty_pe_fetch import (
        resolve_nifty_trailing_pe,
    )

    payload = resolve_nifty_trailing_pe(
        trading_day=_macro_fetch_ctx.get("trading_day"),
        force=bool(_macro_fetch_ctx.get("force")),
    )
    if not payload:
        return None
    return {
        "factor": "nifty_pe",
        "value": float(payload["value"]),
        "source": payload.get("source") or "unknown",
        "metadata": payload.get("metadata") or {},
    }


def _pcr_value_is_valid(value: Any) -> bool:
    if value is None:
        return False
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return False
    return not math.isnan(parsed) and parsed > 0


def _fetch_nifty_pcr() -> dict[str, Any] | None:
    try:
        from trade_integrations.hub_capture.channel import read_captured_pcr
    except ImportError:
        read_captured_pcr = None  # type: ignore[assignment,misc]

    if read_captured_pcr is not None:
        captured = read_captured_pcr("NIFTY")
        if _pcr_value_is_valid(captured):
            return {
                "factor": "nifty_pcr",
                "value": float(captured),
                "source": "hub_capture",
                "metadata": {"source": "capture_ledger"},
            }

    try:
        from trade_integrations.openalgo.market_data import fetch_option_chain_with_fallback
    except ImportError:
        return None

    try:
        chain = fetch_option_chain_with_fallback("NIFTY", "NFO", strike_count=15, is_index=True)
    except Exception as exc:
        logger.debug("OpenAlgo NIFTY PCR fetch failed: %s", exc)
        return None

    pcr = chain.get("pcr")
    if not _pcr_value_is_valid(pcr):
        return None
    return {
        "factor": "nifty_pcr",
        "value": float(pcr),
        "source": chain.get("source") or "openalgo",
        "metadata": {
            "expiry_date": chain.get("expiry_date"),
            "total_call_oi": chain.get("total_call_oi"),
            "total_put_oi": chain.get("total_put_oi"),
        },
    }


def _fetch_nifty_technical_factors() -> dict[str, Any] | None:
    from trade_integrations.dataflows.index_research.sources.history_loader import (
        load_nifty_history,
    )
    from trade_integrations.dataflows.index_research.technical_features import (
        technical_factor_rows,
    )

    history = load_nifty_history(days=280)
    rows = technical_factor_rows(history)
    if not rows:
        return None
    return {"rows": rows}


def _fetch_calendar_factors() -> dict[str, Any] | None:
    from datetime import date

    from trade_integrations.dataflows.index_research.calendar_features import (
        calendar_factor_rows,
    )

    rows = calendar_factor_rows(date.today())
    return {"rows": rows}


def _fetch_rbi_factors() -> dict[str, Any] | None:
    context = fetch_rbi_cpi_context()
    rows: list[dict[str, Any]] = []
    source = context.get("source", "rbi")

    if context.get("repo_rate") is not None:
        rows.append(
            {
                "factor": "repo_rate",
                "value": float(context["repo_rate"]),
                "source": source,
                "metadata": {"rbi_events": context.get("rbi_events", [])},
            }
        )
    if context.get("cpi_yoy_proxy") is not None:
        rows.append(
            {
                "factor": "cpi_yoy_proxy",
                "value": float(context["cpi_yoy_proxy"]),
                "source": source,
            }
        )

    if not rows:
        return None
    return {"rows": rows, "context": context}


def _fetch_index_sentiment(constituent_sentiments: list[float] | None) -> dict[str, Any] | None:
    if not constituent_sentiments:
        return None
    scores = [float(s) for s in constituent_sentiments]
    return {
        "factor": "index_sentiment",
        "value": sum(scores) / len(scores),
        "source": "constituent_finbert",
        "metadata": {"count": len(scores)},
    }


def _attempt_from_fetch(name: str, fetcher) -> SourceAttempt:
    try:
        payload = fetcher()
    except Exception as exc:
        return SourceAttempt(name=name, status="error", error=str(exc))
    if not payload:
        return SourceAttempt(
            name=name,
            status="error",
            error="no data",
            remediation=remediation_for("no_data"),
        )
    return SourceAttempt(name=name, status="ok", data=payload)


def _factor_row_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    factor_key = str(payload.get("factor") or "")
    from trade_integrations.dataflows.index_research.explain import _FACTOR_LABELS

    row: dict[str, Any] = {
        "factor": factor_key,
        "label": _FACTOR_LABELS.get(factor_key) or factor_key.replace("_", " ").title(),
        "value": payload["value"],
        "source": payload.get("source"),
    }
    if payload.get("z_score") is not None:
        row["z_score"] = payload["z_score"]
    if payload.get("metadata"):
        row["metadata"] = payload["metadata"]
    return row


def collect_global_factor_rows(*, constituent_sentiments: list[float] | None = None) -> list[dict]:
    """Return factor rows ready for ``save_daily_factors``."""
    return fetch_global_macro_snapshot(
        constituent_sentiments=constituent_sentiments
    ).data.get("factor_rows", [])


def _stage_result_to_cache(stage: StageResult) -> dict[str, Any]:
    return {
        "stage": stage.stage,
        "status": stage.status,
        "vendor": stage.vendor,
        "fetched_at": stage.fetched_at.isoformat(),
        "data": stage.data,
        "errors": list(stage.errors),
    }


def _stage_result_from_cache(payload: dict[str, Any]) -> StageResult:
    fetched_raw = payload.get("fetched_at") or ""
    try:
        fetched_at = datetime.fromisoformat(str(fetched_raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        fetched_at = _stage_now()
    return StageResult(
        stage=str(payload.get("stage") or "macro_global"),
        status=payload.get("status") or "ok",
        vendor=str(payload.get("vendor") or "macro_global"),
        fetched_at=fetched_at,
        data=dict(payload.get("data") or {}),
        errors=list(payload.get("errors") or []),
    )


def _fetch_yfinance_factors_parallel() -> list[tuple[str, SourceAttempt]]:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    out: list[tuple[str, SourceAttempt]] = []
    with ThreadPoolExecutor(max_workers=len(_YFINANCE_FACTORS) or 1) as pool:
        futures = {
            pool.submit(_attempt_from_fetch, factor, lambda f=factor, s=symbol: _fetch_yfinance_factor(f, s)): factor
            for factor, symbol in _YFINANCE_FACTORS.items()
        }
        for future in as_completed(futures):
            factor = futures[future]
            try:
                out.append((factor, future.result()))
            except Exception as exc:
                out.append((factor, SourceAttempt(name=factor, status="error", error=str(exc))))
    out.sort(key=lambda row: row[0])
    return out


def _fetch_global_macro_snapshot_uncached(
    *,
    constituent_sentiments: list[float] | None = None,
) -> StageResult:
    """Collect daily global macro factors for index research."""
    attempts: list[SourceAttempt] = []
    factor_rows: list[dict[str, Any]] = []
    factors: dict[str, Any] = {}

    for _factor, attempt in _fetch_yfinance_factors_parallel():
        attempts.append(attempt)
        if attempt.status == "ok" and attempt.data:
            row = _factor_row_from_payload(attempt.data)
            factor_rows.append(row)
            factors[_factor] = attempt.data["value"]

    for name, fetcher in (
        ("us_10y", _fetch_us_10y),
        ("india_vix", _fetch_india_vix),
        ("fii_net_5d", _fetch_fii_net_5d),
        ("dii_net_5d", _fetch_dii_net_5d),
        ("institutional_net_5d", _fetch_institutional_net_5d),
        ("dii_absorption_ratio", _fetch_dii_absorption_ratio),
        ("nifty_pe", _fetch_nifty_pe),
        ("nifty_pcr", _fetch_nifty_pcr),
    ):
        attempt = _attempt_from_fetch(name, fetcher)
        attempts.append(attempt)
        if attempt.status == "ok" and attempt.data:
            row = _factor_row_from_payload(attempt.data)
            factor_rows.append(row)
            factors[name] = attempt.data["value"]

    for name, fetcher in (
        ("nifty_technical", _fetch_nifty_technical_factors),
        ("calendar", _fetch_calendar_factors),
    ):
        attempt = _attempt_from_fetch(name, fetcher)
        attempts.append(attempt)
        if attempt.status == "ok" and attempt.data:
            for row in attempt.data.get("rows", []):
                factor_rows.append(row)
                factors[row["factor"]] = row["value"]

    rbi_attempt = _attempt_from_fetch("rbi_cpi", _fetch_rbi_factors)
    attempts.append(rbi_attempt)
    if rbi_attempt.status == "ok" and rbi_attempt.data:
        for row in rbi_attempt.data.get("rows", []):
            factor_rows.append(row)
            factors[row["factor"]] = row["value"]
        context = rbi_attempt.data.get("context") or {}
        rbi_events = context.get("rbi_events") or []
        if rbi_events:
            factors["rbi_events"] = rbi_events

    if constituent_sentiments:
        sentiment_attempt = _attempt_from_fetch(
            "index_sentiment",
            lambda: _fetch_index_sentiment(constituent_sentiments),
        )
        attempts.append(sentiment_attempt)
        if sentiment_attempt.status == "ok" and sentiment_attempt.data:
            row = _factor_row_from_payload(sentiment_attempt.data)
            factor_rows.append(row)
            factors["index_sentiment"] = sentiment_attempt.data["value"]
    else:
        attempts.append(
            SourceAttempt(
                name="index_sentiment",
                status="skipped",
                error="constituent_sentiments not provided",
            )
        )

    try:
        from trade_integrations.dataflows.index_research.sources.india_rates import india_rate_factor_rows

        repo = factors.get("repo_rate")
        repo_f = float(repo) if repo is not None else None
        for row in india_rate_factor_rows(repo_rate=repo_f):
            factor_rows.append(_factor_row_from_payload(row))
            factors[row["factor"]] = row["value"]
    except Exception as exc:
        logger.debug("india rate snapshot skipped: %s", exc)

    try:
        from trade_integrations.dataflows.index_research.fundamental_features import (
            fundamental_factor_rows_from_dict,
        )
        from trade_integrations.dataflows.index_research.spread_features import spread_factor_rows_from_dict

        for row in fundamental_factor_rows_from_dict(factors):
            factor_rows.append(_factor_row_from_payload(row))
            factors[row["factor"]] = row["value"]
        for row in spread_factor_rows_from_dict(factors):
            if row["factor"] not in factors:
                factor_rows.append(_factor_row_from_payload(row))
                factors[row["factor"]] = row["value"]

        # Velocities need short history window.
        from datetime import date, timedelta

        from trade_integrations.dataflows.index_research.factor_store import load_factor_history
        from trade_integrations.dataflows.index_research.spread_features import enrich_spread_columns

        end_d = date.today().isoformat()
        start_d = (date.today() - timedelta(days=20)).isoformat()
        hist = load_factor_history(start_d, end_d)
        if not hist.empty and "factor" in hist.columns:
            wide = hist.pivot_table(index="date", columns="factor", values="value", aggfunc="last")
            wide = wide.reset_index().rename(columns={"index": "date"})
            if "date" not in wide.columns:
                wide["date"] = wide.index.astype(str)
            spread_frame = enrich_spread_columns(wide)
            if not spread_frame.empty:
                last = spread_frame.iloc[-1]
                for key in (
                    "india_vix_velocity_3d",
                    "usd_inr_momentum_5d",
                    "us_10y_velocity_3d",
                    "fii_net_5d_momentum",
                ):
                    val = last.get(key)
                    if val is not None and not pd.isna(val):
                        payload = {"factor": key, "value": float(val), "source": "spread_features_live"}
                        factor_rows.append(_factor_row_from_payload(payload))
                        factors[key] = float(val)
    except Exception as exc:
        logger.debug("phase I live derived skipped: %s", exc)

    try:
        from datetime import date

        from trade_integrations.dataflows.index_research.panel_live_parity import (
            merge_panel_parity_into_factors,
            upsert_factor_rows_for_parity,
        )

        parity_day = str(_macro_fetch_ctx.get("trading_day") or date.today().isoformat())[:10]
        factors, parity_applied = merge_panel_parity_into_factors(factors, parity_day)
        if parity_applied:
            factor_rows = upsert_factor_rows_for_parity(factor_rows, factors, parity_applied)
    except Exception as exc:
        logger.debug("panel parity overlay skipped: %s", exc)

    has_output = bool(factor_rows)
    status = stage_status_from_attempts(attempts, has_output=has_output)

    return StageResult(
        stage="macro_global",
        status=status,
        vendor="macro_global",
        fetched_at=_stage_now(),
        data={
            "factors": factors,
            "factor_rows": factor_rows,
            "source_attempts": [a.to_dict() for a in attempts],
        },
        errors=[f"{a.name}: {a.error}" for a in attempts if a.status != "ok" and a.error],
    )


def fetch_global_macro_snapshot(
    *,
    constituent_sentiments: list[float] | None = None,
    trading_day: str | None = None,
    force: bool = False,
) -> StageResult:
    """Collect daily global macro factors for index research (trading-day cache)."""
    from trade_integrations.dataflows.company_research.market import india_trading_date_iso
    from trade_integrations.dataflows.index_research.day_cache import get_or_fetch

    day = (trading_day or india_trading_date_iso())[:10]
    _macro_fetch_ctx["trading_day"] = day
    _macro_fetch_ctx["force"] = force
    try:

        def fetch() -> dict[str, Any]:
            stage = _fetch_global_macro_snapshot_uncached(
                constituent_sentiments=constituent_sentiments,
            )
            return _stage_result_to_cache(stage)

        payload, cached = get_or_fetch(
            namespace="macro_snapshot",
            trading_day=day,
            fetch_fn=fetch,
            force=force,
        )
        stage = _stage_result_from_cache(payload)
        stage.data["_cached"] = cached
        return stage
    finally:
        _macro_fetch_ctx.clear()
