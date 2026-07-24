#!/usr/bin/env python3
"""Incrementally extend local HF NSE replay data (append-only, no full re-sync)."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
DEFAULT_DATA_ROOT = REPO / "data/nse/historic_data"
HF_DATASET = "thetrademarkk/india-index-options-1m"
HF_LOCAL = "hf-india-index-options-1m"
MANIFEST_REL = Path("replay/meta/source_manifest.json")


def _repo_root() -> Path:
    return REPO


def _hf_root(data_root: Path) -> Path:
    return data_root / "replay" / HF_LOCAL


def _read_index_watermark(path: Path) -> pd.Timestamp | None:
    if not path.is_file():
        return None
    df = pd.read_parquet(path, columns=["timestamp"])
    ts = pd.to_datetime(df["timestamp"])
    return ts.max()


def _read_options_watermark(opt_dir: Path) -> str | None:
    if not opt_dir.is_dir():
        return None
    latest: str | None = None
    for file in opt_dir.glob("*.parquet"):
        try:
            days = pd.read_parquet(file, columns=["trading_day"])["trading_day"].astype(str)
        except Exception:
            continue
        if days.empty:
            continue
        day_max = days.max()
        if latest is None or day_max > latest:
            latest = day_max
    return latest


def _merge_parquet_delta(local_path: Path, remote_path: Path, *, ts_col: str) -> int:
    local = pd.read_parquet(local_path)
    remote = pd.read_parquet(remote_path)
    local[ts_col] = pd.to_datetime(local[ts_col])
    remote[ts_col] = pd.to_datetime(remote[ts_col])
    watermark = local[ts_col].max()
    delta = remote[remote[ts_col] > watermark]
    if delta.empty:
        return 0
    merged = pd.concat([local, delta], ignore_index=True)
    merged = merged.drop_duplicates(subset=[ts_col], keep="last").sort_values(ts_col)
    merged.to_parquet(local_path, index=False)
    return len(delta)


def _merge_options_file(local_path: Path, remote_path: Path) -> int:
    local = pd.read_parquet(local_path)
    remote = pd.read_parquet(remote_path)
    local["timestamp"] = pd.to_datetime(local["timestamp"])
    remote["timestamp"] = pd.to_datetime(remote["timestamp"])
    if "trading_day" in local.columns:
        watermark = local["trading_day"].astype(str).max()
        remote_days = remote["trading_day"].astype(str)
        delta = remote[remote_days > watermark]
    else:
        watermark_ts = local["timestamp"].max()
        delta = remote[remote["timestamp"] > watermark_ts]
    if delta.empty:
        return 0
    merged = pd.concat([local, delta], ignore_index=True)
    merged = merged.drop_duplicates(subset=["timestamp", "strike", "option_type"], keep="last")
    merged = merged.sort_values("timestamp")
    merged.to_parquet(local_path, index=False)
    return len(delta)


def _download_hf_file(relpath: str, dest: Path) -> None:
    try:
        from huggingface_hub import hf_hub_download
    except ImportError as exc:
        raise SystemExit("Install huggingface_hub: pip install huggingface_hub") from exc

    dest.parent.mkdir(parents=True, exist_ok=True)
    cached = Path(
        hf_hub_download(
            repo_id=HF_DATASET,
            repo_type="dataset",
            filename=relpath,
        )
    )
    dest.write_bytes(cached.read_bytes())


def extend_index(data_root: Path, *, dry_run: bool = False) -> dict[str, int | str]:
    hf_root = _hf_root(data_root)
    local_index = hf_root / "index" / "NIFTY.parquet"
    if not local_index.is_file():
        raise SystemExit(f"Missing local index parquet: {local_index}")

    watermark = _read_index_watermark(local_index)
    if dry_run:
        return {"status": "dry_run", "watermark": str(watermark)}

    with tempfile.TemporaryDirectory() as tmp:
        remote_path = Path(tmp) / "NIFTY.parquet"
        _download_hf_file("index/NIFTY.parquet", remote_path)
        remote_max = pd.to_datetime(pd.read_parquet(remote_path, columns=["timestamp"])["timestamp"]).max()
        if watermark is not None and remote_max <= watermark:
            return {"status": "up_to_date", "watermark": str(watermark), "remote_max": str(remote_max)}
        added = _merge_parquet_delta(local_index, remote_path, ts_col="timestamp")
    return {"status": "extended", "added_rows": added, "watermark_before": str(watermark)}


def extend_options(data_root: Path, *, dry_run: bool = False) -> dict[str, int | str]:
    hf_root = _hf_root(data_root)
    opt_dir = hf_root / "options" / "NIFTY"
    opt_dir.mkdir(parents=True, exist_ok=True)
    watermark = _read_options_watermark(opt_dir)

    try:
        from huggingface_hub import HfApi
    except ImportError as exc:
        raise SystemExit("Install huggingface_hub: pip install huggingface_hub") from exc

    api = HfApi()
    remote_files = [
        f
        for f in api.list_repo_files(HF_DATASET, repo_type="dataset")
        if f.startswith("options/NIFTY/") and f.endswith(".parquet")
    ]
    if dry_run:
        return {"status": "dry_run", "watermark": watermark or "", "remote_files": len(remote_files)}

    added_files = 0
    added_rows = 0
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        for relpath in remote_files:
            name = Path(relpath).name
            local_path = opt_dir / name
            remote_tmp = tmp_path / name
            if not local_path.is_file():
                _download_hf_file(relpath, remote_tmp)
                remote_tmp.replace(local_path)
                added_files += 1
                continue
            _download_hf_file(relpath, remote_tmp)
            rows = _merge_options_file(local_path, remote_tmp)
            if rows:
                added_rows += rows
    return {
        "status": "extended",
        "added_files": added_files,
        "added_rows": added_rows,
        "watermark_before": watermark or "",
    }


def update_manifest(data_root: Path) -> None:
    hf_root = _hf_root(data_root)
    manifest_path = data_root / MANIFEST_REL
    manifest: dict = {}
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    index_path = hf_root / "index" / "NIFTY.parquet"
    opt_dir = hf_root / "options" / "NIFTY"
    if index_path.is_file():
        ts = pd.to_datetime(pd.read_parquet(index_path, columns=["timestamp"])["timestamp"])
        manifest.setdefault("index", {}).setdefault("NIFTY", {})
        manifest["index"]["NIFTY"].update(
            {
                "rows": int(len(ts)),
                "from": str(ts.min()),
                "to": str(ts.max()),
                "path": "index/NIFTY.parquet",
            }
        )
        manifest["watermark_index_to"] = str(ts.max())[:10]
    if opt_dir.is_dir():
        manifest["watermark_options_to"] = _read_options_watermark(opt_dir)
        manifest.setdefault("options", {}).setdefault("NIFTY", {})
        manifest["options"]["NIFTY"]["expiry_files"] = len(list(opt_dir.glob("*.parquet")))
    manifest["source"] = "huggingface"
    manifest["dataset_id"] = HF_DATASET
    manifest["local_dir"] = "data/nse/historic_data/replay/hf-india-index-options-1m"
    manifest["extended_at_utc"] = datetime.now(timezone.utc).isoformat()
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Append-only extend of HF NIFTY replay data")
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--index-only", action="store_true")
    parser.add_argument("--options-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    data_root = args.data_root.expanduser().resolve()
    results: dict[str, dict] = {}
    if not args.options_only:
        results["index"] = extend_index(data_root, dry_run=args.dry_run)
        print(json.dumps(results["index"], indent=2))
    if not args.index_only:
        results["options"] = extend_options(data_root, dry_run=args.dry_run)
        print(json.dumps(results["options"], indent=2))
    if not args.dry_run:
        update_manifest(data_root)
        print(f"Updated manifest: {data_root / MANIFEST_REL}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
