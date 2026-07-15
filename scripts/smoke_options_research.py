#!/usr/bin/env python3
"""Smoke test for options research pipeline (OpenAlgo optional)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tradingagents"))

import trade_integrations  # noqa: F401

from trade_integrations.context.hub import save_options_research
from trade_integrations.dataflows.options_research.aggregator import run_options_research


def _check_doc(label: str, doc) -> bool:
    ok = True
    print(f"\n=== {label} ===")
    print(f"underlying={doc.underlying} expiry={doc.expiry} spot={doc.spot}")
    print(f"stages={[s.stage + ':' + s.status for s in doc.stages]}")
    print(f"ranked={len(doc.ranked_strategies)} recommended={doc.recommended.get('name')}")
    if not doc.chain_snapshot:
        print("WARN: empty chain_snapshot (OpenAlgo/nselib may be offline)")
    if not doc.ranked_strategies:
        print("WARN: no ranked strategies")
        ok = False
    return ok


def main() -> int:
    symbols = sys.argv[1:] or ["NIFTY", "RELIANCE"]
    all_ok = True
    for sym in symbols:
        doc = run_options_research(sym)
        path = save_options_research(doc)
        print(f"Saved: {path}")
        if not _check_doc(sym, doc):
            all_ok = False
        if doc.recommended:
            print(json.dumps({"recommended": doc.recommended.get("name"), "legs": len(doc.recommended.get("legs") or [])}))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
