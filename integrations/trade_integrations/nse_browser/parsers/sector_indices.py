"""Parse NSE sector index historical CSV exports (data/nse/nifty50/)."""

from __future__ import annotations

import re
from io import StringIO
from pathlib import Path
from typing import Any

import pandas as pd

_NIFTY50_DIR = "nifty50"
_INDEX_SLUGS: dict[str, str] = {
    "NIFTY 50": "nifty50",
    "NIFTY PHARMA": "pharma",
    "NIFTY METAL": "metal",
    "NIFTY OIL & GAS": "oil_gas",
    "NIFTY POWER": "power",
    "NIFTY PSU BANK": "psu_bank",
    "NIFTY PRIVATE BANK": "private_bank",
    "NIFTY MIDSMALL IT & TELECOM": "midsmall_it",
    "NIFTY MIDSMALL HEALTHCARE": "midsmall_health",
    "NIFTY MIDSMALL FINANCIAL SERVICES": "midsmall_fin",
}

_BREADTH_SECTORS: frozenset[str] = frozenset(
    slug for name, slug in _INDEX_SLUGS.items() if slug != "nifty50"
)


def index_slug(name: str) -> str:
    """Normalize NSE index display name to a stable slug."""
    key = str(name or "").strip().upper()
    if key in _INDEX_SLUGS:
        return _INDEX_SLUGS[key]
    slug = re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")
    return slug or "unknown"


def parse_sector_index_csv(text: str, *, source_file: str = "") -> pd.DataFrame:
    """Parse one NSE historical index CSV into long daily rows."""
    if not text or len(text.strip()) < 20:
        return pd.DataFrame()
    try:
        raw = pd.read_csv(
            StringIO(text),
            encoding="utf-8-sig",
            quotechar='"',
        )
    except Exception:
        return pd.DataFrame()
    if raw.empty:
        return pd.DataFrame()

    cols = {str(c).strip().lower(): c for c in raw.columns}
    name_col = cols.get("index name") or cols.get("index_name") or raw.columns[0]
    date_col = cols.get("date") or raw.columns[1]
    open_col = cols.get("open")
    high_col = cols.get("high")
    low_col = cols.get("low")
    close_col = cols.get("close")

    rows: list[dict[str, Any]] = []
    for _, item in raw.iterrows():
        index_name = str(item.get(name_col) or "").strip()
        if not index_name:
            continue
        parsed = pd.to_datetime(item.get(date_col), errors="coerce", dayfirst=True)
        if pd.isna(parsed):
            continue
        day = parsed.strftime("%Y-%m-%d")
        slug = index_slug(index_name)
        row: dict[str, Any] = {
            "date": day,
            "index_name": index_name,
            "index_slug": slug,
            "source": "nse_sector_csv",
        }
        if source_file:
            row["source_file"] = source_file
        for col_key, dest in (
            (open_col, "open"),
            (high_col, "high"),
            (low_col, "low"),
            (close_col, "close"),
        ):
            if col_key is None:
                continue
            val = item.get(col_key)
            if val is None or (isinstance(val, float) and pd.isna(val)):
                continue
            try:
                row[dest] = float(val)
            except (TypeError, ValueError):
                continue
        if "close" in row:
            rows.append(row)

    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows)
    return frame.sort_values(["date", "index_slug"]).drop_duplicates(["date", "index_slug"], keep="last")


def load_nifty50_sector_csvs(repo_root: Path) -> pd.DataFrame:
    """Load all sector index CSVs under data/nse/nifty50/."""
    csv_dir = repo_root / _NIFTY50_DIR
    if not csv_dir.is_dir():
        return pd.DataFrame()

    frames: list[pd.DataFrame] = []
    for path in sorted(csv_dir.glob("*.csv")):
        try:
            text = path.read_text(encoding="utf-8-sig")
        except OSError:
            continue
        parsed = parse_sector_index_csv(text, source_file=path.name)
        if not parsed.empty:
            frames.append(parsed)

    if not frames:
        return pd.DataFrame()
    merged = pd.concat(frames, ignore_index=True)
    return merged.sort_values(["date", "index_slug"]).drop_duplicates(["date", "index_slug"], keep="last")


def breadth_sector_slugs() -> frozenset[str]:
    return _BREADTH_SECTORS
