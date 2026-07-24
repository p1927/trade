#!/usr/bin/env python3
"""Audit external prediction provenance for duplicate URLs and listing winners."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlparse

from trade_integrations.context.hub import get_hub_dir
from trade_integrations.dataflows.index_research.external_predictions.url_policy import (
    classify_page_kind,
)


def _load_latest_records(symbol: str, horizon_days: int) -> list[dict]:
    root = get_hub_dir() / symbol.upper() / "external_predictions" / "sources"
    records: list[dict] = []
    if not root.is_dir():
        return records
    for source_dir in sorted(root.iterdir()):
        if not source_dir.is_dir():
            continue
        path = source_dir / f"latest_{horizon_days}d.json"
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            records.append(payload)
    return records


def audit_records(records: list[dict]) -> dict:
    url_to_sources: dict[str, list[str]] = defaultdict(list)
    page_kinds: Counter[str] = Counter()
    domains: Counter[str] = Counter()
    ok_records = [r for r in records if r.get("fetch_status") == "ok"]
    denied_rationale = 0

    for record in ok_records:
        prov = record.get("provenance") or {}
        url = str(prov.get("url") or "").strip()
        source_id = str(record.get("source_id") or "")
        if url:
            url_to_sources[url].append(source_id)
            host = (urlparse(url).hostname or "").lower().removeprefix("www.")
            if host:
                domains[host] += 1
            page_kinds[classify_page_kind(url)] += 1
        blob = " ".join(record.get("rationale_bullets") or [])
        if any(
            phrase in blob.lower()
            for phrase in ("no explicit", "not a broker", "topic page", "no forecast")
        ):
            denied_rationale += 1

    duplicate_pairs = [
        (url, sources)
        for url, sources in url_to_sources.items()
        if len(set(sources)) > 1
    ]

    return {
        "sources_total": len(records),
        "sources_ok": len(ok_records),
        "duplicate_url_groups": len(duplicate_pairs),
        "duplicate_urls": [
            {"url": url, "sources": sorted(set(sources))} for url, sources in duplicate_pairs
        ],
        "distinct_domains_ok": len(domains),
        "page_kind_breakdown": dict(page_kinds),
        "ok_with_denying_rationale": denied_rationale,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="NIFTY")
    parser.add_argument("--horizon-days", type=int, default=14)
    parser.add_argument("--json", action="store_true", help="Print JSON report")
    args = parser.parse_args()

    records = _load_latest_records(args.symbol, args.horizon_days)
    report = audit_records(records)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"Symbol: {args.symbol} · horizon: {args.horizon_days}d")
        print(f"OK: {report['sources_ok']}/{report['sources_total']}")
        print(f"Duplicate URL groups: {report['duplicate_url_groups']}")
        print(f"Distinct domains (ok): {report['distinct_domains_ok']}")
        print(f"Page kinds: {report['page_kind_breakdown']}")
        print(f"OK with denying rationale: {report['ok_with_denying_rationale']}")
        for row in report["duplicate_urls"]:
            print(f"  DUPLICATE {row['url']} -> {', '.join(row['sources'])}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
