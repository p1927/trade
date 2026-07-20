"""Refresh nse_browser hub datasets for the index prediction pipeline."""

from __future__ import annotations

import logging
import os
from datetime import date, timedelta
from typing import Any

logger = logging.getLogger(__name__)

_PREDICTION_DATASETS = ("fii_dii", "fpi")


def refresh_nse_browser_for_prediction(
    *,
    days: int = 365,
    refresh: bool = False,
    refresh_cookies: bool = False,
    agent_fallback: bool = True,
) -> dict[str, Any]:
    """
    Fetch-if-stale NSE/NSDL datasets used by merge_flow_derivatives_frame.

    Mirrors MCP get_nse_browser_data(dataset, start_date, end_date, refresh=...).
    """
    from trade_integrations.dataflows.index_research.factor_backfill_enrichment import (
        _prepare_nse_repository_layers,
    )
    from trade_integrations.nse_browser.orchestrator import get_nse_browser_data

    repo_sync = _prepare_nse_repository_layers(
        allow_live_fetch=refresh,
        enrich_days=days,
        batch_historic=False,
        skip_niftyinvest_fetch=True,
    )
    end = date.today().isoformat()
    start = (date.today() - timedelta(days=max(30, days))).isoformat()
    out: dict[str, Any] = {
        "status": "ok",
        "date_range": {"start": start, "end": end},
        "repo_sync": repo_sync,
        "datasets": {},
        "web_flow": None,
    }
    errors: list[str] = []

    if refresh or os.environ.get("NSE_BROWSER_WEB_FLOW_ON_REFRESH", "1").strip().lower() in {
        "1",
        "true",
        "yes",
    }:
        try:
            from trade_integrations.dataflows.index_research.sources.web_flow_fetch import (
                seed_niftyinvest_flow_to_repo,
            )

            out["niftyinvest_api"] = seed_niftyinvest_flow_to_repo(days=days)
            if out["niftyinvest_api"].get("status") != "ok":
                errors.append(
                    f"niftyinvest_api: {out['niftyinvest_api'].get('error') or out['niftyinvest_api'].get('status')}"
                )
        except Exception as exc:
            logger.warning("niftyinvest API seed failed: %s", exc)
            out["niftyinvest_api"] = {"status": "error", "error": str(exc)}
            errors.append(f"niftyinvest_api: {exc}")
        if os.environ.get("NSE_BROWSER_WEB_FLOW_BROWSER", "0").strip().lower() in {
            "1",
            "true",
            "yes",
        }:
            try:
                from trade_integrations.nse_browser.missions.web_flow_history import run_web_flow_history

                out["web_flow"] = run_web_flow_history(refresh_cookies=refresh_cookies)
                if out["web_flow"].get("status") not in ("ok", "partial"):
                    errors.append(f"web_flow: {out['web_flow'].get('error') or out['web_flow'].get('status')}")
            except Exception as exc:
                logger.warning("web_flow browser scrape failed: %s", exc)
                out["web_flow"] = {"status": "error", "error": str(exc)}
                errors.append(f"web_flow: {exc}")

    for dataset in _PREDICTION_DATASETS:
        try:
            payload = get_nse_browser_data(
                dataset,
                start_date=start,
                end_date=end,
                refresh=refresh,
                refresh_cookies=refresh_cookies,
                agent_fallback=agent_fallback,
            )
        except Exception as exc:
            logger.warning("nse_browser refresh failed for %s: %s", dataset, exc)
            payload = {"status": "error", "dataset": dataset, "error": str(exc)}
        out["datasets"][dataset] = payload
        if payload.get("status") not in ("ok", "partial"):
            err = str(payload.get("error") or payload.get("status") or "unknown")
            errors.append(f"{dataset}: {err}")

    if errors and not any(
        (out["datasets"].get(ds) or {}).get("row_count", 0) > 0 for ds in _PREDICTION_DATASETS
    ):
        out["status"] = "error"
        out["error"] = "; ".join(errors)
    elif errors:
        out["status"] = "partial"
        out["error"] = "; ".join(errors)
    return out
