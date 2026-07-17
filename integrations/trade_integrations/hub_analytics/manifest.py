"""Build and persist hub _data/manifest.json inventory."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trade_integrations.context.hub import get_hub_dir


def _count_glob(directory: Path, pattern: str) -> int:
    if not directory.is_dir():
        return 0
    return sum(1 for _ in directory.glob(pattern))


def _parquet_rows(path: Path) -> int | None:
    if not path.is_file():
        csv_path = path.with_suffix(".csv")
        if csv_path.is_file():
            try:
                return max(0, sum(1 for _ in csv_path.open(encoding="utf-8")) - 1)
            except OSError:
                return None
        return None
    try:
        import pandas as pd

        return int(len(pd.read_parquet(path)))
    except Exception:
        csv_path = path.with_suffix(".csv")
        if csv_path.is_file():
            try:
                return max(0, sum(1 for _ in csv_path.open(encoding="utf-8")) - 1)
            except OSError:
                return None
    return None


def _symbol_artifacts(hub: Path, symbol_dir: Path) -> dict[str, Any]:
    sym = symbol_dir.name
    out: dict[str, Any] = {"symbol": sym, "artifacts": {}}
    for kind in (
        "company_research",
        "options_research",
        "stock_research",
        "index_research",
        "agent_debate",
        "news_event_scenarios",
    ):
        latest = symbol_dir / kind / "latest.json"
        history_dir = symbol_dir / kind / "history"
        entry: dict[str, Any] = {"has_latest": latest.is_file()}
        if latest.is_file():
            entry["latest_mtime"] = datetime.fromtimestamp(
                latest.stat().st_mtime, tz=timezone.utc
            ).isoformat()
        if history_dir.is_dir():
            entry["history_count"] = _count_glob(history_dir, "*.json")
        if entry.get("has_latest") or entry.get("history_count"):
            out["artifacts"][kind] = entry
    return out if out["artifacts"] else {}


def build_manifest(hub: Path | None = None) -> dict[str, Any]:
    root = hub or get_hub_dir()
    data = root / "_data"
    manifest: dict[str, Any] = {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "hub_dir": str(root.resolve()),
        "symbols": [],
        "ledgers": {},
        "time_series": {},
    }

    for symbol_dir in sorted(root.iterdir()):
        if not symbol_dir.is_dir() or symbol_dir.name.startswith("_"):
            continue
        row = _symbol_artifacts(root, symbol_dir)
        if row:
            manifest["symbols"].append(row)

    ledger_specs = {
        "index_predictions": data / "index_predictions" / "ledger.parquet",
        "options_predictions": data / "options_predictions" / "ledger.parquet",
        "auto_paper_outcomes": data / "auto_paper" / "outcomes.parquet",
        "trade_executions": data / "trades" / "executions.parquet",
        "trade_fills": data / "trades" / "fills.parquet",
        "news_events": data / "news_events" / "events.parquet",
        "news_impact_ledger": data / "news_impact" / "ledger.parquet",
    }
    for name, path in ledger_specs.items():
        rows = _parquet_rows(path)
        if rows is not None:
            manifest["ledgers"][name] = {"path": str(path.relative_to(root)), "rows": rows}

    exec_json = data / "executions" / "ledger.json"
    if exec_json.is_file():
        try:
            payload = json.loads(exec_json.read_text(encoding="utf-8"))
            entries = payload.get("entries") if isinstance(payload, dict) else payload
            manifest["ledgers"]["executions_json"] = {
                "path": str(exec_json.relative_to(root)),
                "rows": len(entries) if isinstance(entries, list) else 0,
            }
        except (json.JSONDecodeError, OSError):
            pass

    factor_daily = data / "index_factors" / "daily"
    manifest["time_series"]["index_factors_daily"] = {
        "path": str(factor_daily.relative_to(root)) if factor_daily.is_dir() else "index_factors/daily",
        "days": _count_glob(factor_daily, "*.parquet") or _count_glob(factor_daily, "*.csv"),
    }
    poi = data / "participant_oi"
    if poi.is_dir():
        manifest["time_series"]["participant_oi"] = {
            "path": str(poi.relative_to(root)),
            "days": _count_glob(poi, "*.json"),
        }

    for key, rel in (
        ("ticks_daily", "_data/ticks/daily"),
        ("news_daily", "_data/news/daily"),
        ("derivatives_chain_daily", "_data/derivatives_chain/daily"),
    ):
        directory = root / rel
        if directory.is_dir():
            manifest["time_series"][key] = {
                "path": rel,
                "days": _count_glob(directory, "*.parquet") or _count_glob(directory, "*.csv"),
            }

    try:
        from trade_integrations.hub_storage.timescale_ticks import timescale_health

        manifest["timescale"] = timescale_health()
    except Exception:
        pass

    try:
        from trade_integrations.hub_capture.registry import build_capture_stats, load_registry
        from trade_integrations.hub_capture.rollup import capture_coverage_stats

        reg = load_registry(create=False)
        manifest["capture"] = {
            "registry_path": "_data/capture_registry.json",
            "entities": reg.get("entities") or [],
            "stats": build_capture_stats("NIFTY"),
            "coverage": capture_coverage_stats(entity_id="NIFTY"),
        }
    except Exception:
        pass

    model_path = data / "index_factors" / "model" / "latest.json"
    if model_path.is_file():
        manifest["time_series"]["index_model"] = {
            "path": str(model_path.relative_to(root)),
            "mtime": datetime.fromtimestamp(model_path.stat().st_mtime, tz=timezone.utc).isoformat(),
        }

    manifest["summary"] = {
        "symbol_count": len(manifest["symbols"]),
        "factor_days": manifest["time_series"].get("index_factors_daily", {}).get("days", 0),
    }

    try:
        from trade_integrations.hub_analytics.duckdb_views import list_views

        manifest["analytics"] = {
            "engine": "duckdb",
            "views": list_views(),
            "query_cli": "scripts/hub_query.py",
        }
    except Exception:
        pass

    try:
        from trade_integrations.auto_paper.outcome_ledger import (
            compute_execution_calibration_metrics,
            compute_paper_calibration_metrics,
        )

        manifest["calibration"] = {
            "paper": compute_paper_calibration_metrics(),
            "execution": compute_execution_calibration_metrics(),
        }
    except Exception:
        pass

    return manifest


def write_hub_manifest(*, sync_executions: bool = True) -> dict[str, Any]:
    """Sync execution parquet if needed, build manifest, write to hub."""
    if sync_executions:
        try:
            from trade_integrations.hub_storage.executions_store import sync_executions_from_ledger

            sync_executions_from_ledger()
        except Exception:
            pass
    hub = get_hub_dir()
    manifest = build_manifest(hub)
    out_path = hub / "_data" / "manifest.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(manifest, indent=2, default=str) + "\n", encoding="utf-8")
    return {"path": str(out_path), "summary": manifest.get("summary"), "calibration": manifest.get("calibration")}
