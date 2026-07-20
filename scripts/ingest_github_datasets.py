#!/usr/bin/env python3
"""Ingest external financial datasets (GitHub, Hugging Face, Kaggle) into hub."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "integrations") not in sys.path:
    sys.path.insert(0, str(ROOT / "integrations"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest external financial datasets into hub")
    parser.add_argument("--force-fetch", action="store_true", help="Re-download from remote sources")
    parser.add_argument(
        "--slow-fetch",
        action="store_true",
        help="Pace remote requests (sets TRADE_FETCH_DELAY_SEC=2, max retries=6)",
    )
    parser.add_argument("--github-only", action="store_true", help="GitHub datasets/* only")
    parser.add_argument("--external-only", action="store_true", help="HF/Kaggle/Archive only")
    parser.add_argument(
        "--cold-tier-only",
        action="store_true",
        help="Skip macro_daily merge for GitHub macro factors",
    )
    parser.add_argument("--verify-only", action="store_true", help="Report coverage/merge status only")
    parser.add_argument("--skip-hf", action="store_true", help="Skip Hugging Face NSE download")
    parser.add_argument("--skip-kaggle", action="store_true", help="Skip Kaggle download attempt")
    parser.add_argument("--curated-only", action="store_true", help="Nifty50/FII/events curated ingest only")
    parser.add_argument("--full-audit", action="store_true", help="Inventory all external sources and report gaps")
    parser.add_argument("--ingest-gaps", action="store_true", help="Full audit + ingest missing relevant files")
    args = parser.parse_args()

    from trade_integrations.env import load_trade_env

    load_trade_env()

    if args.slow_fetch:
        os.environ.setdefault("TRADE_FETCH_DELAY_SEC", "2")
        os.environ.setdefault("TRADE_FETCH_MAX_RETRIES", "6")

    if args.full_audit or args.ingest_gaps:
        from trade_integrations.dataflows.external_financial_datasets import ingest_audit_gaps, run_full_source_audit

        if args.ingest_gaps:
            result = ingest_audit_gaps(force_fetch=args.force_fetch)
        else:
            result = run_full_source_audit()
        print(json.dumps(result, indent=2, default=str))
        return 0

    run_github = not args.external_only and not args.curated_only
    run_external = not args.github_only and not args.curated_only

    if args.verify_only:
        from trade_integrations.dataflows.github_datasets import verify_github_macro_merge
        from trade_integrations.dataflows.external_financial_datasets import (
            verify_curated_market_data,
            verify_external_financial_datasets,
        )

        result = {
            "github": verify_github_macro_merge(),
            "external": verify_external_financial_datasets(),
            "curated": verify_curated_market_data(),
        }
        print(json.dumps(result, indent=2, default=str))
        return 0

    result: dict[str, object] = {}

    if args.curated_only:
        from trade_integrations.dataflows.external_financial_datasets import ingest_curated_market_data

        result["curated"] = ingest_curated_market_data(
            force_fetch=args.force_fetch,
            include_kaggle=not args.skip_kaggle,
        )
        print(json.dumps(result, indent=2, default=str))
        return 0

    if run_github:
        from trade_integrations.dataflows.github_datasets import ingest_github_macro_datasets

        result["github"] = ingest_github_macro_datasets(
            force_fetch=args.force_fetch,
            merge_macro_daily=not args.cold_tier_only,
        )

    if run_external:
        from trade_integrations.dataflows.external_financial_datasets import ingest_external_financial_datasets

        result["external"] = ingest_external_financial_datasets(
            force_fetch=args.force_fetch,
            include_huggingface=not args.skip_hf,
            include_kaggle=not args.skip_kaggle,
            skip_curated=args.skip_curated,
        )

    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
