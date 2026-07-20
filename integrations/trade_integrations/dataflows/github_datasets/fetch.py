"""Download CSV datasets from GitHub datasets/* repos."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trade_integrations.dataflows.throttled_http import fetch_to_path

from .config import DATASETS, cache_dir, raw_url

logger = logging.getLogger(__name__)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch_all(*, force: bool = False) -> dict[str, Path]:
    """Fetch configured CSV files; return local cache paths keyed by dataset key."""
    out_dir = cache_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}

    for spec in DATASETS:
        key = str(spec["key"])
        rel_path = str(spec["path"])
        dest = out_dir / f"{key}.csv"
        if dest.is_file() and not force:
            paths[key] = dest
            continue
        url = raw_url(str(spec["repo"]), str(spec["branch"]), rel_path)
        logger.info("Fetching %s", url)
        fetch_to_path(url, dest, force=force, timeout=120)
        paths[key] = dest

    return paths


def cache_manifest(paths: dict[str, Path]) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    for key, path in sorted(paths.items()):
        body = path.read_bytes()
        files.append(
            {
                "key": key,
                "path": str(path),
                "bytes": len(body),
                "sha256": _sha256_bytes(body),
                "mtime": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(),
            }
        )
    return {
        "sources": [spec["source_url"] for spec in DATASETS],
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "files": files,
    }
