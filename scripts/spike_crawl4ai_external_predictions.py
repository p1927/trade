#!/usr/bin/env python3
"""Parallel Crawl4AI spike for all seed external-prediction sources."""

from __future__ import annotations

import argparse
import os
import sys
import time


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    parser = argparse.ArgumentParser(description="Spike parallel Crawl4AI fetch for external predictions")
    parser.add_argument("--horizon", type=int, default=30)
    args = parser.parse_args()

    sys.path.insert(0, os.path.join(_repo_root(), "integrations"))

    from trade_integrations.dataflows.crawl4ai_client import crawl4ai_is_installed
    from trade_integrations.dataflows.index_research.external_predictions.crawl4ai_fetcher import (
        crawl_sources_parallel,
        filter_markdown_for_extraction,
        pick_best_crawl_result,
        source_keywords,
    )
    from trade_integrations.dataflows.index_research.external_predictions.source_registry import (
        seed_registry_if_missing,
        watchlisted_sources,
    )

    if not crawl4ai_is_installed():
        print("crawl4ai not installed — pip install 'trade-stack[external-predictions]' && crawl4ai-setup")
        return 1

    seed_registry_if_missing()
    sources = watchlisted_sources()
    print(f"Spike: {len(sources)} watchlisted sources, horizon={args.horizon}d\n")

    started = time.time()
    grouped = crawl_sources_parallel(sources, horizon_days=args.horizon)
    elapsed = time.time() - started

    ok_sources = 0
    for src in sources:
        rows = grouped.get(src.id, [])
        best = pick_best_crawl_result(rows, source_keywords(src))
        print("=" * 60)
        print(f"SOURCE: {src.display_name} ({src.id})")
        if best is None:
            if rows:
                _, first = rows[0]
                err = first.error_message or "crawl failed"
            else:
                err = "no URLs"
            print(f"  FAIL — {err}")
            continue
        url, crawl = best
        filtered = filter_markdown_for_extraction(crawl.markdown, source_keywords(src))
        preview = [line for line in filtered.splitlines() if line.strip()][:6]
        print(f"  URL: {url}")
        print(f"  OK — {len(crawl.markdown)} chars, {crawl.elapsed_ms:.0f}ms")
        if preview:
            ok_sources += 1
            print(f"  Matches ({len(preview)} preview lines):")
            for line in preview:
                print(f"    • {line[:140]}")
        else:
            print("  WARN — crawled OK but no keyword lines")

    print("\n" + "=" * 60)
    print(f"Done in {elapsed:.1f}s — {ok_sources}/{len(sources)} sources with keyword matches")
    return 0 if ok_sources >= max(1, len(sources) // 2) else 1


if __name__ == "__main__":
    raise SystemExit(main())
