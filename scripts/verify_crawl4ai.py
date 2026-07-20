#!/usr/bin/env python3
"""Smoke test Crawl4AI stealth fetch for external predictions."""

from __future__ import annotations

import argparse
import os
import sys


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Crawl4AI install and stealth fetch")
    parser.add_argument(
        "--url",
        default="https://economictimes.indiatimes.com/markets/stocks/news",
        help="URL to crawl",
    )
    parser.add_argument(
        "--extract",
        action="store_true",
        help="Run MiniMax/regex extract on filtered markdown (requires MINIMAX_API_KEY for LLM path)",
    )
    args = parser.parse_args()

    sys.path.insert(0, os.path.join(_repo_root(), "integrations"))

    from trade_integrations.dataflows.crawl4ai_client import (
        crawl4ai_is_installed,
        crawl_urls_parallel_sync,
    )

    if not crawl4ai_is_installed():
        print("crawl4ai not installed")
        print("Run: pip install 'trade-stack[external-predictions]' && crawl4ai-setup")
        return 1

    print(f"Crawling: {args.url}")
    results = crawl_urls_parallel_sync([args.url])
    result = results[0]
    if not result.success:
        print(f"FAIL: {result.error_message}")
        return 1

    keywords = ["forecast", "target", "nifty", "outlook", "prediction"]
    matches = []
    for line in result.markdown.splitlines():
        lower = line.lower()
        if any(k in lower for k in keywords) and len(line.strip()) > 15:
            matches.append(line.strip())

    print(f"OK — {len(result.markdown)} chars, {result.elapsed_ms:.0f}ms")
    print("\nTop keyword matches:")
    for line in matches[:12]:
        print(f"  • {line[:160]}")

    if args.extract:
        key = os.environ.get("MINIMAX_API_KEY", "").strip() or os.environ.get("MINIMAX_CN_API_KEY", "").strip()
        if not key:
            print("\nWARN: MINIMAX_API_KEY not set — extract will use regex fallback only")
        from trade_integrations.dataflows.index_research.external_predictions.crawl4ai_fetcher import (
            filter_markdown_for_extraction,
        )
        from trade_integrations.dataflows.index_research.external_predictions.extractor import (
            extract_prediction_from_text,
        )
        from trade_integrations.dataflows.index_research.external_predictions.models import (
            ExternalPredictionSource,
        )

        source = ExternalPredictionSource(id="verify", display_name="Verify", domains=[], search_queries=[])
        body = filter_markdown_for_extraction(result.markdown, keywords)
        record = extract_prediction_from_text(
            source=source,
            horizon_days=30,
            spot=24000.0,
            title=result.title or "Verify",
            url=args.url,
            snippet=body[:500],
            body=body,
        )
        print("\nExtract result:")
        print(f"  status={record.fetch_status} mid={record.target.mid} model={record.extraction.get('model')}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
