"""Download Excel datasets from the Nifty 100 financial intelligence GitHub repo."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trade_integrations.dataflows.throttled_http import fetch_to_path

from .config import RAW_FILES, cache_dir, raw_url

logger = logging.getLogger(__name__)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch_raw_workbooks(*, force: bool = False) -> dict[str, Path]:
    """Fetch all configured Excel workbooks; return local cache paths keyed by sheet name."""
    out_dir = cache_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}

    for rel_path, key in RAW_FILES:
        dest = out_dir / Path(rel_path).name
        if dest.is_file() and not force:
            paths[key] = dest
            continue
        url = raw_url(rel_path)
        logger.info("Fetching %s", url)
        fetch_to_path(url, dest, force=force)
        paths[key] = dest

    return paths


def cache_manifest(paths: dict[str, Path]) -> dict[str, Any]:
    """Build manifest metadata for cached workbooks."""
    from .config import github_repo

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
        "source_repo": f"https://github.com/{github_repo()}",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "files": files,
    }
