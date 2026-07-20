#!/usr/bin/env python3
"""Smoke-test curated external-prediction URLs (HEAD / quick crawl readiness)."""

from __future__ import annotations

import argparse
import sys
from urllib.request import Request, urlopen

from trade_integrations.dataflows.index_research.external_predictions.curated_urls import (
    CURATED_URLS_BY_SOURCE,
)
from trade_integrations.dataflows.index_research.external_predictions.url_policy import (
    is_allowed_listing_url,
)


def _head_ok(url: str, timeout: float = 12.0) -> tuple[bool, str]:
    try:
        req = Request(url, method="HEAD", headers={"User-Agent": "TradeExternalPredictionsAudit/1.0"})
        with urlopen(req, timeout=timeout) as resp:
            code = getattr(resp, "status", 200)
            if code >= 400:
                return False, f"HTTP {code}"
            return True, "ok"
    except Exception as exc:
        return False, str(exc)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit curated NIFTY 50 forecast URLs")
    parser.add_argument("--source", help="Limit to one source_id")
    parser.add_argument("--timeout", type=float, default=12.0)
    args = parser.parse_args()

    failed = 0
    checked = 0
    for source_id, urls in sorted(CURATED_URLS_BY_SOURCE.items()):
        if args.source and source_id != args.source:
            continue
        for url in urls:
            checked += 1
            policy = is_allowed_listing_url(url)
            if not policy.allowed:
                print(f"FAIL [{source_id}] policy:{policy.reason} {url}")
                failed += 1
                continue
            ok, reason = _head_ok(url, timeout=args.timeout)
            status = "PASS" if ok else "FAIL"
            print(f"{status} [{source_id}] {reason} {url}")
            if not ok:
                failed += 1

    print(f"\nChecked {checked} URL(s); {failed} failure(s)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
