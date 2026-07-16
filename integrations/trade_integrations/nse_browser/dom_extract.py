"""Extract visible table data from NSE pages without triggering downloads."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

_FII_DII_TABLE_JS = """
() => {
  const rows = [];
  const tables = Array.from(document.querySelectorAll('table'));
  for (const table of tables) {
    const headerCells = Array.from(table.querySelectorAll('thead th, tr:first-child th, tr:first-child td'))
      .map(c => (c.textContent || '').trim().toLowerCase());
    if (!headerCells.length) continue;
    const hasDate = headerCells.some(h => h.includes('date'));
    const hasCategory = headerCells.some(h => h.includes('category') || h.includes('type'));
    if (!hasDate && !hasCategory) continue;

    const bodyRows = table.querySelectorAll('tbody tr');
    const dataRows = bodyRows.length ? bodyRows : table.querySelectorAll('tr');
    for (const tr of dataRows) {
      const cells = Array.from(tr.querySelectorAll('td, th')).map(c => (c.textContent || '').trim());
      if (cells.length < 3) continue;
      const row = {};
      const headers = headerCells.length >= cells.length ? headerCells : cells.map((_, i) => 'col' + i);
      headers.forEach((h, i) => {
        if (cells[i] !== undefined) row[h] = cells[i];
      });
      if (Object.keys(row).length >= 3) rows.push(row);
    }
  }
  return rows;
}
"""

_SCROLL_JS = """
() => {
  const before = document.documentElement.scrollHeight;
  window.scrollTo(0, document.documentElement.scrollHeight);
  return before !== document.documentElement.scrollHeight;
}
"""


def _normalize_dom_row(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, val in row.items():
        lk = str(key).lower().replace(" ", "_")
        if "date" in lk:
            out["date"] = val
        elif "category" in lk or lk == "type":
            out["category"] = val
        elif "buy" in lk:
            out["buy"] = val
        elif "sell" in lk:
            out["sell"] = val
        elif "net" in lk:
            out["net"] = val
        else:
            out[lk] = val
    return out


async def extract_fii_dii_table(tab) -> list[dict[str, Any]]:
    """Pull FII/DII rows from visible DOM tables."""
    if tab is None:
        return []
    try:
        raw = await tab.evaluate(_FII_DII_TABLE_JS)
        if not isinstance(raw, list):
            return []
        out: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            if "raw" in item and len(item) == 1:
                continue
            out.append(_normalize_dom_row(item))
        return out
    except Exception as exc:
        logger.debug("extract_fii_dii_table failed: %s", exc)
        return []


async def extract_fii_dii_table_all(tab, *, max_scrolls: int = 15) -> list[dict[str, Any]]:
    """Scroll/paginate and collect all FII/DII DOM rows."""
    if tab is None:
        return []
    seen: set[str] = set()
    merged: list[dict[str, Any]] = []

    for _ in range(max_scrolls):
        batch = await extract_fii_dii_table(tab)
        new_count = 0
        for row in batch:
            key = f"{row.get('date')}|{row.get('category')}|{row.get('net')}"
            if key not in seen:
                seen.add(key)
                merged.append(row)
                new_count += 1
        if new_count == 0 and _ > 0:
            break
        try:
            scrolled = await tab.evaluate(_SCROLL_JS)
            if not scrolled:
                break
        except Exception:
            break
        await asyncio.sleep(0.4)

    return merged


_NSEARCHIVES_LINKS_JS = """
() => Array.from(document.querySelectorAll('a[href]'))
  .map(a => a.href)
  .filter(h => h && (h.includes('nsearchives.nseindia.com') || h.includes('archives.nseindia.com'))
    && (h.toLowerCase().includes('.csv') || h.toLowerCase().includes('download')))
"""


async def collect_nsearchives_csv_links(tab) -> list[str]:
    if tab is None:
        return []
    try:
        hrefs = await tab.evaluate(_NSEARCHIVES_LINKS_JS)
        if isinstance(hrefs, list):
            return list(dict.fromkeys(str(h) for h in hrefs if h))
    except Exception as exc:
        logger.debug("collect_nsearchives_csv_links failed: %s", exc)
    return []
