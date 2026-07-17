#!/usr/bin/env python3
"""Verify hub data layer integration across phases 8–11."""

from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "integrations") not in sys.path:
    sys.path.insert(0, str(ROOT / "integrations"))


def _load_env() -> None:
    env_file = ROOT / ".env"
    if not env_file.is_file():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def _check(name: str, ok: bool, detail: str = "") -> dict:
    return {"name": name, "ok": ok, "detail": detail}


def main() -> int:
    _load_env()
    from trade_integrations.env import load_trade_env

    load_trade_env()
    results: list[dict] = []

    try:
        from trade_integrations.context.hub import get_hub_dir

        hub = get_hub_dir()
        results.append(_check("hub_dir", hub.is_dir(), str(hub)))
    except Exception as exc:
        results.append(_check("hub_dir", False, str(exc)))
        hub = ROOT / "reports" / "hub"

    modules = [
        "trade_integrations.hub_storage.executions_store",
        "trade_integrations.hub_storage.openalgo_fills_export",
        "trade_integrations.hub_storage.timescale_ticks",
        "trade_integrations.hub_storage.market_intelligence_archive",
        "trade_integrations.hub_storage.verified_news_store",
        "trade_integrations.hub_analytics.duckdb_views",
        "trade_integrations.hub_analytics.calibration_orchestrator",
        "trade_integrations.hub_analytics.manifest",
        "trade_integrations.hub_capture.registry",
        "trade_integrations.hub_capture.gate",
        "trade_integrations.hub_capture.writers",
        "trade_integrations.hub_capture.rollup",
        "trade_integrations.hub_capture.channel",
        "trade_integrations.monitor.execution_ledger",
        "trade_integrations.auto_paper.outcome_ledger",
        "trade_integrations.dataflows.options_research.strategy_ranker",
    ]
    for mod in modules:
        try:
            importlib.import_module(mod)
            results.append(_check(f"import:{mod.split('.')[-1]}", True))
        except Exception as exc:
            results.append(_check(f"import:{mod.split('.')[-1]}", False, str(exc)))

    data = hub / "_data"
    try:
        from trade_integrations.hub_storage.verified_news_store import ensure_hub_storage

        ensure_hub_storage()
    except Exception:
        pass
    paths = {
        "executions_json": data / "executions" / "ledger.json",
        "executions_parquet": data / "trades" / "executions.parquet",
        "fills_parquet": data / "trades" / "fills.parquet",
        "outcomes_parquet": data / "auto_paper" / "outcomes.parquet",
        "manifest": data / "manifest.json",
        "news_events": data / "news_events" / "events.parquet",
    }
    for key, path in paths.items():
        exists = path.is_file()
        results.append(_check(f"path:{key}", exists, str(path)))

    try:
        from trade_integrations.hub_analytics.duckdb_views import get_hub_connection

        con = get_hub_connection()
        views = {row[0] for row in con.execute("SHOW TABLES").fetchall()}
        con.close()
        expected = {
            "executions",
            "outcomes",
            "fills",
            "index_predictions",
            "news_daily",
            "derivatives_chain_daily",
            "news_verified",
            "news_impact_ledger",
        }
        missing = expected - views
        results.append(_check("duckdb_views", not missing, f"missing={sorted(missing)}" if missing else "ok"))
    except Exception as exc:
        results.append(_check("duckdb_views", False, str(exc)))

    try:
        from trade_integrations.dataflows.options_research.strategy_ranker import rank_strategies

        ranked = rank_strategies(
            [{"name": "iron_condor", "tags": ["range"], "legs": [{"symbol": "X", "price": 1.0}]}],
            chain_snapshot={"expiry_date": "30JUL25"},
            analytics={"iv_regime": "moderate"},
            history={},
            events=[],
            spot=100.0,
        )
        results.append(_check("ranker_calibration_hook", bool(ranked)))
    except Exception as exc:
        results.append(_check("ranker_calibration_hook", False, str(exc)))

    try:
        from trade_integrations.hub_analytics.calibration_orchestrator import run_morning_hub_calibration

        dry = run_morning_hub_calibration({"dry_run": True})
        results.append(_check("morning_orchestrator", dry.get("status") == "dry_run"))
    except Exception as exc:
        results.append(_check("morning_orchestrator", False, str(exc)))

    scheduler_modules = [
        "src.scheduled_research.hub_calibration_jobs",
        "src.scheduled_research.trade_data_jobs",
        "src.scheduled_research.index_jobs",
        "src.scheduled_research.capture_jobs",
    ]
    agent_src = ROOT / "vibetrading" / "agent"
    if str(agent_src) not in sys.path:
        sys.path.insert(0, str(agent_src))
    for mod in scheduler_modules:
        try:
            m = importlib.import_module(mod)
            if mod.endswith("hub_calibration_jobs"):
                register_fn = "register_default_hub_calibration_jobs"
            elif mod.endswith("trade_data_jobs"):
                register_fn = "register_default_trade_data_jobs"
            elif mod.endswith("capture_jobs"):
                register_fn = "register_default_hub_capture_jobs"
            else:
                register_fn = "register_default_index_jobs"
            results.append(_check(f"scheduler:{mod.split('.')[-1]}", hasattr(m, register_fn)))
        except Exception as exc:
            results.append(_check(f"scheduler:{mod.split('.')[-1]}", False, str(exc)))

    try:
        from trade_integrations.hub_capture.registry import load_registry, build_factor_tree
        from trade_integrations.hub_capture.gate import should_capture

        reg = load_registry(create=True)
        results.append(_check("capture_registry", bool(reg.get("entities"))))
        results.append(_check("capture_factor_tree", len(build_factor_tree()) >= 1))
        results.append(_check("capture_gate", should_capture("NIFTY", "derivatives_chain") is True))
    except Exception as exc:
        results.append(_check("capture_registry", False, str(exc)))

    try:
        from trade_integrations.hub_capture.channel import get_chain, channel_stats_today
        from trade_integrations.hub_capture.registry import save_registry, update_entity

        def _fake_chain(underlying, exchange, *, expiry_date=None, strike_count=None):
            return {
                "underlying": underlying.upper(),
                "underlying_ltp": 24500.0,
                "expiry_date": "16JUL26",
                "chain": [{"strike": 24500, "ce": {"ltp": 100, "oi": 500}, "pe": {"ltp": 95, "oi": 600}}],
                "source": "verify_mock",
            }

        save_registry({"entities": []})
        update_entity("NIFTY", {"capture_enabled": True, "factor_groups": ["derivatives", "flows"]})
        latest = hub / "NIFTY" / "options_research" / "latest.json"
        if latest.is_file():
            try:
                payload = json.loads(latest.read_text(encoding="utf-8"))
                payload["as_of"] = "2020-01-01T00:00:00+00:00"
                latest.write_text(json.dumps(payload), encoding="utf-8")
            except (json.JSONDecodeError, OSError):
                pass
        chain = get_chain("NIFTY", "NFO", _fake_chain, strike_count=5)
        stats = channel_stats_today()
        capture_dir = hub / "_data" / "capture" / "nifty" / "derivatives_chain"
        wrote = capture_dir.is_dir() and any(capture_dir.glob("*.parquet"))
        results.append(_check("hub_channel_fetch", bool(chain.get("chain"))))
        results.append(_check("hub_channel_write_through", wrote or stats.get("vendor_fetches", 0) >= 1))
    except Exception as exc:
        results.append(_check("hub_channel_fetch", False, str(exc)))
        results.append(_check("hub_channel_write_through", False, str(exc)))

    try:
        from trade_integrations.hub_storage.timescale_ticks import timescale_health

        results.append(_check("timescale_health", timescale_health().get("ok") is True, str(timescale_health())))
    except Exception as exc:
        results.append(_check("timescale_health", False, str(exc)))

    try:
        from trade_integrations.hub_storage.market_intelligence_archive import archive_market_intelligence

        dry = archive_market_intelligence(as_of_date="2099-01-01")
        results.append(_check("market_intelligence_archive", "symbols_scanned" in dry))
    except Exception as exc:
        results.append(_check("market_intelligence_archive", False, str(exc)))

    failed = [row for row in results if not row["ok"]]
    report = {"passed": len(results) - len(failed), "failed": len(failed), "checks": results}
    print(json.dumps(report, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
